"""BFN-register — läser kallor/bfnregister.yaml till typade objekt.

Samma roll för Bokföringsnämndens skörd som lagregister.py har för SFS:
listan över vad som ska hämtas, och reglerna för hur det ska kategoriseras,
finns i YAML och aldrig i Python-kod.

Invarianter:
    * Kategorierna prövas i registrets ordning — den FÖRSTA matchande
      prefixen vinner. Ordningen i filen är alltså betydelsebärande, och
      sorteras aldrig om här.
    * `indexera_nivaer` är den enda platsen som avgör vad som får hamna i
      sökindexet. Ingen kod någon annanstans får utvidga den mängden.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Se motsvarande kommentar i register.py — samma QUIET_OPPEN_DATA_ROOT-
# mekanism, av samma skäl.
_PROJEKT_ROT = Path(os.environ.get("QUIET_OPPEN_DATA_ROOT") or Path(__file__).parent.parent.parent)
_BFNREGISTER_FIL = _PROJEKT_ROT / "kallor" / "bfnregister.yaml"


@dataclass(frozen=True)
class Kategori:
    """En kategoriregel: sidsökväg -> kategori, typ och nivå."""

    prefix: str
    kategori: str
    typ: str
    niva: str
    rang: int
    beskrivning: str = ""
    # Sidor vars värde ligger i sidtexten, inte i bilagor (frågor och svar).
    skorda_sidtext: bool = False
    # False = länken registreras men filen laddas inte ner.
    hamta: bool = True


@dataclass(frozen=True)
class Filmonster:
    """Filnamnsheuristik för bilagor som ingen sida länkar till."""

    monster: str
    kategori: str
    typ: str
    niva: str


@dataclass(frozen=True)
class Nyckeldokument:
    """Ett dokument som en körning MÅSTE få hem för att räknas som fullständig."""

    id: str
    namn: str
    krav: str
    bfnar: str = ""
    fil: str = ""
    monster: str = ""


@dataclass(frozen=True)
class BfnRegister:
    """Hela registret."""

    kategorier: tuple[Kategori, ...]
    filmonster: tuple[Filmonster, ...]
    nyckeldokument: tuple[Nyckeldokument, ...]
    filtyper: tuple[str, ...] = (".pdf",)
    max_filstorlek_mb: int = 60
    ta_med_foraldralosa_bilagor: bool = True
    ladda_ner_nivaer: frozenset[str] = field(default_factory=lambda: frozenset({"karna", "stod"}))
    indexera_nivaer: frozenset[str] = field(default_factory=lambda: frozenset({"karna", "stod"}))

    def kategorisera_sidvag(self, sokvag: str) -> Kategori:
        """Första matchande prefix vinner. Fångstnätet (prefix "") matchar allt."""
        for k in self.kategorier:
            if not k.prefix or sokvag.startswith(k.prefix):
                return k
        # Registret har alltid ett fångstnät, men om någon tar bort det ska
        # felet sägas rakt ut och inte tystas med en påhittad kategori.
        raise ValueError(
            f"Ingen kategori matchade sökvägen {sokvag!r} och registret saknar "
            f"fångstnät (en post med prefix: \"\"). Lägg tillbaka det."
        )

    def kategorisera_filnamn(self, filnamn: str) -> tuple[str, str, str]:
        """(kategori, typ, niva) ur filnamnsheuristiken.

        Används BARA för bilagor som ingen sida länkar till. Den skriver
        aldrig över en kategori som en sidsökväg redan har gett — sidans
        placering är BFN:s egen indelning, filnamnet bara vår gissning.
        """
        for m in self.filmonster:
            if re.search(m.monster, filnamn, re.IGNORECASE):
                return m.kategori, m.typ, m.niva
        sista = self.kategorier[-1]
        return sista.kategori, sista.typ, sista.niva

    def far_indexeras(self, niva: str) -> bool:
        return niva in self.indexera_nivaer

    def far_laddas_ner(self, niva: str) -> bool:
        return niva in self.ladda_ner_nivaer


def las(registersokväg: Path | str | None = None) -> BfnRegister:
    """Läser bfnregister.yaml och returnerar ett fryst BfnRegister.

    Raises:
        ValueError: om en kategoripost saknar obligatoriska fält.
        FileNotFoundError: om filen saknas.
    """
    sökväg = Path(registersokväg) if registersokväg else _BFNREGISTER_FIL

    with open(sökväg, encoding="utf-8") as f:
        d: dict[str, Any] = yaml.safe_load(f) or {}

    kategorier: list[Kategori] = []
    for i, post in enumerate(d.get("kategorier") or []):
        if not isinstance(post, dict):
            raise ValueError(f"Kategori {i} är inte ett YAML-objekt: {post!r}")
        for falt in ("kategori", "typ", "niva", "rang"):
            if falt not in post:
                raise ValueError(f"Kategori {i} saknar obligatoriskt fält {falt!r}: {post!r}")
        kategorier.append(
            Kategori(
                prefix=str(post.get("prefix", "")),
                kategori=str(post["kategori"]),
                typ=str(post["typ"]),
                niva=str(post["niva"]),
                rang=int(post["rang"]),
                beskrivning=str(post.get("beskrivning", "")).strip(),
                skorda_sidtext=bool(post.get("skorda_sidtext", False)),
                hamta=bool(post.get("hamta", True)),
            )
        )

    filmonster = tuple(
        Filmonster(
            monster=str(m["monster"]),
            kategori=str(m["kategori"]),
            typ=str(m["typ"]),
            niva=str(m["niva"]),
        )
        for m in (d.get("filmonster") or [])
    )

    nyckeldokument = tuple(
        Nyckeldokument(
            id=str(n["id"]),
            namn=str(n.get("namn", "")),
            krav=str(n.get("krav", "")).strip(),
            bfnar=str(n.get("bfnar", "")),
            fil=str(n.get("fil", "")),
            monster=str(n.get("monster", "")),
        )
        for n in (d.get("nyckeldokument") or [])
    )

    skord = d.get("skord") or {}
    return BfnRegister(
        kategorier=tuple(kategorier),
        filmonster=filmonster,
        nyckeldokument=nyckeldokument,
        filtyper=tuple(skord.get("filtyper") or [".pdf"]),
        max_filstorlek_mb=int(skord.get("max_filstorlek_mb", 60)),
        ta_med_foraldralosa_bilagor=bool(skord.get("ta_med_foraldralosa_bilagor", True)),
        ladda_ner_nivaer=frozenset(skord.get("ladda_ner_nivaer") or ["karna", "stod"]),
        indexera_nivaer=frozenset(skord.get("indexera_nivaer") or ["karna", "stod"]),
    )
