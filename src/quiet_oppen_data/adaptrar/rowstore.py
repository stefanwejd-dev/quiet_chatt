"""RowStore-adapter — generisk EntryScape RowStore-klient.

Används av:
  - skatteverket_rowstore  (skatteverket.entryscape.net)
  - kronofogden_rowstore   (kronofogden.entryscape.net)
  - _generisk_rowstore     (godtycklig RowStore-instans via katalogindexet)

Alla RowStore-datamängder nås som:
  GET {bas_url}/{uuid}/json?_limit=N&_offset=M[&{kolumn}={värde}]

Paginering: API:t returnerar {resultCount, offset, limit, next, results}.
Adaptern hämtar en sida (default limit=100) och returnerar den som Faktaposter.
"""

import logging
from typing import Any
from urllib.parse import urlparse

from quiet_oppen_data.adaptrar.transport import hamta_json
from quiet_oppen_data.modeller import Faktautkast, Fragplan
from quiet_oppen_data.register import Kalla, hamta

logger = logging.getLogger(__name__)

_STANDARD_LIMIT = 100


class RowStoreAdapter:
    """Generisk adapter för EntryScape RowStore-datamängder.

    Instansieras med ett kalla_id (t.ex. "skatteverket_rowstore" eller
    "_generisk_rowstore"). Den generiska varianten tar dataset-UUID och
    bas_url från Fragplan.extra.
    """

    def __init__(self, kalla_id: str) -> None:
        k = hamta(kalla_id)
        if not isinstance(k, Kalla):
            raise RuntimeError(f"RowStore-källan '{kalla_id}' saknas eller är blockerad.")
        self._kalla = k

    @property
    def id(self) -> str:
        return self._kalla.id

    def beskriv(self) -> list[dict[str, Any]]:
        generisk = self._kalla.generisk
        if generisk:
            return [{
                "name": self.id,
                "description": (
                    "Hämtar data ur en godtycklig EntryScape RowStore-datamängd "
                    "via katalogindexets access_url. Kräver bas_url och uuid."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "bas_url": {
                            "type": "string",
                            "description": "Rooten till RowStore-instansen (utan /dataset/...)"
                        },
                        "uuid": {
                            "type": "string",
                            "description": "Dataset-UUID"
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max rader (standard 100)",
                            "minimum": 1,
                            "maximum": 500
                        },
                        "offset": {
                            "type": "integer",
                            "description": "Hoppa över de första N raderna (paginering)",
                            "minimum": 0
                        },
                        "filter": {
                            "type": "object",
                            "description": "Kolumn=värde-filter, t.ex. {\"statistikterm\": \"Moms\"}"
                        }
                    },
                    "required": ["bas_url", "uuid"]
                }
            }]
        else:
            return [{
                "name": self.id,
                "description": (
                    f"Hämtar data från {self._kalla.myndighet or self.id} via RowStore. "
                    "Anger dataset-UUID och eventuellt filter."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "uuid": {
                            "type": "string",
                            "description": "Dataset-UUID (hittas via dataportal-sökning)"
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max rader (standard 100)",
                            "minimum": 1,
                            "maximum": 500
                        },
                        "offset": {
                            "type": "integer",
                            "description": "Hoppa över de första N raderna",
                            "minimum": 0
                        },
                        "filter": {
                            "type": "object",
                            "description": "Kolumn=värde-filter"
                        }
                    },
                    "required": ["uuid"]
                }
            }]

    def hamta(self, plan: Fragplan) -> list[Faktautkast]:
        uuid = plan.extra.get("uuid")
        if not uuid:
            logger.info("%s: anrop utan UUID", self.id)
            return []

        # Generisk: bas_url kan komma från plan (katalogindexets access_url)
        if self._kalla.generisk:
            bas_url = plan.extra.get("bas_url") or ""
            if not bas_url:
                logger.warning("%s: generisk rowstore utan bas_url", self.id)
                return []
            # Normalisera: strippa /dataset/uuid om det råkar sitta med
            if "/dataset/" in bas_url:
                bas_url = bas_url.split("/dataset/")[0]
        else:
            bas_url = self._kalla.bas_url or ""

        limit = min(int(plan.extra.get("limit") or _STANDARD_LIMIT), 500)
        offset = max(int(plan.extra.get("offset") or 0), 0)
        filter_dict = plan.extra.get("filter") or {}

        url = f"{bas_url}/{uuid}/json"
        params: dict[str, Any] = {"_limit": limit, "_offset": offset}
        params.update(filter_dict)

        try:
            res = hamta_json(self.id, "GET", url, params=params)
        except Exception:
            logger.warning("%s: hämtning av UUID=%s misslyckades", self.id, uuid, exc_info=True)
            return []

        results = res.get("results") or []
        total = res.get("resultCount") or 0

        if not results:
            logger.info("%s: inga rader för UUID=%s (total=%s)", self.id, uuid, total)
            return []

        # Bestäm myndighet och manniska-länk
        myndighet = self._kalla.myndighet or urlparse(bas_url).netloc
        manniska = self._kalla.manniskolank_mall or bas_url

        utkast: list[Faktautkast] = []
        for rad in results:
            if not isinstance(rad, dict):
                continue
            # Serialisera hela raden till ett läsbart strängvärde
            varde = "; ".join(f"{k}: {v}" for k, v in rad.items() if v is not None)
            if not varde:
                continue

            utkast.append(Faktautkast(
                etikett=f"{myndighet}, dataset {uuid} (rad {offset + len(utkast) + 1}/{total})",
                varde=varde,
                kalla_id=self.id,
                myndighet=myndighet,
                licens=self._kalla.licens,
                attribution=self._kalla.attribution,
                dataset=uuid,
                dimensioner=filter_dict,
                lank_manniska=manniska,
                lank_maskin=url,
            ))

        return utkast
