"""EU-register — läser eu/euregister.yaml till typade objekt.

Samma roll för EU-rättsakterna som lagregister.py har för SFS. Listan över
vilka rättsakter som ingår, och deras CELEX-nummer, finns i YAML och aldrig i
Python-kod.

Invarianter:
    * Kastar ValueError om en post saknar 'celex'.
    * Länkarna härleds ur CELEX-numret — de skrivs inte in per post, eftersom
      två sanningar om samma adress är en för många.
    * `luckor` läses ut men hämtas aldrig. En lucka som blir hämtbar ska
      flyttas till `rattsakter` med ett verifierat anrop, inte tyst börja
      fungera.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# Se motsvarande kommentar i register.py — samma QUIET_OPPEN_DATA_ROOT-mekanism.
_PROJEKT_ROT = Path(os.environ.get("QUIET_OPPEN_DATA_ROOT") or Path(__file__).parent.parent.parent)
_EUREGISTER_FIL = _PROJEKT_ROT / "eu" / "euregister.yaml"

# Resursvägen och människolänken. De står här och inte i kallregister.yaml
# därför att de är mallar med en plats för CELEX — bas_url i registret är den
# adress som verifierats, den här är hur den används.
_MASKINLANK = "http://publications.europa.eu/resource/celex/{celex}"
_MANNISKOLANK = "https://eur-lex.europa.eu/legal-content/SV/TXT/?uri=CELEX:{celex}"


@dataclass(frozen=True)
class Rattsakt:
    """En EU-rättsakt i registret."""

    celex: str
    namn: str
    kortnamn: str
    beskrivning: str = ""
    verifierat: str = ""

    @property
    def lank_maskin(self) -> str:
        return _MASKINLANK.format(celex=self.celex)

    @property
    def lank_manniska(self) -> str:
        return _MANNISKOLANK.format(celex=self.celex)


@dataclass(frozen=True)
class Lucka:
    """Något som medvetet inte hämtas, med sitt skäl."""

    identitet: str
    skal: str
    atgard: str = ""


def las(registersokväg: Path | str | None = None) -> list[Rattsakt]:
    """Läser euregister.yaml och returnerar rättsakterna som ska hämtas."""
    sökväg = Path(registersokväg) if registersokväg else _EUREGISTER_FIL

    with open(sökväg, encoding="utf-8") as f:
        d: dict[str, Any] = yaml.safe_load(f) or {}

    resultat: list[Rattsakt] = []
    for i, post in enumerate(d.get("rattsakter") or []):
        if not isinstance(post, dict):
            raise ValueError(f"Post {i} är inte ett YAML-objekt: {post!r}")
        if "celex" not in post:
            raise ValueError(f"Post {i} saknar obligatoriskt fält 'celex': {post!r}")
        if "namn" not in post:
            raise ValueError(f"Post {i} saknar obligatoriskt fält 'namn': {post!r}")
        resultat.append(
            Rattsakt(
                celex=str(post["celex"]).strip(),
                namn=" ".join(str(post["namn"]).split()),
                kortnamn=str(post.get("kortnamn", "")).strip(),
                beskrivning=" ".join(str(post.get("beskrivning", "")).split()),
                verifierat=str(post.get("verifierat", "")).strip(),
            )
        )
    return resultat


def luckor(registersokväg: Path | str | None = None) -> list[Lucka]:
    """De poster som medvetet inte hämtas."""
    sökväg = Path(registersokväg) if registersokväg else _EUREGISTER_FIL
    with open(sökväg, encoding="utf-8") as f:
        d: dict[str, Any] = yaml.safe_load(f) or {}
    return [
        Lucka(
            identitet=str(x.get("identitet", "")),
            skal=" ".join(str(x.get("skal", "")).split()),
            atgard=" ".join(str(x.get("atgard", "")).split()),
        )
        for x in (d.get("luckor") or [])
    ]


def hamta(celex_eller_kortnamn: str, registersokväg: Path | str | None = None) -> Rattsakt | None:
    """Hämtar en rättsakt på CELEX-nummer eller kortnamn."""
    sökterm = celex_eller_kortnamn.strip().lower()
    for post in las(registersokväg):
        if post.celex.lower() == sökterm or post.kortnamn.lower() == sökterm:
            return post
    return None
