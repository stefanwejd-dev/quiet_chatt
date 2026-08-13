import logging
from typing import Any

from quiet_oppen_data.adaptrar.transport import hamta_json
from quiet_oppen_data.modeller import Faktautkast, Fragplan
from quiet_oppen_data.register import Kalla, hamta

logger = logging.getLogger(__name__)


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

    def beskriv(self) -> list[dict[str, Any]]:
        return [{
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
        }]

    def hamta(self, plan: Fragplan) -> list[Faktautkast]:
        momsnr = plan.extra.get("momsnr")
        if not momsnr:
            logger.info("%s: anrop utan momsnummer, inget att hämta", self.id)
            return []

        land = (plan.extra.get("land") or "SE").upper()
        momsnr = str(momsnr).strip().replace(" ", "").upper()
        if momsnr.startswith(land):
            momsnr = momsnr[len(land):]

        url = f"{self._kalla.bas_url}/ms/{land}/vat/{momsnr}"

        try:
            res = hamta_json(self.id, "GET", url)
        except Exception:
            logger.warning("%s: validering av %s%s misslyckades", self.id, land, momsnr, exc_info=True)
            return []

        if not isinstance(res, dict):
            logger.warning("%s: oväntat svarsformat för %s%s", self.id, land, momsnr)
            return []

        giltigt = bool(res.get("isValid", False))
        # Etiketten får inte påstå giltighet — den beskriver vad som mätts,
        # värdet bär utfallet. "Momsnummer X giltigt = false" är en fälla för
        # syntesmodellen att läsa fel.
        utkast = [
            Faktautkast(
                etikett=f"VIES-kontroll av momsregistreringsnummer {land}{momsnr}",
                varde="giltigt" if giltigt else "ogiltigt",
                period=(res.get("requestDate") or "")[:10] or None,
                kalla_id=self.id,
                myndighet=self._kalla.myndighet or "Europeiska kommissionen",
                licens=self._kalla.licens,
                attribution=self._kalla.attribution,
                dataset=f"{land}{momsnr}",
                lank_manniska=self._kalla.manniskolank_mall or self._kalla.bas_url,
                lank_maskin=url,
            )
        ]

        # VIES returnerar "---" när namn inte lämnas ut; ta bara med riktiga värden.
        namn = (res.get("name") or "").strip()
        if giltigt and namn and namn != "---":
            utkast.append(
                Faktautkast(
                    etikett=f"Registrerat namn för {land}{momsnr} enligt VIES",
                    varde=namn,
                    kalla_id=self.id,
                    myndighet=self._kalla.myndighet or "Europeiska kommissionen",
                    licens=self._kalla.licens,
                    attribution=self._kalla.attribution,
                    dataset=f"{land}{momsnr}",
                    lank_manniska=self._kalla.manniskolank_mall or self._kalla.bas_url,
                    lank_maskin=url,
                )
            )
        return utkast
