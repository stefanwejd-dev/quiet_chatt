"""Adapter för Bokföringsnämndens allmänna råd och vägledningar (steg 23).

Läser ur det lokala korpusindexet i stället för att anropa bfn.se vid
frågetillfället — samma val som LagtextAdapter, och av samma skäl: BFN
publicerar pdf:er, inte ett API, och en pdf går inte att hämta och parsa inne
i en fråga.

Invarianter:
    * Faktautkast.period = dokumentets egen versionsuppgift ("Uppdaterad
      2025-12-15"), eller tom när titelsidan inte bär någon. Den gissas aldrig.
    * Faktautkast.dataset = BFNAR-numret när det finns, annars dokument-id.
    * hamtad = när kopian togs, inte när frågan ställdes.
    * dimensioner["blocktyp"] skiljer ett BINDANDE allmänt råd från BFN:s
      kommentar till det. Det är adapterns viktigaste fält: en kommentar som
      citeras som om den vore rådet är fel svar med rätt källänk.

Licensen är BFN:s PSI-villkor: fritt vidareutnyttjande utan avtal, mot
källangivelse "BFN och datum". Datumet ligger i attributionen per dokument.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from quiet_oppen_data.index.sok import sok_korpus
from quiet_oppen_data.modeller import Faktautkast, Fragplan
from quiet_oppen_data.register import Kalla, hamta

logger = logging.getLogger(__name__)

KORPUS = "bfn"

# Blocktyperna som får efterfrågas. Listan står här och inte i schemat därför
# att den är verktygets kontrakt mot modellen — schemat beskriver lagring.
_BLOCKTYPER = ("allmant_rad", "kommentar", "lagtext", "exempel", "brodtext")


class BfnAdapter:
    """Adapter för lokalt indexerade BFN-dokument."""

    def __init__(self, kalla_id: str = "bokforingsnamnden") -> None:
        k = hamta(kalla_id)
        if not isinstance(k, Kalla):
            raise RuntimeError(f"Källan {kalla_id} saknas eller är blockerad i källregistret.")
        self._kalla = k

    @property
    def id(self) -> str:
        return self._kalla.id

    def beskriv(self) -> list[dict[str, Any]]:
        return [{
            "name": self.id,
            "description": (
                "Söker i Bokföringsnämndens allmänna råd och vägledningar (K1, K2, K3, "
                "Årsbokslut, vägledningen Bokföring m.fl.) — god redovisningssed. "
                "Använd den för frågor om HUR något ska bokföras eller redovisas när "
                "lagtexten inte räcker: värdering, avskrivning, periodisering, "
                "verifikationens innehåll, årsredovisningens uppställning. "
                "Sök lagtext (verktyget 'lagtext') för vad LAGEN kräver; BFN säger hur "
                "kravet uppfylls i praktiken. "
                "VIKTIGT: ett 'allmänt råd' är bindande normgivning, en 'kommentar' är "
                "BFN:s förklaring till rådet och är det inte. Fältet blocktyp visar "
                "vilket som är vilket — blanda dem inte i ett svar."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "sok": {
                        "type": "string",
                        "description": (
                            "Sökfråga (t.ex. 'avskrivning av byggnad', 'vad ska en "
                            "verifikation innehålla', 'periodisering av intäkter', "
                            "'gränsvärden mindre företag')."
                        ),
                    },
                    "dokument": {
                        "type": "string",
                        "description": (
                            "Valfri filtrering på ett dokument: BFNAR-nummer "
                            "('BFNAR 2012:1' för K3, 'BFNAR 2016:10' för K2, "
                            "'BFNAR 2013:2' för Bokföring) eller dokument-id."
                        ),
                    },
                    "blocktyp": {
                        "type": "string",
                        "enum": list(_BLOCKTYPER),
                        "description": (
                            "Valfri filtrering på typ av text. 'allmant_rad' = bindande "
                            "normgivning. 'kommentar' = BFN:s förklaring, inte bindande. "
                            "'lagtext' = det lagrum rådet knyter an till. 'exempel' = "
                            "räkneexempel."
                        ),
                    },
                    "punkt": {
                        "type": "string",
                        "description": "Valfri exakt punkt i ett allmänt råd, t.ex. '12.7'.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max antal stycken att hämta (1-10). Standard: 5.",
                        "minimum": 1,
                        "maximum": 10,
                    },
                },
                "required": ["sok"],
            },
        }]

    def hamta(self, plan: Fragplan) -> list[Faktautkast]:
        sok_term = plan.extra.get("sok") or plan.fraga
        punkt = plan.extra.get("punkt")
        if not sok_term and not punkt:
            logger.info("%s: anrop utan sökterm", self.id)
            return []

        blocktyp = plan.extra.get("blocktyp")
        if blocktyp and blocktyp not in _BLOCKTYPER:
            # En okänd blocktyp filtrerar bort allt och skulle se ut som
            # "BFN har inget att säga". Hellre ignorera filtret och logga.
            logger.info("%s: okänd blocktyp %r — filtret ignoreras", self.id, blocktyp)
            blocktyp = None

        limit = min(int(plan.extra.get("limit") or 5), 10)

        try:
            träffar = sok_korpus(
                fraga=sok_term or "",
                korpus=KORPUS,
                max_antal=limit,
                dokument_filter=str(plan.extra["dokument"]) if plan.extra.get("dokument") else None,
                blocktyp_filter=blocktyp,
                beteckning_filter=str(punkt) if punkt else None,
            )
        except Exception:
            logger.warning("%s: sökning misslyckades (sok=%r)", self.id, sok_term, exc_info=True)
            return []

        utkast: list[Faktautkast] = []
        for t in träffar:
            try:
                hamtad_dt = datetime.fromisoformat(t.hamtad)
            except (ValueError, TypeError):
                hamtad_dt = datetime.now(UTC)

            # Etiketten ska gå att läsa som en källhänvisning i sig.
            namn = t.kortnamn or t.titel
            if t.beteckning:
                etikett = f"{namn} punkt {t.beteckning}"
            elif t.kapitel_nr:
                etikett = f"{namn} kapitel {t.kapitel_nr}"
            else:
                etikett = namn
            if t.blocktyp == "kommentar":
                etikett += " (BFN:s kommentar, ej bindande)"
            elif t.blocktyp == "lagtext":
                etikett += " (lagtext återgiven av BFN)"
            elif t.blocktyp == "exempel":
                etikett += " (exempel)"

            delar = []
            if t.kapitel_rubrik:
                kap = f"Kapitel {t.kapitel_nr} " if t.kapitel_nr else ""
                delar.append(f"{kap}{t.kapitel_rubrik}".strip())
            if t.avsnitt:
                delar.append(t.avsnitt)
            delar.append(t.text)
            varde = "\n\n".join(delar)

            dimensioner: dict[str, Any] = {
                "blocktyp": t.blocktyp,
                "dokument": t.titel,
            }
            if t.kortnamn:
                dimensioner["bfnar"] = t.kortnamn
            if t.kapitel_nr:
                dimensioner["kapitel"] = t.kapitel_nr
            if t.beteckning:
                dimensioner["punkt"] = t.beteckning
            if t.sida:
                # Sidan är den plats en människa kan slå upp i pdf:en. Den är
                # också den enda platsmarkör som finns när textutvinningen
                # inte bevarat punktnumret.
                dimensioner["sida"] = str(t.sida)

            utkast.append(Faktautkast(
                etikett=etikett,
                varde=varde,
                kalla_id=self.id,
                myndighet=t.utgivare or self._kalla.myndighet or "Bokföringsnämnden (BFN)",
                licens=t.licens or self._kalla.licens,
                attribution=t.attribution or self._kalla.attribution,
                dataset=t.kortnamn or t.dok_id,
                period=t.lydelse or None,
                hamtad=hamtad_dt,
                lank_manniska=t.lank_manniska,
                lank_maskin=t.lank_maskin,
                dimensioner=dimensioner,
            ))

        return utkast
