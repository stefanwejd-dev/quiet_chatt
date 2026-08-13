import logging
from typing import Any

from quiet_oppen_data.adaptrar.transport import hamta_json
from quiet_oppen_data.modeller import Faktautkast, Fragplan
from quiet_oppen_data.register import Kalla, hamta

logger = logging.getLogger(__name__)


class RiksbankenAdapter:
    """Adapter för Sveriges Riksbank (räntor och valutakurser)."""

    def __init__(self) -> None:
        k = hamta("riksbanken")
        if not isinstance(k, Kalla):
            raise RuntimeError("Riksbanken-källan saknas eller är blockerad i registret.")
        self._kalla = k

    @property
    def id(self) -> str:
        return self._kalla.id

    def beskriv(self) -> list[dict[str, Any]]:
        return [{
            "name": self.id,
            "description": "Hämtar senaste observation för en ränta eller valutakurs (t.ex. SEKEURPMI).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "serie": {
                        "type": "string",
                        "description": "Seriens ID (ex. SEKEURPMI för Euro-kurs, SECRINTP för styrränta)"
                    }
                },
                "required": ["serie"]
            }
        }]

    def hamta(self, plan: Fragplan) -> list[Faktautkast]:
        serie = plan.extra.get("serie")
        if not serie:
            logger.info("%s: anrop utan serie-id, inget att hämta", self.id)
            return []

        url = f"{self._kalla.bas_url}/Observations/Latest/{serie}"

        try:
            res = hamta_json(self.id, "GET", url)
        except Exception:
            # Nätverksfel eller okänd serie. Loggas — annars blir ett trasigt
            # anrop omöjligt att skilja från "källan hade inget att säga".
            logger.warning("%s: hämtning av serie %s misslyckades", self.id, serie, exc_info=True)
            return []

        # SWEA svarar med ett objekt för Latest, men med lista för intervall.
        data = res[0] if isinstance(res, list) and res else res
        if not isinstance(data, dict) or data.get("value") is None:
            logger.info("%s: serie %s gav inget värde", self.id, serie)
            return []

        return [
            Faktautkast(
                etikett=f"Riksbanken, serie {serie}",
                varde=str(data["value"]),
                period=data.get("date"),
                kalla_id=self.id,
                myndighet=self._kalla.myndighet or "Sveriges riksbank",
                licens=self._kalla.licens,
                attribution=self._kalla.attribution,
                dataset=serie,
                lank_manniska=self._kalla.manniskolank_mall or self._kalla.bas_url,
                lank_maskin=url,
            )
        ]
