"""json_rest-adapter — generisk GET → JSON-path-adapter.

Konfigureras per källa i kallregister.yaml. Samma kodbas hanterar:
  - smhi           (meteorologiska observationer)
  - skolverket     (skolenhetsregistret)
  - trafa          (trafikstatistik)
  - polisen_handelser
  - jobtech        (platsannonser)
"""

import logging
from typing import Any
from urllib.parse import urlparse

from quiet_oppen_data.adaptrar.transport import hamta_json
from quiet_oppen_data.modeller import Faktautkast, Fragplan
from quiet_oppen_data.register import Kalla, hamta

logger = logging.getLogger(__name__)


class JsonRestAdapter:
    """Adapter för enkla GET-API:er som svarar med JSON.

    Instansieras med ett kalla_id. Hämtar {bas_url}/{path} med valfria
    query-parametrar och returnerar en Faktapost per rad i svaret.

    Svarsformatet kan vara:
      - En lista av objekt → en post per objekt
      - Ett objekt med en listnyckel → en post per element i listan
      - Ett platt objekt → en enda post
    """

    def __init__(self, kalla_id: str) -> None:
        k = hamta(kalla_id)
        if not isinstance(k, Kalla):
            raise RuntimeError(f"JSON-REST-källan '{kalla_id}' saknas eller är blockerad.")
        self._kalla = k

    @property
    def id(self) -> str:
        return self._kalla.id

    def beskriv(self) -> list[dict[str, Any]]:
        myndighet = self._kalla.myndighet or self.id
        return [{
            "name": self.id,
            "description": (
                f"Hämtar data från {myndighet} via ett enkelt JSON REST-API. "
                "Anger sökväg och valfria parametrar."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Sökväg relativt bas-URL, t.ex. '/api/events' eller "
                            "'/skolenhetsregistret/v1/skolenhet'. "
                            "Utelämna för att anropa bas-URL direkt."
                        )
                    },
                    "params": {
                        "type": "object",
                        "description": "Query-parametrar, t.ex. {\"locationname\": \"Stockholm\"}"
                    },
                    "listnycklar": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Ordnad lista av nycklar att följa i svaret för att nå listan "
                            "med rader. T.ex. [\"skolenhetslista\", \"skolenhet\"] "
                            "om svaret är {\"skolenhetslista\": {\"skolenhet\": [...]}}. "
                            "Lämna tomt om svaret är en lista direkt."
                        )
                    },
                    "etikett_falt": {
                        "type": "string",
                        "description": (
                            "Vilket fält i varje rad som ska bli Faktapostens etikett. "
                            "Standard: 'name' eller 'titel' eller 'header'."
                        )
                    },
                    "varde_falt": {
                        "type": "string",
                        "description": (
                            "Vilket fält som ska bli Faktapostens värde. "
                            "Standard: hela raden serialiserad."
                        )
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max antal poster att returnera (standard 10)",
                        "minimum": 1,
                        "maximum": 50
                    }
                },
                "required": []
            }
        }]

    def hamta(self, plan: Fragplan) -> list[Faktautkast]:
        path = (plan.extra.get("path") or "").lstrip("/")
        params = plan.extra.get("params") or {}
        listnycklar = plan.extra.get("listnycklar") or []
        etikett_falt = plan.extra.get("etikett_falt") or ""
        varde_falt = plan.extra.get("varde_falt") or ""
        limit = min(int(plan.extra.get("limit") or 10), 50)

        bas = self._kalla.bas_url or ""
        url = f"{bas}/{path}" if path else bas

        try:
            res = hamta_json(self.id, "GET", url, params=params)
        except Exception:
            logger.warning("%s: hämtning misslyckades (url=%s)", self.id, url, exc_info=True)
            return []

        # Navigera till listan via listnycklar
        data = res
        for nyckel in listnycklar:
            if isinstance(data, dict):
                data = data.get(nyckel) or []
            else:
                break

        # Normalisera till lista
        if isinstance(data, dict):
            rader: list[Any] = [data]
        elif isinstance(data, list):
            rader = data
        else:
            logger.warning("%s: svaret är varken dict eller list", self.id)
            return []

        if not rader:
            logger.info("%s: inga rader i svaret", self.id)
            return []

        manniska = self._kalla.manniskolank_mall or bas
        myndighet = self._kalla.myndighet or urlparse(bas).netloc

        _ETIKETT_KANDIDATER = [etikett_falt, "name", "namn", "titel", "title", "header", "rubrik"]
        _VARDE_KANDIDATER = [varde_falt, "description", "beskrivning", "summary", "value"]

        utkast: list[Faktautkast] = []
        for rad in rader[:limit]:
            if not isinstance(rad, dict):
                varde_str = str(rad)
                etikett_str = f"{myndighet} svar"
            else:
                # Välj etikett
                etikett_str = ""
                for k in _ETIKETT_KANDIDATER:
                    if k and rad.get(k):
                        etikett_str = str(rad[k])
                        break
                if not etikett_str:
                    etikett_str = f"{myndighet} post"

                # Välj värde
                varde_str = ""
                for k in _VARDE_KANDIDATER:
                    if k and rad.get(k):
                        varde_str = str(rad[k])
                        break
                if not varde_str:
                    # Fall tillbaka: serialisera hela raden (max 500 tecken)
                    varde_str = "; ".join(
                        f"{k}: {v}" for k, v in rad.items() if v is not None
                    )[:500]

            if not varde_str:
                continue

            utkast.append(Faktautkast(
                etikett=etikett_str[:200],
                varde=varde_str,
                kalla_id=self.id,
                myndighet=myndighet,
                licens=self._kalla.licens,
                attribution=self._kalla.attribution,
                lank_manniska=manniska,
                lank_maskin=url,
            ))

        return utkast
