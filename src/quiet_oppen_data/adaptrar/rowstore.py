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
        specar: list[dict[str, Any]] = []

        # Har källan en kurerad datasetkatalog exponeras den som eget verktyg.
        # Ett RowStore-UUID går inte att gissa; utan katalogen är källan i
        # praktiken oanvändbar för modellen (ARKITEKTUR.md §5 regel 7).
        if self._kalla.dataset:
            specar.append({
                "name": f"{self.id}_lista_dataset",
                "description": (
                    f"Listar tillgängliga datamängder hos "
                    f"{self._kalla.myndighet or self.id} med UUID, innehåll och "
                    "kolumnnamn. Anropa ALLTID detta först — UUID går inte att gissa."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "sok": {
                            "type": "string",
                            "description": (
                                "Fritext som matchas mot datamängdens namn och "
                                "beskrivning, t.ex. 'skattesats' eller 'traktamente'."
                            ),
                        }
                    },
                    "required": [],
                },
            })

        if True:
            specar.append({
                "name": self.id,
                "description": (
                    f"Hämtar data från {self._kalla.myndighet or self.id} via RowStore. "
                    + ("UUID MÅSTE komma från "
                       f"{self.id}_lista_dataset — gissa aldrig ett UUID."
                       if self._kalla.dataset else
                       "Anger dataset-UUID och eventuellt filter.")
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
            })

        return specar

    # ------------------------------------------------------------------
    # Datasetkatalog
    # ------------------------------------------------------------------

    def _lista_dataset(self, sok: str | None) -> list[Faktautkast]:
        """Returnerar den kurerade katalogen som utkast, så modellen kan välja."""
        poster = self._kalla.dataset or []
        if sok:
            nal = sok.lower()
            poster = [
                d for d in poster
                if nal in str(d.get("namn", "")).lower()
                or nal in str(d.get("beskrivning", "")).lower()
            ]
        if not poster:
            logger.info("%s: ingen datamängd matchade sok=%r", self.id, sok)
            return []

        rader = [
            f"{d['uuid']} — {d.get('namn', '?')}"
            + (f" (kolumner: {', '.join(d['kolumner'])})" if d.get("kolumner") else "")
            for d in poster
        ]
        return [
            Faktautkast(
                etikett=(f"Datamängder hos {self._kalla.myndighet or self.id} "
                         f"som matchar {sok!r}" if sok
                         else f"Tillgängliga datamängder hos {self._kalla.myndighet or self.id}"),
                varde=" | ".join(rader),
                kalla_id=self.id,
                myndighet=self._kalla.myndighet or self.id,
                licens=self._kalla.licens,
                attribution=self._kalla.attribution,
                lank_manniska=self._kalla.manniskolank_mall or self._kalla.bas_url,
                lank_maskin=self._kalla.bas_url,
            )
        ]

    def hamta(self, plan: Fragplan) -> list[Faktautkast]:
        if plan.extra.get("verktyg") == f"{self.id}_lista_dataset":
            return self._lista_dataset(plan.extra.get("sok"))

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

        # Namnet ur den kurerade katalogen. Etiketten måste säga VAD uppgiften
        # är, inte bara vilket UUID den hämtades med (ARKITEKTUR.md §5) —
        # "Skatteverket, dataset 006353ad-…" gör felet osynligt i källpanelen.
        katalogpost = next(
            (d for d in (self._kalla.dataset or []) if d.get("uuid") == uuid), {}
        )
        datasetnamn = katalogpost.get("namn")

        # Kolumner som fungerar som period. RowStore-datamängderna hos
        # Skatteverket bär årtalet i "år"; andra instanser kan använda andra namn.
        # De elva statistikdatamängderna från steg 17 bär i stället period/år
        # i myndighetsspecifika kolumnnamn (redovisningsperiod, besoksar, …).
        _PERIODKOLUMNER = (
            "år", "ar", "period", "redovisningsperiod", "inkomstår", "inkomstar",
            "ankomstår", "ankomstar", "besoksar", "verksamhetsar", "redovisningsar",
        )

        # Kolumner som bär radens egen uppdateringsuppgift (steg 17). Till
        # skillnad från de åtta ursprungliga datamängderna bär statistik-
        # datamängderna en `uppdateringsdatum`-kolumn i varje rad — den är då
        # AVLÄST, inte påstådd, och ska in i dimensioner precis som period.
        _UPPDATERINGSKOLUMNER = ("uppdateringsdatum", "uppdaterad")

        utkast: list[Faktautkast] = []
        for rad in results:
            if not isinstance(rad, dict):
                continue

            period = next(
                (str(rad[k]) for k in _PERIODKOLUMNER if rad.get(k) not in (None, "")),
                None,
            )
            uppdaterad_rad = next(
                (str(rad[k]) for k in _UPPDATERINGSKOLUMNER if rad.get(k) not in (None, "")),
                None,
            )
            # Period- och uppdateringskolumnen upprepas inte i värdet — de bärs
            # av period- respektive dimensioner-fältet.
            varde = "; ".join(
                f"{k}: {v}" for k, v in rad.items()
                if v is not None
                and not (period and k in _PERIODKOLUMNER)
                and not (uppdaterad_rad and k in _UPPDATERINGSKOLUMNER)
            )
            if not varde:
                continue

            dimensioner = dict(filter_dict)
            if uppdaterad_rad:
                dimensioner["uppdateringsdatum"] = uppdaterad_rad
            elif katalogpost.get("uppdaterad"):
                # Ingen uppdateringsdatum i den här raden. Datumet nedan är
                # källregistrets PÅSTÅDDA uppgift (Skatteverkets egen
                # publiceringsuppgift), inte avläst ur anropet — nyckelnamnet
                # gör den skillnaden synlig i svaret (ARKITEKTUR.md §5 regel 8).
                dimensioner["uppdaterad_enligt_kallregister"] = katalogpost["uppdaterad"]

            if datasetnamn:
                etikett = f"{myndighet}: {datasetnamn}"
            else:
                etikett = f"{myndighet}, dataset {uuid}"
            etikett += f" (rad {offset + len(utkast) + 1}/{total})"

            utkast.append(Faktautkast(
                etikett=etikett,
                period=period,
                varde=varde,
                kalla_id=self.id,
                myndighet=myndighet,
                licens=self._kalla.licens,
                attribution=self._kalla.attribution,
                dataset=uuid,
                dimensioner=dimensioner,
                lank_manniska=manniska,
                lank_maskin=url,
            ))

        return utkast
