import logging
import re
from typing import Any
from urllib.parse import urlencode

from quiet_oppen_data.adaptrar.transport import hamta_json
from quiet_oppen_data.index.ingest import (
    DC_DESCRIPTION,
    DC_PUBLISHER,
    DC_TITLE,
    bygg_manniskolank,
    hamta_entry_och_resurs,
    hamta_text,
    hamta_utgivare,
)
from quiet_oppen_data.modeller import Faktautkast, Fragplan
from quiet_oppen_data.register import Kalla, hamta

logger = logging.getLogger(__name__)

# dataportal.se namnger utgivare som "http://dataportal.se/organisation/SE<orgnr>"
# när utgivarens namn inte är utskrivet i sökträffen. Numret är ett vanligt
# svenskt organisationsnummer — samma sorts identitetsbeteckning Bolagsverkets
# HVD-API tar emot. Se _slå_upp_myndighetsnamn.
_ORGANISATION_URI_MONSTER = re.compile(r"^http://dataportal\.se/organisation/SE(\d{10})$")


def _slå_upp_myndighetsnamn(orgnr: str) -> str | None:
    """Slår upp ett läsbart organisationsnamn för en dataportal-utgivarkod.

    Bäst-ansträngning: om Bolagsverket-källan är spärrad, avstängd, eller
    anropet av någon annan anledning misslyckas, returneras None och
    anroparen faller tillbaka på den opaka koden i stället för att låta hela
    dataportal-sökningen krascha eller stanna upp på en enskild utgivare.
    """
    k = hamta("bolagsverket_hvd")
    if not isinstance(k, Kalla) or not k.aktiverad or not k.bas_url:
        return None

    try:
        from quiet_oppen_data.adaptrar.bolagsverket import hamta_token

        token = hamta_token(k)
        res = hamta_json(
            k.id, "POST", f"{k.bas_url}/organisationer",
            headers={"Authorization": f"Bearer {token}"},
            json={"identitetsbeteckning": orgnr},
        )
    except Exception:
        logger.info("Kunde inte slå upp myndighetsnamn för orgnr %s", orgnr, exc_info=True)
        return None

    organisationer = (res or {}).get("organisationer") or []
    if not organisationer:
        return None

    namn_container = organisationer[0].get("organisationsnamn") or {}
    if namn_container.get("fel"):
        return None

    lista = namn_container.get("organisationsnamnLista") or []
    if lista and lista[0].get("namn"):
        return lista[0]["namn"]
    return None


def _bestam_utgivare(metadata: dict, alla_metadata: dict) -> str:
    """Utgivarens namn i läsbar form.

    Ordning: literalt värde i svaret → uppslag via Bolagsverket om utgivaren
    är kodad som ett svenskt organisationsnummer → ingest.py:s befintliga,
    redan testade fallback (label i samma svar, annars sista URI-segmentet).
    """
    for v in metadata.get(DC_PUBLISHER, []):
        if v.get("type", "literal") != "uri" and v.get("value"):
            return v["value"]

    for v in metadata.get(DC_PUBLISHER, []):
        pub_uri = (v.get("value") or "").strip()
        match = _ORGANISATION_URI_MONSTER.match(pub_uri) if pub_uri else None
        if match:
            namn = _slå_upp_myndighetsnamn(match.group(1))
            if namn:
                return namn

    return hamta_utgivare(metadata, alla_metadata) or ""


class DataportalAdapter:
    """Adapter för Sveriges dataportal (discovery-lager och nivå-3-fallback).

    När ingen annan adapter kan exekvera mot en datamängd kan denna adapter
    returnera *metadata* om var uppgiften finns som Faktaposter — boten svarar
    då «det finns hos Boverket, här är datamängden» i stället för det faktiska
    värdet. Det är ett giltigt svar, inte ett fel (ARKITEKTUR.md §3.3).
    """

    def __init__(self) -> None:
        k = hamta("dataportal")
        if not isinstance(k, Kalla):
            raise RuntimeError("Dataportal-källan saknas eller är blockerad i registret.")
        self._kalla = k

    @property
    def id(self) -> str:
        return self._kalla.id

    def beskriv(self) -> list[dict[str, Any]]:
        return [{
            "name": self.id,
            "description": (
                "Söker i Sveriges dataportal (23 000+ datamängder från 155+ myndigheter). "
                "Returnerar metadata om var data finns — inte själva värdena. "
                "Använd detta som sista utväg när ingen specifik adapter kan svara."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "sok": {
                        "type": "string",
                        "description": "Sökterm, t.ex. 'vindkraft', 'bostadspriser', 'miljötillstånd'"
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

        limit = min(int(plan.extra.get("limit") or 5), 10)

        # Dataportalens sök-API (EntryScape, inte ren Solr) tar limit/offset —
        # inte rows/start. Fel parameternamn tystas till standardsidstorleken
        # i stället för att ge ett fel, vilket gjorde bugg nr 1 osynlig.
        query = f"rdfType:http\\://www.w3.org/ns/dcat\\#Dataset AND public:true AND (*{sok}*)"
        params: dict[str, Any] = {
            "type": "solr",
            "query": query,
            "limit": limit,
            "offset": 0,
        }

        url = self._kalla.bas_url

        try:
            res = hamta_json(self.id, "GET", url, params=params)
        except Exception:
            logger.warning("%s: sökning misslyckades (sok=%r)", self.id, sok, exc_info=True)
            return []

        # Svaret är en EntryScape-resursgraf: {"resource": {"children": [...]}},
        # inte Solr-formen {"hits": {"hits": [...]}}. Samma format som
        # index/ingest.py redan tolkar korrekt — återanvänd den logiken i
        # stället för att upprepa den (och riskera att den driver isär igen).
        try:
            children = (res or {}).get("resource", {}).get("children") or []
        except AttributeError:
            logger.warning("%s: oväntat svarsformat", self.id)
            return []

        if not children:
            logger.info("%s: inga träffar för sok=%r", self.id, sok)
            return []

        utkast: list[Faktautkast] = []
        for child in children:
            parsad = hamta_entry_och_resurs(child)
            if not parsad:
                continue
            entry_url, resurs_uri = parsad

            alla_metadata: dict = child.get("metadata", {})
            metadata: dict = alla_metadata.get(resurs_uri, {})
            if not metadata:
                continue

            titel = hamta_text(metadata, DC_TITLE) or ""
            beskrivning = hamta_text(metadata, DC_DESCRIPTION) or ""
            utgivare = _bestam_utgivare(metadata, alla_metadata)
            manniska = bygg_manniskolank(entry_url) or f"https://www.dataportal.se/datasets/{resurs_uri}"
            maskin = f"{url}?{urlencode({'type': 'solr', 'query': f'id:{resurs_uri}'})}"

            utkast.append(Faktautkast(
                etikett=f"Dataportal: {titel or resurs_uri}",
                varde=beskrivning[:300] if beskrivning else "Ingen beskrivning tillgänglig",
                kalla_id=self.id,
                myndighet=utgivare or (self._kalla.myndighet or ""),
                licens=self._kalla.licens,
                attribution=self._kalla.attribution,
                dataset=resurs_uri,
                dimensioner={"utgivare": utgivare} if utgivare else {},
                lank_manniska=manniska,
                lank_maskin=maskin,
            ))

        return utkast
