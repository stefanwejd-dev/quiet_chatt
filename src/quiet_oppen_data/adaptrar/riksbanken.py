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
        return [
            {
                "name": "riksbanken_lista_serier",
                "description": (
                    "Listar Riksbankens tillgängliga serier med id och beskrivning. "
                    "Anropa ALLTID detta först — serie-id går inte att gissa. "
                    "Filtrera med sok, t.ex. sok='reference' eller sok='EUR'."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "sok": {
                            "type": "string",
                            "description": (
                                "Fritext som matchas mot seriens beskrivning. "
                                "Beskrivningarna är på engelska: 'reference rate', "
                                "'policy rate', 'exchange rate'."
                            ),
                        }
                    },
                    "required": [],
                },
            },
            {
                "name": "riksbanken_hamta",
                "description": (
                    "Hämtar senaste observation för en serie. Serie-id MÅSTE komma "
                    "från riksbanken_lista_serier — gissa aldrig ett id."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "serie": {
                            "type": "string",
                            "description": "Serie-id ur riksbanken_lista_serier, t.ex. SECBREFEFF",
                        }
                    },
                    "required": ["serie"],
                },
            },
        ]

    # ------------------------------------------------------------------
    # Seriekatalog
    # ------------------------------------------------------------------

    def _serier(self) -> list[dict]:
        """Hämtar Riksbankens seriekatalog (117 serier). Cachas av transportlagret."""
        try:
            res = hamta_json(self.id, "GET", f"{self._kalla.bas_url}/Series")
        except Exception:
            logger.warning("%s: kunde inte hämta seriekatalogen", self.id, exc_info=True)
            return []
        return res if isinstance(res, list) else []

    def _lista_serier(self, sok: str | None) -> list[Faktautkast]:
        """Returnerar matchande serier som utkast, så modellen kan välja rätt.

        Utan det här steget måste modellen gissa serie-id. Vid provkörning
        2026-08-13 gissade den nio gånger, fick 429 från Riksbanken, och landade
        på SECBREPOEFF (styrränta) när frågan gällde referensräntan SECBREFEFF —
        ett tecken isär, helt annan ränta. Ett trovärdigt tal som är fel är
        precis vad §5 regel 7 finns för att hindra.
        """
        serier = self._serier()
        if not serier:
            return []

        if sok:
            nal = sok.lower()
            serier = [
                s for s in serier
                if nal in (s.get("shortDescription") or "").lower()
                or nal in (s.get("midDescription") or "").lower()
                or nal in (s.get("seriesId") or "").lower()
            ]
        if not serier:
            logger.info("%s: ingen serie matchade sok=%r", self.id, sok)
            return []

        rader = [
            f"{s['seriesId']} ({s.get('shortDescription') or '?'})"
            for s in serier[:60]
        ]
        return [
            Faktautkast(
                etikett=f"Riksbankens serier som matchar {sok!r}" if sok
                        else "Riksbankens tillgängliga serier",
                varde=", ".join(rader),
                kalla_id=self.id,
                myndighet=self._kalla.myndighet or "Sveriges riksbank",
                licens=self._kalla.licens,
                attribution=self._kalla.attribution,
                lank_manniska=self._kalla.manniskolank_mall or self._kalla.bas_url,
                lank_maskin=f"{self._kalla.bas_url}/Series",
            )
        ]

    def _beskrivning(self, serie: str) -> str | None:
        for s in self._serier():
            if s.get("seriesId") == serie:
                return s.get("shortDescription") or s.get("midDescription")
        return None

    # ------------------------------------------------------------------
    # Enhet
    # ------------------------------------------------------------------

    def _gruppvag(self) -> dict[int, list[str]]:
        """Bygger {groupId: [namn på alla förfäder + eget namn]} ur /Groups.

        SWEA anger ingen enhet per serie. Men gruppträdet skiljer räntor från
        valutakurser, och den strukturen kommer från API:et självt — vi
        härleder ur den i stället för att gissa på seriens namn.
        """
        try:
            rot = hamta_json(self.id, "GET", f"{self._kalla.bas_url}/Groups")
        except Exception:
            logger.warning("%s: kunde inte hämta gruppträdet", self.id, exc_info=True)
            return {}

        vagar: dict[int, list[str]] = {}

        def ga(nod: dict, forfader: list[str]) -> None:
            namn = forfader + [nod.get("name") or ""]
            gid = nod.get("groupId")
            if gid is not None:
                vagar[gid] = namn
            for barn in nod.get("childGroups") or []:
                ga(barn, namn)

        for nod in (rot if isinstance(rot, list) else [rot]):
            ga(nod, [])
        return vagar

    def _enhet(self, serie: str) -> str | None:
        """Enhet för en serie, härledd ur gruppträdet. None när det är oklart.

        Utan enhet blir svaret "referensräntan ligger på 2" — vilket är
        tvetydigt. Vi gissar dock inte: hittar vi ingen grupp lämnas fältet
        tomt, och syntesmodellen påpekar då själv att sorten saknas.
        """
        post = next((s for s in self._serier() if s.get("seriesId") == serie), None)
        if post is None:
            return None

        vag = self._gruppvag().get(post.get("groupId"))
        if not vag:
            return None
        # Rotnoden heter "Interest rates and exchange rates" och matchar därmed
        # båda grenarna. Den bär ingen information — vi läser grenen under den.
        text = " / ".join(vag[1:] or vag).lower()

        if "exchange rate" in text or "currencies against" in text:
            valuta = (post.get("shortDescription") or "").strip()
            return f"SEK per {valuta}" if valuta else "SEK"
        if "interest rate" in text or "rates" in text:
            return "procent"
        return None

    def hamta(self, plan: Fragplan) -> list[Faktautkast]:
        if plan.extra.get("verktyg") == "riksbanken_lista_serier":
            return self._lista_serier(plan.extra.get("sok"))

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

        # Etiketten måste säga VAD serien är. "Riksbanken, serie SECBREPOEFF"
        # gör det omöjligt för läsaren i källpanelen att se att det är
        # styrräntan och inte referensräntan.
        beskrivning = self._beskrivning(serie)
        etikett = (
            f"Riksbanken: {beskrivning} ({serie})" if beskrivning
            else f"Riksbanken, serie {serie}"
        )

        return [
            Faktautkast(
                etikett=etikett,
                enhet=self._enhet(serie),
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
