import logging
from typing import Any

from quiet_oppen_data.adaptrar.transport import hamta_json
from quiet_oppen_data.modeller import Faktautkast, Fragplan
from quiet_oppen_data.register import Kalla, hamta

logger = logging.getLogger(__name__)

_SPRAK_ORDNING = ["swe", "eng", "fra", "deu"]


def _plocka_text(obj: Any) -> str:
    """Hämtar läsbar text ur ett flerspråkigt TED-textfält.

    TED returnerar textfält som antingen:
      - En sträng (det enkla fallet)
      - Ett dict med språkkoder som nycklar: {"swe": "...", "eng": "..."}
      - En lista av sådana (vi tar första).
    """
    if isinstance(obj, str):
        return obj
    if isinstance(obj, list):
        obj = obj[0] if obj else ""
        if isinstance(obj, str):
            return obj
    if isinstance(obj, dict):
        for sprak in _SPRAK_ORDNING:
            if obj.get(sprak):
                return str(obj[sprak])
        # Fall tillbaka på första tillgängliga
        for v in obj.values():
            if v:
                return str(v)
    return ""


class TedAdapter:
    """Adapter för TED (Tenders Electronic Daily) — EU:s upphandlingsportal.

    Söker bland meddelanden från svenska upphandlare. Notera att ett *meddelande*
    inte är samma sak som en *upphandling* — samma upphandling kan ge upphov till
    flera meddelanden (t.ex. ett för utlysning och ett för tilldelning).
    """

    def __init__(self) -> None:
        k = hamta("ted")
        if not isinstance(k, Kalla):
            raise RuntimeError("TED-källan saknas eller är blockerad i registret.")
        self._kalla = k

    @property
    def id(self) -> str:
        return self._kalla.id

    def beskriv(self) -> list[dict[str, Any]]:
        return [{
            "name": self.id,
            "description": (
                "Söker i TED (Tenders Electronic Daily) efter svenska upphandlingsmeddelanden. "
                "Returnerar upphandlingar annonserade av svenska köpare. "
                "Observera: ett meddelande är inte alltid en hel upphandling — "
                "samma upphandling kan ge flera meddelanden."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "fraga": {
                        "type": "string",
                        "description": (
                            "TED-frågesträng (eForms-syntax). "
                            "Standard: 'buyer-country=SWE AND publication-date>=today(-30)'. "
                            "Exempel: 'buyer-country=SWE AND classification-cpv=45000000'"
                        )
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max antal träffar (1–10). Standard: 5.",
                        "minimum": 1,
                        "maximum": 10
                    }
                },
                "required": []
            }
        }]

    def hamta(self, plan: Fragplan) -> list[Faktautkast]:
        fraga = plan.extra.get("fraga") or "buyer-country=SWE AND publication-date>=today(-30)"
        limit = min(int(plan.extra.get("limit") or 5), 10)

        faltval = list(self._kalla.faltval) if self._kalla.faltval else [
            "publication-number", "publication-date", "notice-title",
            "buyer-name", "buyer-city", "tender-value", "tender-value-cur",
        ]

        payload = {
            "query": fraga,
            "limit": limit,
            "fields": faltval,
        }

        url = self._kalla.bas_url  # POST-endpoint är bas_url för TED

        try:
            res = hamta_json(self.id, "POST", url, json=payload)
        except Exception:
            logger.warning("%s: sökning misslyckades (fraga=%r)", self.id, fraga, exc_info=True)
            return []

        notices = res.get("notices") or []
        if not notices:
            logger.info("%s: inga träffar för fraga=%r", self.id, fraga)
            return []

        utkast: list[Faktautkast] = []
        for notice in notices:
            pub_nr = _plocka_text(notice.get("publication-number") or "")
            pub_date = _plocka_text(notice.get("publication-date") or "")
            titel = _plocka_text(notice.get("notice-title") or "")
            kopare = _plocka_text(notice.get("buyer-name") or "")
            stad = _plocka_text(notice.get("buyer-city") or "")
            varde = _plocka_text(notice.get("tender-value") or "")
            valuta = _plocka_text(notice.get("tender-value-cur") or "")

            if not pub_nr:
                continue

            # Visa kontraktsvärde om det finns, annars typen
            varde_str = f"{varde} {valuta}".strip() if varde else "ej angivet"
            kopare_str = f"{kopare}, {stad}".strip(", ") if kopare else "okänd köpare"

            manniska = self._kalla.manniskolank_mall or ""
            if "{publication_number}" in manniska:
                manniska = manniska.format(publication_number=pub_nr)

            utkast.append(Faktautkast(
                etikett=f"TED-meddelande {pub_nr}: {titel or 'utan titel'}",
                varde=f"Köpare: {kopare_str} | Kontraktsvärde: {varde_str}",
                period=pub_date or None,
                kalla_id=self.id,
                myndighet=self._kalla.myndighet or "TED",
                licens=self._kalla.licens,
                attribution=self._kalla.attribution,
                dataset=pub_nr,
                lank_manniska=manniska or self._kalla.bas_url,
                lank_maskin=url,
            ))

        return utkast
