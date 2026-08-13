"""Fas C — validator (fail-closed). Se ARKITEKTUR.md §4 ("Fas C — Validering").

Den sista instansen innan ett svar lämnar backend. Fyra kontroller, alla
oberoende av vad modellen "sa" om sig själv:

  1. Varje `kallor`-id i varje stycke måste finnas i sessionens Faktaregister.
  2. Varje stycke som innehåller en siffra, ett datum eller ett egennamn ska
     ha minst en källa. (JSON-schemat i fas B garanterar redan detta för
     schema-giltig utdata — kontrollen fångar schema-drift, inte det
     normala fallet.)
  3. Varje citerad Faktapost måste ha `lank_manniska` satt.
  4. Varje citerad Faktapost med `licens == "CC-BY"` måste ha `attribution`
     satt — annars kan svarsobjektet inte bära den attribution licensen
     kräver.

Vid fel: ett omförsök av fas B med validatorns felmeddelande inlagt. Vid
andra felet: fail-closed. Aldrig ett obelagt svar.

Varje valideringsfel loggas med orsak — enligt PLAN.md är det den viktigaste
kvalitetssignalen systemet har: den talar om vilken adapter, vilket schema
eller vilken prompt som brister.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from typing import Protocol

from quiet_oppen_data.modeller import Faktaregister
from quiet_oppen_data.motor.syntes import INGET_HITTAT, SyntesSvar

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Valideringsfel
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Valideringsfel:
    """Ett enskilt fel från en av de fyra kontrollerna."""
    kontroll: str
    meddelande: str


def _logga_fel(fel: list[Valideringsfel], forsok: int) -> None:
    for f in fel:
        logger.warning("Fas C-valideringsfel (försök %d) [%s]: %s", forsok, f.kontroll, f.meddelande)


# ---------------------------------------------------------------------------
# De fyra kontrollerna
# ---------------------------------------------------------------------------

def _kontrollera_kallor_finns(svar: SyntesSvar, register: Faktaregister) -> list[Valideringsfel]:
    """Kontroll 1: varje citerat F-id måste finnas i registret."""
    fel: list[Valideringsfel] = []
    for stycke in svar.stycken:
        for fid in stycke.kallor:
            if register.hamta(fid) is None:
                fel.append(Valideringsfel(
                    kontroll="okänd_kalla",
                    meddelande=f"Stycket \"{stycke.text}\" citerar '{fid}', som inte finns i Faktaregistret.",
                ))
    return fel


# Grov proper noun/siffer-heuristik — bara en backstop mot schema-drift, inte
# en fullständig svensk NLP-analys.
_SIFFRA = re.compile(r"\d")


def _har_troligen_sakuppgift(text: str) -> bool:
    if _SIFFRA.search(text):
        return True
    ord_ = text.split()
    for w in ord_[1:]:  # hoppa över första ordet — versal där är bara meningsstart
        kärna = w.strip(".,;:!?()\"'")
        if kärna and kärna[0].isupper() and not kärna.isupper():
            return True
    return False


def _kontrollera_stycken_har_tackning(svar: SyntesSvar) -> list[Valideringsfel]:
    """Kontroll 2: stycken med sakuppgift men utan källa. Fångar schema-drift —
    JSON-schemat i fas B kräver redan minItems: 1 på kallor för giltig utdata."""
    fel: list[Valideringsfel] = []
    for stycke in svar.stycken:
        if not stycke.kallor and _har_troligen_sakuppgift(stycke.text):
            fel.append(Valideringsfel(
                kontroll="ociterat_stycke",
                meddelande=f"Stycket \"{stycke.text}\" innehåller en sakuppgift men har ingen källa.",
            ))
    return fel


def _kontrollera_lank_manniska(svar: SyntesSvar, register: Faktaregister) -> list[Valideringsfel]:
    """Kontroll 3: varje citerad Faktapost måste ha lank_manniska satt."""
    fel: list[Valideringsfel] = []
    for stycke in svar.stycken:
        for fid in stycke.kallor:
            post = register.hamta(fid)
            if post is not None and not post.lank_manniska:
                fel.append(Valideringsfel(
                    kontroll="saknar_lank_manniska",
                    meddelande=f"Faktapost '{fid}' saknar lank_manniska men citeras.",
                ))
    return fel


def _kontrollera_attribution(svar: SyntesSvar, register: Faktaregister) -> list[Valideringsfel]:
    """Kontroll 4: citerad CC-BY-källa måste ha attribution satt på Faktaposten."""
    fel: list[Valideringsfel] = []
    for stycke in svar.stycken:
        for fid in stycke.kallor:
            post = register.hamta(fid)
            if post is not None and post.licens == "CC-BY" and not post.attribution:
                fel.append(Valideringsfel(
                    kontroll="saknar_attribution",
                    meddelande=f"Faktapost '{fid}' har licens CC-BY men saknar attribution.",
                ))
    return fel


def validera(svar: SyntesSvar, register: Faktaregister) -> list[Valideringsfel]:
    """Kör alla fyra kontrollerna. Tom lista = giltigt svar."""
    fel: list[Valideringsfel] = []
    fel.extend(_kontrollera_kallor_finns(svar, register))
    fel.extend(_kontrollera_stycken_har_tackning(svar))
    fel.extend(_kontrollera_lank_manniska(svar, register))
    fel.extend(_kontrollera_attribution(svar, register))
    return fel


def _formatera_felmeddelande(fel: list[Valideringsfel]) -> str:
    rader = [f"- [{f.kontroll}] {f.meddelande}" for f in fel]
    return "\n".join(rader)


def _med_attribution(svar: SyntesSvar, register: Faktaregister) -> SyntesSvar:
    """Fyller i attribution deterministiskt utifrån de citerade Faktaposterna.

    Aldrig modellens jobb att skriva av attributionstext korrekt — validatorn
    hämtar den direkt från Faktaposten. Ordning: första citeringen, dubbletter
    borttagna.
    """
    sedd: set[str] = set()
    attribution: list[str] = []
    for stycke in svar.stycken:
        for fid in stycke.kallor:
            post = register.hamta(fid)
            if post is not None and post.licens == "CC-BY" and post.attribution:
                if post.attribution not in sedd:
                    sedd.add(post.attribution)
                    attribution.append(post.attribution)
    return replace(svar, attribution=tuple(attribution))


def _fail_closed() -> SyntesSvar:
    return SyntesSvar(kan_besvaras=False, stycken=(), forbehall=INGET_HITTAT)


# ---------------------------------------------------------------------------
# Publikt API
# ---------------------------------------------------------------------------

class _SyntetiserareProtokoll(Protocol):
    def syntetisera(
        self, fraga: str, register: Faktaregister, felmeddelande: str | None = None
    ) -> SyntesSvar: ...


class FasCValidator:
    """Fas C: kör fas B, validerar, försöker om en gång, fail-closed vid andra felet.

    Tar en syntetiserare via konstruktorn (duck-typed mot _SyntetiserareProtokoll)
    så att tester kan injicera en attrapp utan API-nyckel. Standard är en riktig
    `FasBSyntes()`.
    """

    def __init__(self, syntes: _SyntetiserareProtokoll | None = None) -> None:
        if syntes is None:
            from quiet_oppen_data.motor.syntes import FasBSyntes
            syntes = FasBSyntes()
        self._syntes = syntes

    def kor(self, fraga: str, register: Faktaregister) -> SyntesSvar:
        """Kör fas B→C-flödet för en fråga och returnerar ett validerat svar."""
        svar = self._syntes.syntetisera(fraga, register)
        fel = validera(svar, register)
        if not fel:
            return _med_attribution(svar, register)

        _logga_fel(fel, forsok=1)

        svar2 = self._syntes.syntetisera(
            fraga, register, felmeddelande=_formatera_felmeddelande(fel)
        )
        fel2 = validera(svar2, register)
        if not fel2:
            return _med_attribution(svar2, register)

        _logga_fel(fel2, forsok=2)
        logger.warning(
            "Fas C: andra försöket underkändes också — fail-closed för frågan %r", fraga
        )
        return _fail_closed()
