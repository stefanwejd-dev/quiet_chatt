import logging
from typing import Any
from urllib.parse import urlencode

from quiet_oppen_data.adaptrar.transport import hamta_json
from quiet_oppen_data.modeller import Faktautkast, Fragplan
from quiet_oppen_data.register import Kalla, hamta

logger = logging.getLogger(__name__)


class DataportalAdapter:
    """Adapter för Sveriges dataportal (discovery-lager och nivå-3-fallback).

    När ingen annan adapter kan exekvera mot en datamängd kan denna adapter
    returnera *metadata* om var uppgiften finns som Faktaposter — boten svarar
    då «det finns hos Boverket, här är datamängden» i stället för det faktiska
    värdet. Det är ett giltigt svar, inte ett fel (ARKITEKTUR.md §3.3).
    """

    def __init__(self) -> None:
        k = hamta("dataportal")
        if not isinstance(k, Kalla):
            raise RuntimeError("Dataportal-källan saknas eller är blockerad i registret.")
        self._kalla = k

    @property
    def id(self) -> str:
        return self._kalla.id

    def beskriv(self) -> list[dict[str, Any]]:
        return [{
            "name": self.id,
            "description": (
                "Söker i Sveriges dataportal (23 000+ datamängder från 155+ myndigheter). "
                "Returnerar metadata om var data finns — inte själva värdena. "
                "Använd detta som sista utväg när ingen specifik adapter kan svara."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "sok": {
                        "type": "string",
                        "description": "Sökterm, t.ex. 'vindkraft', 'bostadspriser', 'miljötillstånd'"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max antal träffar (1–10). Standard: 5.",
                        "minimum": 1,
                        "maximum": 10
                    }
                },
                "required": ["sok"]
            }
        }]

    def hamta(self, plan: Fragplan) -> list[Faktautkast]:
        sok = plan.extra.get("sok")
        if not sok:
            logger.info("%s: anrop utan söksträng", self.id)
            return []

        limit = min(int(plan.extra.get("limit") or 5), 10)

        # Dataportalen använder Solr-syntax
        query = f"rdfType:http\\://www.w3.org/ns/dcat\\#Dataset AND public:true AND (*{sok}*)"
        params: dict[str, Any] = {
            "type": "solr",
            "query": query,
            "rows": limit,
            "start": 0,
        }

        url = self._kalla.bas_url

        try:
            res = hamta_json(self.id, "GET", url, params=params)
        except Exception:
            logger.warning("%s: sökning misslyckades (sok=%r)", self.id, sok, exc_info=True)
            return []

        # Dataportalen svarar med {"hits": {"hits": [...]}, "aggregations": {...}}
        try:
            hits = res.get("hits", {}).get("hits") or []
        except AttributeError:
            logger.warning("%s: oväntat svarsformat", self.id)
            return []

        if not hits:
            logger.info("%s: inga träffar för sok=%r", self.id, sok)
            return []

        utkast: list[Faktautkast] = []
        for hit in hits:
            src = hit.get("_source") or {}
            titel = (src.get("title_sv") or src.get("title_en") or "").strip()
            utgivare = (src.get("publisher_name") or "").strip()
            beskrivning = (src.get("description_sv") or src.get("description_en") or "").strip()
            ctx = src.get("context") or ""
            entry = src.get("id") or ""

            if not entry:
                continue

            manniska = self._kalla.manniskolank_mall or ""
            if "{ctx}_{entry}" in manniska and ctx:
                manniska = manniska.format(ctx=ctx, entry=entry)
            elif "{ctx}_{entry}" in manniska:
                manniska = manniska.format(ctx="", entry=entry)

            maskin = f"{url}?{urlencode({'type': 'solr', 'query': f'id:{entry}'})}"

            utkast.append(Faktautkast(
                etikett=f"Dataportal: {titel or entry}",
                varde=beskrivning[:300] if beskrivning else "Ingen beskrivning tillgänglig",
                kalla_id=self.id,
                myndighet=utgivare or (self._kalla.myndighet or ""),
                licens=self._kalla.licens,
                attribution=self._kalla.attribution,
                dataset=entry,
                dimensioner={"utgivare": utgivare} if utgivare else {},
                lank_manniska=manniska or f"https://www.dataportal.se/datasets/{entry}",
                lank_maskin=maskin,
            ))

        return utkast
