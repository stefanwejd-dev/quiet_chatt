import logging
from typing import Any

from quiet_oppen_data.adaptrar.transport import hamta_json
from quiet_oppen_data.modeller import Faktautkast, Fragplan
from quiet_oppen_data.register import Kalla, hamta

logger = logging.getLogger(__name__)


class KoladaAdapter:
    """Adapter för Kolada — kommunala och regionala nyckeltal (RKA).

    Stödjer tre verktyg:
      - kolada_sok_kpi: sök bland ~6100 nyckeltal på textnyckelord
      - kolada_hamta: hämta faktiskt värde för ett givet KPI-id, år och kommuner
    """

    def __init__(self) -> None:
        k = hamta("kolada")
        if not isinstance(k, Kalla):
            raise RuntimeError("Kolada-källan saknas eller är blockerad i registret.")
        self._kalla = k

    @property
    def id(self) -> str:
        return self._kalla.id

    def beskriv(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "kolada_sok_kpi",
                "description": (
                    "Söker i Koladas katalog av ~6100 kommunala och regionala nyckeltal (KPI). "
                    "Returnerar KPI-id och beskrivning. Anropa detta först för att hitta "
                    "rätt KPI-id innan du hämtar data."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "sok": {
                            "type": "string",
                            "description": "Sökord, t.ex. 'förskola', 'ekonomibistånd', 'sjukfrånvaro'"
                        }
                    },
                    "required": ["sok"]
                }
            },
            {
                "name": "kolada_hamta",
                "description": (
                    "Hämtar faktiska värden för ett Kolada-nyckeltal (KPI) för angivna kommuner och år. "
                    "Kräver att du känner till KPI-id (hämtas med kolada_sok_kpi)."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "kpi_id": {
                            "type": "string",
                            "description": "KPI-id från Kolada, t.ex. 'N00914'"
                        },
                        "kommuner": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Lista med kommunkoder (SCB 4-siffriga), t.ex. ['1280', '0180']"
                        },
                        "ar": {
                            "type": "integer",
                            "description": "År, t.ex. 2023"
                        }
                    },
                    "required": ["kpi_id", "kommuner", "ar"]
                }
            }
        ]

    def hamta(self, plan: Fragplan) -> list[Faktautkast]:
        verktyg = plan.extra.get("verktyg") or plan.extra.get("name") or "kolada_hamta"

        if verktyg == "kolada_sok_kpi":
            return self._sok_kpi(plan)
        return self._hamta_data(plan)

    def _sok_kpi(self, plan: Fragplan) -> list[Faktautkast]:
        sok = plan.extra.get("sok")
        if not sok:
            logger.info("%s: kolada_sok_kpi utan sökord", self.id)
            return []

        url = f"{self._kalla.bas_url}/kpi"
        params = {"title": sok, "per_page": 10}

        try:
            res = hamta_json(self.id, "GET", url, params=params)
        except Exception:
            logger.warning("%s: sökning av KPI misslyckades (sok=%r)", self.id, sok, exc_info=True)
            return []

        values = res.get("values") or []
        if not values:
            logger.info("%s: inga KPI-träffar för sok=%r", self.id, sok)
            return []

        utkast: list[Faktautkast] = []
        for kpi in values:
            kid = kpi.get("id") or ""
            titel = (kpi.get("title") or "").strip()
            if not kid:
                continue
            utkast.append(Faktautkast(
                etikett=f"Kolada KPI {kid}: {titel}",
                varde=kid,
                kalla_id=self.id,
                myndighet=self._kalla.myndighet or "Kolada (RKA)",
                licens=self._kalla.licens,
                attribution=self._kalla.attribution,
                dataset=kid,
                lank_manniska=self._kalla.manniskolank_mall or self._kalla.bas_url,
                lank_maskin=f"{url}/{kid}",
            ))

        return utkast

    def _hamta_data(self, plan: Fragplan) -> list[Faktautkast]:
        kpi_id = plan.extra.get("kpi_id")
        kommuner = plan.extra.get("kommuner") or []
        ar = plan.extra.get("ar")

        if not kpi_id:
            logger.info("%s: kolada_hamta utan kpi_id", self.id)
            return []
        if not kommuner:
            logger.info("%s: kolada_hamta utan kommuner", self.id)
            return []
        if not ar:
            logger.info("%s: kolada_hamta utan år", self.id)
            return []

        kommuner_str = ",".join(str(k) for k in kommuner)
        url = f"{self._kalla.bas_url}/data/kpi/{kpi_id}/municipality/{kommuner_str}/year/{ar}"

        try:
            res = hamta_json(self.id, "GET", url)
        except Exception:
            logger.warning("%s: hämtning av KPI %s misslyckades", self.id, kpi_id, exc_info=True)
            return []

        values = res.get("values") or []
        if not values:
            logger.info("%s: inga värden för KPI=%s, år=%s, kommuner=%s", self.id, kpi_id, ar, kommuner_str)
            return []

        utkast: list[Faktautkast] = []
        for post in values:
            for rad in (post.get("values") or []):
                varde_raw = rad.get("value")
                if varde_raw is None:
                    continue
                kommun_id = str(post.get("municipality") or "")
                kon = str(rad.get("gender") or "T")

                utkast.append(Faktautkast(
                    etikett=f"Kolada {kpi_id}, kommun {kommun_id}, {ar}",
                    varde=str(varde_raw),
                    period=str(ar),
                    kalla_id=self.id,
                    myndighet=self._kalla.myndighet or "Kolada (RKA)",
                    licens=self._kalla.licens,
                    attribution=self._kalla.attribution,
                    dataset=kpi_id,
                    dimensioner={"kommun": kommun_id, "kön": kon},
                    lank_manniska=self._kalla.manniskolank_mall or self._kalla.bas_url,
                    lank_maskin=url,
                ))

        return utkast
