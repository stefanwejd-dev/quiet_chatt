"""Adapter för EU-rättsakter ur EUR-Lex (steg 24).

Läser ur det lokala korpusindexet, inte från EUR-Lex vid frågetillfället —
samma val som LagtextAdapter och BfnAdapter. Rättsakterna är stora (momsdirektivet
är 1,8 MB XHTML) och konsolideringsvalet kräver ett extra anrop; det hör hemma
i en nattlig körning, inte i en fråga.

Invarianter:
    * Faktautkast.period = konsolideringsdatumet, eller "ursprunglig lydelse"
      när rättsakten aldrig konsoliderats. Aldrig tomt och aldrig gissat.
    * Faktautkast.dataset = CELEX-numret.
    * lank_maskin = den resurs som faktiskt hämtades, alltså den konsoliderade
      lydelsen — inte bas-CELEX. Ett bevis som pekar på en annan lydelse än
      den citerade vore värdelöst.
    * dimensioner["blocktyp"] skiljer en bindande artikel från direktivets
      skäl, som är tolkningsdata.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from quiet_oppen_data.index.sok import sok_korpus
from quiet_oppen_data.modeller import Faktautkast, Fragplan
from quiet_oppen_data.register import Kalla, hamta

logger = logging.getLogger(__name__)

KORPUS = "eu"

_BLOCKTYPER = ("artikel", "skal", "bilaga")


class EurlexAdapter:
    """Adapter för lokalt indexerade EU-rättsakter."""

    def __init__(self, kalla_id: str = "eurlex") -> None:
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
                "Söker i EU-rättsakter i gällande konsoliderad lydelse på svenska: "
                "momsdirektivet (2006/112/EG) och dess genomförandeförordning, "
                "redovisningsdirektivet (2013/34/EU), e-fakturadirektivet, "
                "moder/dotterbolags-, fusions-, ränte/royalty- och skatteflyktsdirektiven, "
                "samt GDPR. "
                "Använd den när en fråga gäller vad EU-rätten kräver — särskilt vid "
                "gränsöverskridande handel, eller när den svenska lagens lydelse är "
                "oklar och direktivet den genomför ger svaret. Sök alltid svensk "
                "lagtext också: det är den svenska lagen som tillämpas, direktivet "
                "förklarar vad den ska uppnå. "
                "En 'artikel' är bindande; ett 'skal' är direktivets motivering och "
                "används för tolkning, inte som självständig regel."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "sok": {
                        "type": "string",
                        "description": (
                            "Sökfråga (t.ex. 'avdragsrätt för ingående mervärdesskatt', "
                            "'definition av mindre företag', 'plats för tillhandahållande "
                            "av tjänster')."
                        ),
                    },
                    "rattsakt": {
                        "type": "string",
                        "description": (
                            "Valfri filtrering: CELEX-nummer ('32006L0112') eller kortnamn "
                            "('Momsdirektivet', 'Redovisningsdirektivet', 'GDPR')."
                        ),
                    },
                    "artikel": {
                        "type": "string",
                        "description": "Valfri exakt artikel, skrivs som 'Artikel 168'.",
                    },
                    "blocktyp": {
                        "type": "string",
                        "enum": list(_BLOCKTYPER),
                        "description": (
                            "Valfri filtrering. 'artikel' = bindande bestämmelse. "
                            "'skal' = direktivets motivering (tolkningsdata). "
                            "'bilaga' = bilagor, t.ex. uppställningsformer."
                        ),
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
        artikel = plan.extra.get("artikel")
        if not sok_term and not artikel:
            logger.info("%s: anrop utan sökterm", self.id)
            return []

        blocktyp = plan.extra.get("blocktyp")
        if blocktyp and blocktyp not in _BLOCKTYPER:
            logger.info("%s: okänd blocktyp %r — filtret ignoreras", self.id, blocktyp)
            blocktyp = None

        limit = min(int(plan.extra.get("limit") or 5), 10)
        rattsakt = plan.extra.get("rattsakt")

        try:
            träffar = sok_korpus(
                fraga=sok_term or "",
                korpus=KORPUS,
                max_antal=limit,
                dokument_filter=str(rattsakt) if rattsakt else None,
                blocktyp_filter=blocktyp,
                beteckning_filter=str(artikel) if artikel else None,
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

            namn = t.kortnamn or t.titel
            etikett = f"{namn} {t.beteckning}".strip() if t.beteckning else namn
            if t.blocktyp == "skal":
                etikett += " (skäl, ej bindande bestämmelse)"

            delar = []
            if t.kapitel_rubrik:
                plats = f"{t.kapitel_nr}: " if t.kapitel_nr else ""
                delar.append(f"{plats}{t.kapitel_rubrik}")
            if t.avsnitt:
                delar.append(t.avsnitt)
            delar.append(t.text)
            varde = "\n\n".join(delar)

            dimensioner: dict[str, Any] = {
                "blocktyp": t.blocktyp,
                "rattsakt": t.titel,
                "celex": t.dok_id,
            }
            if t.kortnamn:
                dimensioner["kortnamn"] = t.kortnamn
            if t.beteckning:
                dimensioner["artikel"] = t.beteckning
            if t.kapitel_nr:
                dimensioner["indelning"] = t.kapitel_nr

            utkast.append(Faktautkast(
                etikett=etikett,
                varde=varde,
                kalla_id=self.id,
                myndighet=t.utgivare or self._kalla.myndighet or "EUR-Lex",
                licens=t.licens or self._kalla.licens,
                attribution=t.attribution or self._kalla.attribution,
                dataset=t.dok_id,
                period=t.lydelse or None,
                hamtad=hamtad_dt,
                lank_manniska=t.lank_manniska,
                lank_maskin=t.lank_maskin,
                dimensioner=dimensioner,
            ))

        return utkast
