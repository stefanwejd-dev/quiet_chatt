"""Adapter för konsoliderad svensk lagtext (Steg 16).

Läser ur det lokala lagindexet (SQLite + FTS5 + embeddings) i stället för att
anropa en extern källa vid frågetillfället.

Invarianter (ARKITEKTUR.md §5 regel 8):
    * Faktautkast.period = konsolideringspunkten, t.ex. "t.o.m. SFS 2026:1393".
    * Faktautkast.dataset = SFS-numret, t.ex. "1999:1229".
    * Faktautkast.lank_manniska = Riksdagens sida för författningen.
    * Faktautkast.lank_maskin = https://data.riksdagen.se/dokument/{dok_id}.
    * hamtad = när kopian togs, inte när frågan ställdes.
"""
from __future__ import annotations

import logging
from datetime import datetime, UTC
from typing import Any

from quiet_oppen_data.index.sok import sok_lag
from quiet_oppen_data.modeller import Faktautkast, Fragplan
from quiet_oppen_data.register import Kalla, hamta

logger = logging.getLogger(__name__)


class LagtextAdapter:
    """Adapter för lokalt indexerad konsoliderad lagtext från Riksdagen."""

    def __init__(self, kalla_id: str = "lagtext") -> None:
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
                "Söker i gällande svensk lagtext (konsoliderad SFS från Riksdagen) "
                "inom skatt, moms, bokföring och redovisning (IL, ML, SFL, BFL, ÅRL m.fl.). "
                "Returnerar relevanta lagparagrafer med kapitel, rubrik och ändringsnotiser."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "sok": {
                        "type": "string",
                        "description": (
                            "Sökfråga i lagtexten (t.ex. 'när är man begränsat skattskyldig', "
                            "'förenklat årsbokslut', 'representationsavdrag', 'momssats livsmedel')."
                        ),
                    },
                    "lag": {
                        "type": "string",
                        "description": "Valfri filtrering på specifik lag (SFS-nummer t.ex. '1999:1229' eller kortnamn 'IL', 'ML', 'BFL', 'ÅRL', 'SFL').",
                    },
                    "kapitel": {
                        "type": "string",
                        "description": "Valfritt kapitelnummer (t.ex. '3', '16').",
                    },
                    "paragraf": {
                        "type": "string",
                        "description": "Valfritt paragrafnummer (t.ex. '9', '17', '3 a').",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max antal paragrafer att hämta (1–10). Standard: 5.",
                        "minimum": 1,
                        "maximum": 10,
                    },
                },
                "required": ["sok"],
            },
        }]

    def hamta(self, plan: Fragplan) -> list[Faktautkast]:
        sok_term = plan.extra.get("sok") or plan.fraga
        if not sok_term and not plan.extra.get("paragraf"):
            logger.info("%s: anrop utan sökterm", self.id)
            return []

        sfs_filter = plan.extra.get("lag") or plan.extra.get("sfs")
        kapitel_filter = plan.extra.get("kapitel")
        paragraf_filter = plan.extra.get("paragraf")
        limit = min(int(plan.extra.get("limit") or 5), 10)

        try:
            träffar = sok_lag(
                fraga=sok_term or "",
                max_antal=limit,
                sfs_filter=str(sfs_filter) if sfs_filter else None,
                kapitel_filter=str(kapitel_filter) if kapitel_filter else None,
                paragraf_filter=str(paragraf_filter) if paragraf_filter else None,
            )
        except Exception:
            logger.warning("%s: sökning misslyckades (sok=%r)", self.id, sok_term, exc_info=True)
            return []

        utkast: list[Faktautkast] = []
        for t in träffar:
            # Tolka hämtningstidpunkt
            try:
                hamtad_dt = datetime.fromisoformat(t.hamtad)
            except Exception:
                hamtad_dt = datetime.now(UTC)

            # Bygg etikett
            kap_del = f"{t.kapitel_nr} kap. " if t.kapitel_nr else ""
            etikett = f"{t.lag_namn} ({t.sfs}) {kap_del}{t.paragraf_nr} §".strip()

            # Bygg varde: sammanfattande och läsbar text
            varde_delar = []
            if t.kapitel_rubrik:
                varde_delar.append(f"{kap_del}{t.kapitel_rubrik}".strip())
            if t.paragraf_rubrik:
                varde_delar.append(t.paragraf_rubrik)
            varde_delar.append(f"{t.paragraf_nr} § {t.paragraf_text}")
            if t.andringsnotis:
                varde_delar.append(t.andringsnotis)
            varde = "\n\n".join(varde_delar)

            dimensioner = {
                "lag": t.lag_namn,
                "kortnamn": t.kortnamn,
                "sfs": t.sfs,
                "paragraf": t.paragraf_nr,
            }
            if t.kapitel_nr:
                dimensioner["kapitel"] = t.kapitel_nr

            utkast.append(
                Faktautkast(
                    etikett=etikett,
                    varde=varde,
                    kalla_id=self.id,
                    myndighet=self._kalla.myndighet or "Sveriges riksdag",
                    licens=self._kalla.licens,
                    attribution=self._kalla.attribution,
                    dataset=t.sfs,
                    period=t.tom_sfs,  # Regel 8: konsolideringspunkten i period
                    hamtad=hamtad_dt,   # Regel 8: tidpunkt när kopian togs
                    lank_manniska=t.lank_manniska,
                    lank_maskin=t.lank_maskin,
                    dimensioner=dimensioner,
                )
            )

        return utkast
