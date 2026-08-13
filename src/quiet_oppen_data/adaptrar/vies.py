from datetime import datetime, timezone
from typing import Any

from quiet_oppen_data.adaptrar.bas import Adapter
from quiet_oppen_data.adaptrar.transport import hamta_json
from quiet_oppen_data.modeller import Faktapost, Fragplan
from quiet_oppen_data.register import Kalla, hamta


class ViesAdapter:
    """Adapter för EU VIES (momsnummer-validering)."""

    def __init__(self) -> None:
        k = hamta("vies")
        if not isinstance(k, Kalla):
            raise RuntimeError("VIES-källan saknas eller är blockerad i registret.")
        self._kalla = k

    @property
    def id(self) -> str:
        return self._kalla.id

    def beskriv(self) -> dict[str, Any]:
        return {
            "name": self.id,
            "description": "Validerar ett EU-momsregistreringsnummer.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "momsnr": {
                        "type": "string",
                        "description": "Momsnummer (utan lands-prefix), t.ex. 556036111101"
                    },
                    "land": {
                        "type": "string",
                        "description": "Lands-prefix (ex. SE). Standard: SE",
                        "default": "SE"
                    }
                },
                "required": ["momsnr"]
            }
        }

    def hamta(self, plan: Fragplan) -> list[Faktapost]:
        momsnr = getattr(plan, "momsnr", None) or plan.extra.get("momsnr")
        if not momsnr:
            return []
            
        land = getattr(plan, "land", None) or plan.extra.get("land", "SE")
        
        # Säkerställ ren sträng
        momsnr = str(momsnr).strip().replace(" ", "")
        if momsnr.startswith(land):
            momsnr = momsnr[len(land):]

        url = f"{self._kalla.bas_url}/ms/{land}/vat/{momsnr}"
        
        try:
            res = hamta_json(self._kalla, "GET", url)
        except Exception:
            return []
            
        if not res or not isinstance(res, dict):
            return []
            
        is_valid = res.get("isValid", False)
        
        manniska = self._kalla.manniskolank_mall or ""
        
        post = Faktapost(
            id="",
            etikett=f"Momsnummer {land}{momsnr} giltigt",
            varde=str(is_valid).lower(),
            kalla_id=self.id,
            myndighet=self._kalla.myndighet or "Europeiska kommissionen",
            licens=self._kalla.licens,
            hamtad=datetime.now(timezone.utc),
            lank_manniska=manniska,
            lank_maskin=url
        )
        return [post]
