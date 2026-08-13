from datetime import datetime, timezone
from typing import Any

from quiet_oppen_data.adaptrar.bas import Adapter
from quiet_oppen_data.adaptrar.transport import hamta_json
from quiet_oppen_data.modeller import Faktapost, Fragplan
from quiet_oppen_data.register import Kalla, hamta


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

    def hamta(self, plan: Fragplan) -> list[Faktapost]:
        # Flexibel hantering av planens parametrar.
        # Om testet skickar in som attribut:
        serie = getattr(plan, "serie", None) or plan.extra.get("serie")
        if not serie:
            return []

        url = f"{self._kalla.bas_url}/Observations/Latest/{serie}"
        
        try:
            res = hamta_json(self.id, "GET", url)
        except Exception:
            # Vid nätverksfel eller 404 (okänd serie) returnera tomt enligt protokollet.
            return []
            
        if not res or isinstance(res, list) and not res:
            return []
            
        # Riksbanken returnerar t.ex. {"date":"2026-08-12","value":10.9965} eller en lista om listor?
        # Enligt registret: {"date":"2026-08-12","value":10.9965} (eller lista beroende på endpoint, men Latest/ returnerar list[dict] ofta? Vi kollar dict).
        data = res[0] if isinstance(res, list) else res
        if "value" not in data or data["value"] is None:
            return []

        # Riksbankens människo-länk är en portalsida, mall finns i registret.
        manniska = self._kalla.manniskolank_mall or ""
        
        post = Faktapost(
            id="", # Tilldelas av Faktaregister senare
            etikett=f"Riksbanken: {serie}",
            varde=str(data["value"]),
            kalla_id=self.id,
            myndighet=self._kalla.myndighet or "Sveriges riksbank",
            licens=self._kalla.licens,
            hamtad=datetime.now(timezone.utc),
            lank_manniska=manniska,
            lank_maskin=url,
            period=data.get("date")
        )
        return [post]
