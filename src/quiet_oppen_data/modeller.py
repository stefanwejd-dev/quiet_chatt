"""Datamodeller — Faktapost, Faktaregister, Fragplan, Sokresultat.

Se ARKITEKTUR.md §3.4.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Iterator


# ---------------------------------------------------------------------------
# Faktapost — den bärande datatypen
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Faktapost:
    """Den bärande datatypen. Allt som får nämnas i ett svar finns som en Faktapost.

    Invarianter (upprätthålls av Faktaregister.registrera):
        * lank_manniska och lank_maskin måste vara satta och icke-tomma.
        * varde lagras alltid som sträng — ingen omtolkning sker.
        * id tilldelas av Faktaregister och är stabilt för sessionens livstid.
    """

    id: str                          # "F1", "F2" … unikt per session
    etikett: str                     # "Växelkurs SEK/EUR"
    varde: str                       # "10.9965"  (alltid sträng)
    kalla_id: str                    # → kallregister
    myndighet: str
    licens: str
    hamtad: datetime
    lank_manniska: str               # klickbar sida hos myndigheten
    lank_maskin: str                 # det exakta API-anrop som gjordes (bevis)
    enhet: str | None = None         # "SEK per EUR"
    period: str | None = None        # "2026-08-12"
    dimensioner: dict = field(default_factory=dict)   # {"region": "Malmö"}
    dataset: str | None = None       # tabell-id / dataset-uuid
    attribution: str | None = None
    harledd: bool = False            # True om beräknad ur andra Faktaposter
    harledd_av: tuple[str, ...] = () # id:n på ingångsposterna


# ---------------------------------------------------------------------------
# Faktautkast — vad en adapter returnerar
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Faktautkast:
    """En hämtad uppgift som ännu inte fått något F-id.

    Adaptrar returnerar Faktautkast, inte Faktapost. Skälet är att Faktaregister
    ska vara den enda vägen in för fakta (ARKITEKTUR.md §3.4): bara registret får
    mynta ett F-id, och bara registret kontrollerar att båda länkarna finns.

    Tidigare returnerade adaptrarna Faktapost med id="" och fyllde i id senare.
    Det gjorde att en post kunde existera utan länkar — vilket faktiskt inträffade
    i pxweb-adapterns felgren — eftersom kontrollen bara låg i registrera().
    Med Faktautkast är den vägen stängd: en Faktapost kan inte konstrueras utan
    att passera valideringen.
    """

    etikett: str
    varde: str
    kalla_id: str
    myndighet: str
    licens: str
    lank_manniska: str
    lank_maskin: str
    enhet: str | None = None
    period: str | None = None
    dimensioner: dict = field(default_factory=dict)
    dataset: str | None = None
    attribution: str | None = None
    hamtad: datetime | None = None
    harledd: bool = False
    harledd_av: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Faktaregister — per-session-behållare
# ---------------------------------------------------------------------------

class Faktaregister:
    """Per session. Delar ut F-id och är enda vägen in för fakta.

    Invarianter:
        * Registrera avvisar en post utan lank_manniska eller lank_maskin.
        * Id:n är stabila och stigande: F1, F2, F3 …
        * serialisera_for_syntes() exponerar aldrig råa API-svar eller maskinlänkar —
          bara de strukturerade fältvärdena som syntesfasen behöver.
    """

    def __init__(self) -> None:
        self._poster: dict[str, Faktapost] = {}
        self._nasta_nr: int = 1

    # ------------------------------------------------------------------
    # Skriva
    # ------------------------------------------------------------------

    def registrera(self, **falt) -> Faktapost:
        """Skapar och registrerar en Faktapost. Tilldelar nästa F-id.

        Args:
            **falt: Alla fält för Faktapost utom 'id' (tilldelas automatiskt).
                    'hamtad' sätts till UTC-nu om det utelämnas.

        Returns:
            Den skapade och registrerade Faktaposten.

        Raises:
            ValueError: om 'lank_manniska' eller 'lank_maskin' saknas eller är tomma.
        """
        if not falt.get("lank_manniska"):
            raise ValueError(
                "Faktapost måste ha 'lank_manniska' satt — "
                "det är den klickbara källsidan som visas för användaren."
            )
        if not falt.get("lank_maskin"):
            raise ValueError(
                "Faktapost måste ha 'lank_maskin' satt — "
                "det är det exakta API-anropet som är beviset för uppgiften."
            )

        if "hamtad" not in falt:
            falt["hamtad"] = datetime.now(timezone.utc)

        fid = f"F{self._nasta_nr}"
        self._nasta_nr += 1

        post = Faktapost(id=fid, **falt)
        self._poster[fid] = post
        return post

    def registrera_utkast(self, utkast: Faktautkast) -> Faktapost:
        """Registrerar ett Faktautkast från en adapter och tilldelar F-id.

        Detta är den väg adapterresultat ska ta in i registret. Valideringen i
        registrera() gäller oförändrat — ett utkast utan länkar avvisas.
        """
        falt = {
            f.name: getattr(utkast, f.name)
            for f in dataclasses.fields(utkast)
            if not (f.name == "hamtad" and getattr(utkast, "hamtad") is None)
        }
        return self.registrera(**falt)

    def registrera_alla(self, utkast: Iterable[Faktautkast]) -> list[Faktapost]:
        """Registrerar en sekvens utkast i ordning. Returnerar de skapade posterna."""
        return [self.registrera_utkast(u) for u in utkast]

    # ------------------------------------------------------------------
    # Läsa
    # ------------------------------------------------------------------

    def hamta(self, id: str) -> Faktapost | None:
        """Hämtar en Faktapost på F-id. Returnerar None om den inte finns."""
        return self._poster.get(id)

    def alla(self) -> list[Faktapost]:
        """Returnerar alla registrerade Faktaposter i registreringsordning."""
        return list(self._poster.values())

    def __len__(self) -> int:
        return len(self._poster)

    def __iter__(self) -> Iterator[Faktapost]:
        return iter(self._poster.values())

    def ar_tom(self) -> bool:
        return len(self._poster) == 0

    # ------------------------------------------------------------------
    # Syntes
    # ------------------------------------------------------------------

    def serialisera_for_syntes(self) -> str:
        """Returnerar en kompakt textrepresentation för fas B (syntesmodellen).

        Innehåller per post: F-id, etikett, värde, enhet, period, myndighet,
        dimensioner och (om tillämpligt) härledningsinformation.

        Utelämnar avsiktligt: lank_manniska, lank_maskin, hamtad, dataset,
        kalla_id, licens och attribution. Syntesmodellen behöver inte råa
        API-svar eller tekniska metadata — bara de faktauppgifter den ska
        formulera text kring.

        Returns:
            Flerradssträng, en rad per post. Tom sträng om registret är tomt.
        """
        if not self._poster:
            return ""

        rader: list[str] = []
        for post in self._poster.values():
            varde_del = post.varde
            if post.enhet:
                varde_del = f"{post.varde} {post.enhet}"

            delar: list[str] = [post.id, post.etikett, varde_del]

            if post.period:
                delar.append(f"period={post.period}")

            delar.append(f"källa={post.myndighet}")

            if post.dimensioner:
                dim = ", ".join(f"{k}={v}" for k, v in post.dimensioner.items())
                delar.append(f"[{dim}]")

            if post.harledd and post.harledd_av:
                ingångar = "+".join(post.harledd_av)
                delar.append(f"(härlett ur {ingångar})")

            rader.append(" | ".join(delar))

        return "\n".join(rader)


# ---------------------------------------------------------------------------
# Fragplan och Sokresultat
# ---------------------------------------------------------------------------

@dataclass
class Fragplan:
    """Adapterspecifik frågeplan — fylls i av planeraren i fas A."""

    fraga: str
    extra: dict = field(default_factory=dict)


@dataclass
class Sokresultat:
    """Ett sökresultat från katalogindexet (steg 2–3)."""

    datamangd_id: str
    titel: str
    beskrivning: str
    utgivare: str
    relevans: float
    licens: str | None = None
    format: str | None = None
    access_url: str | None = None
    adapter_hint: str | None = None  # vilken adapter som troligen kan exekvera
