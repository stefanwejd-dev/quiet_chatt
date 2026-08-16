"""Acceptanstester för Steg 7 (återupptaget en tredje gång) — DataportalAdapter.

Bakgrund: adaptern hade två buggar sedan den skrevs, upptäckta 2026-08-16 när
en driftsatt sökning efter "luftkvalitet" tyst gav noll träffar trots att
katalogen har 23 000+ datamängder:

  1. Fel parameternamn mot API:et — rows/start i stället för limit/offset.
     API:et (EntryScape, inte ren Solr) ignorerar okända parametrar tyst och
     svarar med sin standardsida, så felet syntes aldrig som ett fel.
  2. Fel antaget svarsformat — hits.hits[] (Solr-konvention) i stället för
     resource.children[] (EntryScape-resursgraf, samma format som
     index/ingest.py redan tolkar korrekt för katalogingesten).

Ingen tidigare test anropade adapterns hamta() mot ett realistiskt svar, så
bugen syntes aldrig i testsviten. Fixturen nedan är trimmad ur ett riktigt
svar från https://admin.dataportal.se/store/search 2026-08-16.
"""

import httpx

from quiet_oppen_data.adaptrar import bolagsverket as bv
from quiet_oppen_data.adaptrar.dataportal import DataportalAdapter
from quiet_oppen_data.modeller import Faktaregister, Faktautkast, Fragplan

_RESURS_URI = "https://opendata.umea.se/api/v2/catalog/datasets/luftkvalitetsmaetningar-vaestra-esplanaden"
_ENTRY_URL = "https://admin.dataportal.se/store/43/entry/69395"

# Trimmad, men strukturellt äkta EntryScape-resursgraf — samma form som
# index/ingest.py:behandla_dataset() förväntar sig.
_SVAR_MED_TRAFF = {
    "offset": 0,
    "resource": {
        "children": [
            {
                "entryId": "69395",
                "contextId": "43",
                "info": {
                    _ENTRY_URL: {
                        "http://entrystore.org/terms/resource": [
                            {"type": "uri", "value": _RESURS_URI}
                        ],
                    }
                },
                "metadata": {
                    _RESURS_URI: {
                        "http://purl.org/dc/terms/title": [
                            {"type": "literal", "lang": "sv",
                             "value": "Luftkvalitetsmätningar Västra esplanaden"}
                        ],
                        "http://purl.org/dc/terms/description": [
                            {"type": "literal", "lang": "sv",
                             "value": "Mätdata från kontinuerliga luftmätningar i centrala Umeå."}
                        ],
                        "http://purl.org/dc/terms/publisher": [
                            {"type": "literal", "value": "Umeå kommun"}
                        ],
                    }
                },
            }
        ]
    },
}

_SVAR_TOMT = {"offset": 0, "resource": {"children": []}}


def _mocka_svar(monkeypatch, svar, forvantade_params=None):
    def mock_request(self, method, url, **kwargs):
        if forvantade_params is not None:
            assert kwargs.get("params") == forvantade_params
        return httpx.Response(200, json=svar, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx.Client, "request", mock_request)


def test_hamta_anvander_limit_offset_inte_rows_start(monkeypatch, isolerad_cache):
    """Bugg 1: API:et vill ha limit/offset, inte rows/start."""
    _mocka_svar(
        monkeypatch, _SVAR_MED_TRAFF,
        forvantade_params={
            "type": "solr",
            "query": "rdfType:http\\://www.w3.org/ns/dcat\\#Dataset AND public:true AND (*luftkvalitet*)",
            "limit": 5,
            "offset": 0,
        },
    )
    adapter = DataportalAdapter()
    utkast = adapter.hamta(Fragplan(fraga="", extra={"sok": "luftkvalitet"}))
    assert len(utkast) == 1


def test_hamta_tolkar_resursgrafen_inte_solr_hits(monkeypatch, isolerad_cache):
    """Bugg 2: svaret är resource.children[], inte hits.hits[]."""
    _mocka_svar(monkeypatch, _SVAR_MED_TRAFF)
    adapter = DataportalAdapter()

    utkast = adapter.hamta(Fragplan(fraga="", extra={"sok": "luftkvalitet"}))

    assert len(utkast) == 1
    u = utkast[0]
    assert isinstance(u, Faktautkast)
    assert u.kalla_id == "dataportal"
    assert "Luftkvalitetsmätningar Västra esplanaden" in u.etikett
    assert "Mätdata från kontinuerliga luftmätningar" in u.varde
    assert u.myndighet == "Umeå kommun"
    assert u.dataset == _RESURS_URI
    assert u.lank_manniska == "https://www.dataportal.se/datasets/43_69395"

    # Utkastet ska passera Faktaregistrets validering precis som andra adaptrar.
    reg = Faktaregister()
    poster = reg.registrera_alla(utkast)
    assert len(poster) == 1
    assert poster[0].lank_manniska and poster[0].lank_maskin


def test_hamta_utan_traffar_ger_tom_lista(monkeypatch, isolerad_cache):
    _mocka_svar(monkeypatch, _SVAR_TOMT)
    adapter = DataportalAdapter()
    utkast = adapter.hamta(Fragplan(fraga="", extra={"sok": "finns-inte-alls-xyz"}))
    assert utkast == []


def test_hamta_utan_sokstrang_ger_tom_lista(isolerad_cache):
    adapter = DataportalAdapter()
    assert adapter.hamta(Fragplan(fraga="", extra={})) == []


# ---------------------------------------------------------------------------
# Myndighetsnamn: dataportalen kodar ibland utgivaren som ett organisations-
# nummer ("http://dataportal.se/organisation/SE...") i stället för ett namn.
# _bestam_utgivare slår då upp det riktiga namnet via Bolagsverket.
# ---------------------------------------------------------------------------

_SVAR_MED_KODAD_UTGIVARE = {
    "offset": 0,
    "resource": {
        "children": [
            {
                "entryId": "5441",
                "contextId": "78",
                "info": {
                    "https://admin.dataportal.se/store/78/entry/5441": {
                        "http://entrystore.org/terms/resource": [
                            {"type": "uri", "value": _RESURS_URI}
                        ],
                    }
                },
                "metadata": {
                    _RESURS_URI: {
                        "http://purl.org/dc/terms/title": [
                            {"type": "literal", "lang": "sv", "value": "Luftmiljö - Modelldata"}
                        ],
                        "http://purl.org/dc/terms/description": [
                            {"type": "literal", "lang": "sv", "value": "Årsmedelhalter av luftföroreningar."}
                        ],
                        "http://purl.org/dc/terms/publisher": [
                            {"type": "uri", "value": "http://dataportal.se/organisation/SE2021000696"}
                        ],
                    }
                },
            }
        ]
    },
}

_ORGSVAR_SMHI = {
    "organisationer": [{
        "organisationsnamn": {
            "dataproducent": "SCB",
            "fel": None,
            "organisationsnamnLista": [
                {"namn": "SVERIGES METEOROLOGISKA OCH HYDROLOGISKA INSTITUT"}
            ],
        },
    }]
}


def _mocka_sok_och_bolagsverket(monkeypatch, orgsvar):
    monkeypatch.setattr(bv, "hamta_token", lambda kalla: "fejktoken")

    def mock_request(self, method, url, **kwargs):
        if "bolagsverket" in url or url.endswith("/organisationer"):
            return httpx.Response(200, json=orgsvar, request=httpx.Request(method, url))
        return httpx.Response(200, json=_SVAR_MED_KODAD_UTGIVARE, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx.Client, "request", mock_request)


def test_bestam_utgivare_slar_upp_myndighetsnamn_via_bolagsverket(monkeypatch, isolerad_cache):
    _mocka_sok_och_bolagsverket(monkeypatch, _ORGSVAR_SMHI)
    adapter = DataportalAdapter()

    utkast = adapter.hamta(Fragplan(fraga="", extra={"sok": "luftmiljö"}))

    assert len(utkast) == 1
    assert utkast[0].myndighet == "SVERIGES METEOROLOGISKA OCH HYDROLOGISKA INSTITUT"


def test_bestam_utgivare_faller_tillbaka_pa_koden_om_bolagsverket_fel(monkeypatch, isolerad_cache):
    """Bolagsverket-uppslaget är bäst-ansträngning — ett fel där ska aldrig
    tömma eller krascha hela dataportal-sökningen."""
    def hamta_token_kastar(kalla):
        raise RuntimeError("simulerat fel")
    monkeypatch.setattr(bv, "hamta_token", hamta_token_kastar)

    def mock_request(self, method, url, **kwargs):
        return httpx.Response(200, json=_SVAR_MED_KODAD_UTGIVARE, request=httpx.Request(method, url))
    monkeypatch.setattr(httpx.Client, "request", mock_request)

    adapter = DataportalAdapter()
    utkast = adapter.hamta(Fragplan(fraga="", extra={"sok": "luftmiljö"}))

    assert len(utkast) == 1
    # Faller tillbaka på sista segmentet av utgivar-URI:n (ingest.py:s
    # befintliga logik) — inte tomt, inte kraschat.
    assert utkast[0].myndighet == "SE2021000696"


# ---------------------------------------------------------------------------
# Utgivare som är en post i dataportalens EGEN databas (foaf:Agent), inte ett
# organisationsnummer — t.ex. Umeå kommuns källa 2026-08-16.
# ---------------------------------------------------------------------------

_AGENT_URI = "https://admin.dataportal.se/store/43/resource/7a00bd3796ed09a600646432cb321722"

_SVAR_MED_ENTRYSTORE_UTGIVARE = {
    "offset": 0,
    "resource": {
        "children": [
            {
                "entryId": "69395",
                "contextId": "43",
                "info": {
                    _ENTRY_URL: {
                        "http://entrystore.org/terms/resource": [
                            {"type": "uri", "value": _RESURS_URI}
                        ],
                    }
                },
                "metadata": {
                    _RESURS_URI: {
                        "http://purl.org/dc/terms/title": [
                            {"type": "literal", "lang": "sv", "value": "Luftkvalitetsmätningar"}
                        ],
                        "http://purl.org/dc/terms/description": [
                            {"type": "literal", "lang": "sv", "value": "Mätdata från Umeå."}
                        ],
                        "http://purl.org/dc/terms/publisher": [
                            {"type": "uri", "value": _AGENT_URI}
                        ],
                    }
                },
            }
        ]
    },
}

_AGENT_METADATA_SVAR = {
    _AGENT_URI: {
        "http://xmlns.com/foaf/0.1/name": [{"type": "literal", "value": "Umeå kommun"}],
    }
}


def test_bestam_utgivare_slar_upp_entrystore_agent(monkeypatch, isolerad_cache):
    """Umeås utgivarkod är ingen extern identifierare — den är en post i
    dataportalens egen databas, uppslagen via samma värds /metadata/-ändpunkt."""
    def mock_request(self, method, url, **kwargs):
        if "/metadata/" in url:
            return httpx.Response(200, json=_AGENT_METADATA_SVAR, request=httpx.Request(method, url))
        return httpx.Response(200, json=_SVAR_MED_ENTRYSTORE_UTGIVARE, request=httpx.Request(method, url))
    monkeypatch.setattr(httpx.Client, "request", mock_request)

    adapter = DataportalAdapter()
    utkast = adapter.hamta(Fragplan(fraga="", extra={"sok": "luftkvalitet"}))

    assert len(utkast) == 1
    assert utkast[0].myndighet == "Umeå kommun"


def test_bestam_utgivare_entrystore_uppslag_fel_faller_tillbaka(monkeypatch, isolerad_cache):
    """Samma bäst-ansträngning som Bolagsverket-uppslaget: ett fel här ska
    aldrig krascha eller tömma sökningen."""
    def mock_request(self, method, url, **kwargs):
        if "/metadata/" in url:
            # Ett icke-transportfel (t.ex. ett oväntat svarsformat) ger inga
            # omförsök i transportlagret — testet ska inte behöva vänta ut
            # _anropa_med_omforsoks backoff för att verifiera fallbacken.
            raise ValueError("simulerat fel")
        return httpx.Response(200, json=_SVAR_MED_ENTRYSTORE_UTGIVARE, request=httpx.Request(method, url))
    monkeypatch.setattr(httpx.Client, "request", mock_request)

    adapter = DataportalAdapter()
    utkast = adapter.hamta(Fragplan(fraga="", extra={"sok": "luftkvalitet"}))

    assert len(utkast) == 1
    # Faller tillbaka på sista URI-segmentet (den opaka koden) — inte tomt.
    assert utkast[0].myndighet == "7a00bd3796ed09a600646432cb321722"
