"""Generisk adapter för PxWeb-servrar (SCB och andra värdar).

Kritiskt: en PxWeb-tabell har dimensioner, och fel skiva ger inte ett
felmeddelande utan ett trovärdigt tal som är fel. Adaptern vägrar därför hämta
data innan alla dimensioner är angivna, och skriver de valda dimensionerna i
varje utkasts `dimensioner` (ARKITEKTUR.md §5 regel 6 och 7).
"""

from __future__ import annotations

import logging
from typing import Any

from quiet_oppen_data.adaptrar.transport import hamta_json
from quiet_oppen_data.modeller import Faktautkast, Fragplan
from quiet_oppen_data.register import Kalla, hamta

logger = logging.getLogger(__name__)

# Fler celler än så avvisas innan anropet görs. SCB rapporterar själv
# maxDataCells 150000 via /config; registret kan sätta ett lägre tak per källa.
STANDARD_MAXCELLER = 150_000


class PxWebAdapter:
    """Generisk adapter för PxWeb-servrar (t.ex. SCB)."""

    def __init__(self, kalla_id: str) -> None:
        k = hamta(kalla_id)
        if not isinstance(k, Kalla):
            raise RuntimeError(
                f"PxWeb-källan {kalla_id} saknas eller är blockerad i registret."
            )
        self._kalla = k

    @property
    def id(self) -> str:
        return self._kalla.id

    # ------------------------------------------------------------------
    # Verktygsdefinitioner
    # ------------------------------------------------------------------

    def beskriv(self) -> list[dict[str, Any]]:
        myndighet = self._kalla.myndighet or "PxWeb"
        return [
            {
                "name": f"{self.id}_lista_dimensioner",
                "description": (
                    f"Listar vilka dimensioner en PxWeb-tabell hos {myndighet} har "
                    "och vilka värdekoder som är giltiga för varje dimension. "
                    "Anropa alltid detta före hamta_data."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "tabell": {
                            "type": "string",
                            "description": "Tabellens id, t.ex. TAB6445",
                        }
                    },
                    "required": ["tabell"],
                },
            },
            {
                "name": f"{self.id}_hamta_data",
                "description": (
                    f"Hämtar data ur en PxWeb-tabell hos {myndighet}. Du måste ange "
                    "ett värde för VARJE dimension som lista_dimensioner returnerade. "
                    "Utelämnas någon dimension hämtas ingen data — du får "
                    "valalternativen tillbaka i stället."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "tabell": {"type": "string", "description": "Tabellens id"},
                        "dimensioner": {
                            "type": "object",
                            "description": (
                                "Map från dimensionskod till lista av värdekoder, "
                                "t.ex. {\"Tid\": [\"2026M07\"], \"ContentsCode\": [\"000007PK\"]}"
                            ),
                        },
                    },
                    "required": ["tabell", "dimensioner"],
                },
            },
        ]

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def _metadata_url(self, tabell: str) -> str:
        return f"{self._kalla.bas_url}/tables/{tabell}/metadata?lang=sv"

    def _data_url(self, tabell: str) -> str:
        # outputFormat MÅSTE ligga i query-strängen. Ett responseFormat i
        # POST-kroppen ignoreras av SCB, som då svarar med PX i iso-8859-1
        # i stället för JSON — verifierat mot api.scb.se 2026-08-13.
        return f"{self._kalla.bas_url}/tables/{tabell}/data?lang=sv&outputFormat=json-stat2"

    def _manniskolank(self, tabell: str) -> str:
        mall = self._kalla.manniskolank_mall
        if not mall:
            return f"{self._kalla.bas_url}/tables/{tabell}"
        try:
            return mall.format(tabell=tabell)
        except (KeyError, IndexError):
            return mall

    def _las_metadata(self, tabell: str) -> dict[str, dict]:
        """Hämtar dimensioner ur tabellens json-stat2-metadata."""
        meta = hamta_json(self.id, "GET", self._metadata_url(tabell))

        dimensioner: dict[str, dict] = {}
        for dim_id in meta.get("id", []):
            dim = meta.get("dimension", {}).get(dim_id, {})
            kategori = dim.get("category", {})
            index = kategori.get("index", {})
            koder = list(index.keys()) if isinstance(index, dict) else list(index or [])
            etiketter = kategori.get("label", {})
            dimensioner[dim_id] = {
                "label": dim.get("label", dim_id),
                "codes": koder,
                "texts": [etiketter.get(k, k) for k in koder],
            }
        return dimensioner

    def _valalternativ(self, tabell: str, dimensioner: dict[str, dict]) -> list[Faktautkast]:
        """Returnerar giltiga dimensionsvärden som utkast.

        Detta är regel 7 i ARKITEKTUR.md §5: hellre visa valen än gissa en skiva.
        """
        utkast: list[Faktautkast] = []
        for dim_id, data in dimensioner.items():
            par = [f"{k} ({t})" for k, t in zip(data["codes"], data["texts"])]
            # Långa dimensioner (typiskt Tid) kortas bakifrån — de senaste
            # värdena är nästan alltid de efterfrågade.
            if len(par) > 100:
                par = par[-100:]
            utkast.append(
                Faktautkast(
                    etikett=(
                        f"Giltiga värden för dimensionen '{data['label']}' "
                        f"({dim_id}) i tabell {tabell}"
                    ),
                    varde=", ".join(par),
                    kalla_id=self.id,
                    myndighet=self._kalla.myndighet or "PxWeb",
                    licens=self._kalla.licens,
                    attribution=self._kalla.attribution,
                    dataset=tabell,
                    lank_manniska=self._manniskolank(tabell),
                    lank_maskin=self._metadata_url(tabell),
                    dimensioner={"dimension": dim_id},
                )
            )
        return utkast

    # ------------------------------------------------------------------
    # Hämtning
    # ------------------------------------------------------------------

    def hamta(self, plan: Fragplan) -> list[Faktautkast]:
        tabell = plan.extra.get("tabell")
        if not tabell:
            logger.info("%s: anrop utan tabell-id, inget att hämta", self.id)
            return []

        try:
            meta_dim = self._las_metadata(tabell)
        except Exception:
            logger.warning("%s: kunde inte läsa metadata för %s", self.id, tabell, exc_info=True)
            return []

        if not meta_dim:
            logger.warning("%s: tabell %s saknar dimensioner i metadata", self.id, tabell)
            return []

        valda = plan.extra.get("dimensioner") or {}
        saknade = [d for d in meta_dim if d not in valda]
        if saknade:
            logger.info("%s: %s saknar dimensioner %s — returnerar valalternativ",
                        self.id, tabell, saknade)
            return self._valalternativ(tabell, meta_dim)

        celler = 1
        for varden in valda.values():
            celler *= len(varden) if isinstance(varden, list) else 1

        maxceller = getattr(self._kalla, "maxceller", None) or STANDARD_MAXCELLER
        if celler > maxceller:
            # Ett fel är inte ett faktum. Vi returnerar ingen post — motorn ser
            # tom lista och loggen förklarar varför.
            logger.warning(
                "%s: uttag ur %s avvisat, %d celler begärda men taket är %d",
                self.id, tabell, celler, maxceller,
            )
            return []

        payload = {
            "selection": [
                {
                    "variableCode": dim,
                    "valueCodes": v if isinstance(v, list) else [v],
                }
                for dim, v in valda.items()
            ]
        }

        try:
            svar = hamta_json(self.id, "POST", self._data_url(tabell), json=payload)
        except Exception:
            logger.warning("%s: datauttag ur %s misslyckades", self.id, tabell, exc_info=True)
            return []

        return self._tolka_jsonstat(tabell, svar)

    # ------------------------------------------------------------------
    # json-stat2 → utkast
    # ------------------------------------------------------------------

    def _tolka_jsonstat(self, tabell: str, svar: dict) -> list[Faktautkast]:
        """Gör om ett json-stat2-svar till ett utkast per cell.

        En Faktapost ska bära ETT värde med sina dimensioner utskrivna, inte en
        datablob. Tidigare returnerade adaptern hela PX-svaret som en sträng,
        vilket gjorde att syntesfasen fick tolka rådata — precis det som §5
        regel 2 förbjuder.
        """
        varden = svar.get("value")
        if not isinstance(varden, list) or not varden:
            logger.warning("%s: tomt eller oväntat json-stat2-svar för %s", self.id, tabell)
            return []

        dim_ordning: list[str] = svar.get("id", [])
        storlek: list[int] = svar.get("size", [])
        dimension = svar.get("dimension", {})

        # Koder och etiketter per dimension, i svarets egen ordning.
        koder: list[list[str]] = []
        etiketter: list[dict[str, str]] = []
        for dim_id in dim_ordning:
            kategori = dimension.get(dim_id, {}).get("category", {})
            index = kategori.get("index", {})
            if isinstance(index, dict):
                # index kan vara {kod: position} — sortera på position.
                kod_lista = [k for k, _ in sorted(index.items(), key=lambda p: p[1])]
            else:
                kod_lista = list(index or [])
            koder.append(kod_lista)
            etiketter.append(kategori.get("label", {}) or {})

        tidsdim = (svar.get("role", {}) or {}).get("time", [])
        enhet = self._enhet(svar, dim_ordning, dimension)
        rubrik = svar.get("label") or f"Tabell {tabell}"

        utkast: list[Faktautkast] = []
        for platt_index, varde in enumerate(varden):
            if varde is None:
                continue
            dim_varden = self._koordinater(platt_index, storlek)
            beskrivning: dict[str, str] = {}
            period: str | None = None
            for pos, dim_id in enumerate(dim_ordning):
                if pos >= len(dim_varden) or dim_varden[pos] >= len(koder[pos]):
                    continue
                kod = koder[pos][dim_varden[pos]]
                text = etiketter[pos].get(kod, kod)
                beskrivning[dimension.get(dim_id, {}).get("label", dim_id)] = text
                if dim_id in tidsdim:
                    period = kod

            utkast.append(
                Faktautkast(
                    etikett=rubrik,
                    varde=str(varde),
                    enhet=enhet,
                    period=period,
                    kalla_id=self.id,
                    myndighet=self._kalla.myndighet or "PxWeb",
                    licens=self._kalla.licens,
                    attribution=self._kalla.attribution,
                    dataset=tabell,
                    lank_manniska=self._manniskolank(tabell),
                    lank_maskin=self._data_url(tabell),
                    dimensioner=beskrivning,
                )
            )
        return utkast

    @staticmethod
    def _koordinater(platt_index: int, storlek: list[int]) -> list[int]:
        """Översätter ett platt json-stat2-index till en koordinat per dimension."""
        koordinater: list[int] = []
        rest = platt_index
        for dim_pos in range(len(storlek)):
            block = 1
            for senare in storlek[dim_pos + 1:]:
                block *= senare
            koordinater.append(rest // block if block else 0)
            rest = rest % block if block else 0
        return koordinater

    @staticmethod
    def _enhet(svar: dict, dim_ordning: list[str], dimension: dict) -> str | None:
        """Plockar enheten ur metric-dimensionens unit-block, om den finns."""
        metric = (svar.get("role", {}) or {}).get("metric", [])
        for dim_id in metric or dim_ordning:
            enheter = dimension.get(dim_id, {}).get("category", {}).get("unit", {})
            if isinstance(enheter, dict) and enheter:
                forsta = next(iter(enheter.values()))
                if isinstance(forsta, dict):
                    return forsta.get("base") or forsta.get("unit")
        return None
