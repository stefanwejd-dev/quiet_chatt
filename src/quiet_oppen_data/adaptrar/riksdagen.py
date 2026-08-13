import logging
from typing import Any

from quiet_oppen_data.adaptrar.transport import hamta_json
from quiet_oppen_data.modeller import Faktautkast, Fragplan
from quiet_oppen_data.register import Kalla, hamta

logger = logging.getLogger(__name__)


class RiksdagenAdapter:
    """Adapter för Riksdagsförvaltningens öppna data.

    Söker i riksdagens dokumentlista (propositioner, motioner, betänkanden, SOU m.m.).
    """

    def __init__(self) -> None:
        k = hamta("riksdagen")
        if not isinstance(k, Kalla):
            raise RuntimeError("Riksdagen-källan saknas eller är blockerad i registret.")
        self._kalla = k

    @property
    def id(self) -> str:
        return self._kalla.id

    def beskriv(self) -> list[dict[str, Any]]:
        return [{
            "name": self.id,
            "description": (
                "Söker i riksdagens dokument: propositioner, motioner, betänkanden, "
                "SOU, SFS-lagar, anföranden och ledamötsuppgifter."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "sok": {
                        "type": "string",
                        "description": "Sökterm (fritext)"
                    },
                    "doktyp": {
                        "type": "string",
                        "description": (
                            "Dokumenttyp (valfritt): prop=proposition, mot=motion, "
                            "bet=betänkande, SOU, sfs=lag/förordning"
                        )
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

        doktyp = plan.extra.get("doktyp") or ""
        limit = min(int(plan.extra.get("limit") or 5), 10)

        params: dict[str, Any] = {
            "sok": sok,
            "utformat": "json",
            "sz": limit,
        }
        if doktyp:
            params["doktyp"] = doktyp

        url = f"{self._kalla.bas_url}/dokumentlista/"

        try:
            res = hamta_json(self.id, "GET", url, params=params)
        except Exception:
            logger.warning("%s: sökning misslyckades (sok=%r)", self.id, sok, exc_info=True)
            return []

        # Riksdagens API svarar med {"dokumentlista": {"dokument": [...], ...}}
        try:
            dokument = res.get("dokumentlista", {}).get("dokument") or []
        except AttributeError:
            logger.warning("%s: oväntat svarsformat", self.id)
            return []

        if not dokument:
            logger.info("%s: inga träffar för sok=%r", self.id, sok)
            return []

        utkast: list[Faktautkast] = []
        for dok in dokument:
            dok_id = dok.get("dok_id") or ""
            titel = (dok.get("titel") or "").strip()
            undertitel = (dok.get("undertitel") or "").strip()
            organ = (dok.get("organ") or "").strip()
            datum = (dok.get("datum") or "")[:10] or None

            if not dok_id:
                continue

            # Bygg klickbar länk
            manniska = self._kalla.manniskolank_mall or ""
            if "{dok_id}" in manniska:
                manniska = manniska.format(dok_id=dok_id)
            maskin = f"{self._kalla.bas_url}/dokument/{dok_id}/?utformat=json"

            full_titel = f"{titel}: {undertitel}".strip(": ") if undertitel else titel

            utkast.append(Faktautkast(
                etikett=f"Riksdagsdokument {dok_id}: {full_titel or 'utan titel'}",
                varde=undertitel or titel or dok_id,
                period=datum,
                kalla_id=self.id,
                myndighet=self._kalla.myndighet or "Riksdagsförvaltningen",
                licens=self._kalla.licens,
                attribution=self._kalla.attribution,
                dataset=dok_id,
                dimensioner={"organ": organ} if organ else {},
                lank_manniska=manniska,
                lank_maskin=maskin,
            ))

        return utkast
