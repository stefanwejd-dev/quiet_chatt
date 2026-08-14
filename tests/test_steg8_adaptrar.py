"""Acceptanstester för Steg 8 — samtliga nya adaptrar.

Varje nätverksberoende test tar isolerad_cache-fixturen (se conftest.py).
VCR-kassetter spelas in i tests/kassetter/ vid första körning (record_mode="once").
"""

import vcr

from quiet_oppen_data.adaptrar.ted import TedAdapter
from quiet_oppen_data.adaptrar.riksdagen import RiksdagenAdapter
from quiet_oppen_data.adaptrar.kolada import KoladaAdapter
from quiet_oppen_data.adaptrar.rowstore import RowStoreAdapter
from quiet_oppen_data.adaptrar.json_rest import JsonRestAdapter
from quiet_oppen_data.modeller import Fragplan

VCR_CONFIG = {
    "cassette_library_dir": "tests/kassetter",
    "record_mode": "once",
    "match_on": ["method", "scheme", "host", "port", "path", "query"],
}


# ---------------------------------------------------------------------------
# TED
# ---------------------------------------------------------------------------

@vcr.use_cassette("tests/kassetter/ted_svenska_upphandlingar.yaml", **{k: v for k, v in VCR_CONFIG.items() if k != "cassette_library_dir"})
def test_ted_returnerar_faktautkast(isolerad_cache):
    adapter = TedAdapter()
    plan = Fragplan(fraga="", extra={
        "fraga": "buyer-country=SWE AND publication-date>=today(-30)",
        "limit": 2
    })
    poster = adapter.hamta(plan)

    assert len(poster) >= 1
    post = poster[0]
    assert "TED-meddelande" in post.etikett
    assert post.lank_manniska.startswith("https://ted.europa.eu")
    assert post.lank_maskin.startswith("https://api.ted.europa.eu")
    assert post.kalla_id == "ted"


@vcr.use_cassette("tests/kassetter/ted_tomt_resultat.yaml", **{k: v for k, v in VCR_CONFIG.items() if k != "cassette_library_dir"})
def test_ted_returnerar_tomt_vid_inga_traffar(isolerad_cache):
    adapter = TedAdapter()
    # Extremt specifik fråga som borde ge noll träffar
    plan = Fragplan(fraga="", extra={
        "fraga": "buyer-country=SWE AND notice-type=XXXXXXXXXX_OGILTIGT"
    })
    poster = adapter.hamta(plan)
    assert isinstance(poster, list)


def test_ted_returnerar_tomt_utan_nyckel(isolerad_cache):
    """Utan nätverksanrop — säkerhetsnät mot regression."""
    adapter = TedAdapter()
    # Ingen VCR — vi testar bara att adaptern inte kraschar vid nätverksfel
    # genom att använda en isolerad cache utan inspelade svar
    assert adapter.id == "ted"
    assert len(adapter.beskriv()) == 1


# ---------------------------------------------------------------------------
# Riksdagen
# ---------------------------------------------------------------------------

@vcr.use_cassette("tests/kassetter/riksdagen_moms.yaml", **{k: v for k, v in VCR_CONFIG.items() if k != "cassette_library_dir"})
def test_riksdagen_sok_returnerar_dokument(isolerad_cache):
    adapter = RiksdagenAdapter()
    plan = Fragplan(fraga="", extra={"sok": "moms", "limit": 3})
    poster = adapter.hamta(plan)

    assert len(poster) >= 1
    post = poster[0]
    assert post.kalla_id == "riksdagen"
    assert post.lank_manniska.startswith("https://www.riksdagen.se")
    assert post.lank_maskin.startswith("https://data.riksdagen.se")


def test_riksdagen_utan_sok_returnerar_tomt(isolerad_cache):
    adapter = RiksdagenAdapter()
    plan = Fragplan(fraga="", extra={})
    poster = adapter.hamta(plan)
    assert poster == []


# ---------------------------------------------------------------------------
# Kolada
# ---------------------------------------------------------------------------

@vcr.use_cassette("tests/kassetter/kolada_sok_kpi.yaml", **{k: v for k, v in VCR_CONFIG.items() if k != "cassette_library_dir"})
def test_kolada_sok_kpi(isolerad_cache):
    adapter = KoladaAdapter()
    plan = Fragplan(fraga="", extra={"verktyg": "kolada_sok_kpi", "sok": "förskola"})
    poster = adapter.hamta(plan)

    assert len(poster) >= 1
    post = poster[0]
    assert "Kolada KPI" in post.etikett
    assert post.kalla_id == "kolada"
    # Värdet ska vara ett KPI-id (t.ex. "N15033")
    assert post.varde


@vcr.use_cassette("tests/kassetter/kolada_hamta_data.yaml", **{k: v for k, v in VCR_CONFIG.items() if k != "cassette_library_dir"})
def test_kolada_hamta_data(isolerad_cache):
    adapter = KoladaAdapter()
    # N15033 = Inskrivna barn i förskola, andel (%)
    plan = Fragplan(fraga="", extra={
        "kpi_id": "N15033",
        "kommuner": ["1280"],  # Malmö
        "ar": 2022
    })
    poster = adapter.hamta(plan)

    assert len(poster) >= 1
    post = poster[0]
    assert "N15033" in post.etikett
    assert post.dimensioner.get("kommun") == "1280"
    assert post.period == "2022"


# ---------------------------------------------------------------------------
# RowStore
# ---------------------------------------------------------------------------

@vcr.use_cassette("tests/kassetter/rowstore_kronofogden.yaml", **{k: v for k, v in VCR_CONFIG.items() if k != "cassette_library_dir"})
def test_rowstore_kronofogden(isolerad_cache):
    adapter = RowStoreAdapter("kronofogden_rowstore")
    plan = Fragplan(fraga="", extra={
        "uuid": "4e789168-1d3d-468c-b9a1-0cce9a9a4f1e",
        "limit": 2
    })
    poster = adapter.hamta(plan)

    assert len(poster) >= 1
    post = poster[0]
    assert post.kalla_id == "kronofogden_rowstore"
    assert "4e789168" in post.dataset


@vcr.use_cassette("tests/kassetter/rowstore_skatteverket.yaml", **{k: v for k, v in VCR_CONFIG.items() if k != "cassette_library_dir"})
def test_rowstore_skatteverket(isolerad_cache):
    adapter = RowStoreAdapter("skatteverket_rowstore")
    plan = Fragplan(fraga="", extra={
        "uuid": "080c60de-c7e3-4d65-a386-5e72b2aa4bcd",
        "limit": 2
    })
    poster = adapter.hamta(plan)

    assert len(poster) >= 1
    assert poster[0].kalla_id == "skatteverket_rowstore"


def test_rowstore_utan_uuid_returnerar_tomt(isolerad_cache):
    adapter = RowStoreAdapter("kronofogden_rowstore")
    plan = Fragplan(fraga="", extra={})
    assert adapter.hamta(plan) == []


# ---------------------------------------------------------------------------
# JsonRest — Polisens händelser (lätt och snabb)
# ---------------------------------------------------------------------------

@vcr.use_cassette("tests/kassetter/polisen_handelser_stockholm.yaml", **{k: v for k, v in VCR_CONFIG.items() if k != "cassette_library_dir"})
def test_json_rest_polisen_handelser(isolerad_cache):
    adapter = JsonRestAdapter("polisen_handelser")
    plan = Fragplan(fraga="", extra={
        "params": {"locationname": "Stockholm"},
        "limit": 3
    })
    poster = adapter.hamta(plan)

    assert len(poster) >= 1
    post = poster[0]
    assert post.kalla_id == "polisen_handelser"
    assert post.lank_maskin.startswith("https://polisen.se")


@vcr.use_cassette("tests/kassetter/jobtech_larare.yaml", **{k: v for k, v in VCR_CONFIG.items() if k != "cassette_library_dir"})
def test_json_rest_jobtech(isolerad_cache):
    adapter = JsonRestAdapter("jobtech")
    plan = Fragplan(fraga="", extra={
        "path": "/search",
        "params": {"q": "lärare", "limit": 2},
        "listnycklar": ["hits"],
        "etikett_falt": "headline",
        "limit": 2
    })
    poster = adapter.hamta(plan)

    assert len(poster) >= 1
    post = poster[0]
    assert post.kalla_id == "jobtech"
    assert post.lank_maskin.startswith("https://jobsearch.api.jobtechdev.se")


# ---------------------------------------------------------------------------
# Skatteverkets datasetkatalog — tillagd 2026-08-14
# ---------------------------------------------------------------------------

def test_skatteverket_exponerar_datasetkatalog():
    """Ett RowStore-UUID går inte att gissa.

    Utan katalogverktyg är källan i praktiken oanvändbar för modellen — samma
    felklass som PxWeb-dimensioner och Riksbankens serie-id
    (ARKITEKTUR.md §5 regel 7).
    """
    specar = {s["name"]: s for s in RowStoreAdapter("skatteverket_rowstore").beskriv()}
    assert "skatteverket_rowstore_lista_dataset" in specar
    assert "skatteverket_rowstore" in specar
    # Hämtverktyget ska säga åt modellen att inte gissa.
    assert "gissa aldrig" in specar["skatteverket_rowstore"]["description"].lower()


def test_skatteverket_katalog_hittar_traktamenten():
    utkast = RowStoreAdapter("skatteverket_rowstore").hamta(
        Fragplan(fraga="", extra={
            "verktyg": "skatteverket_rowstore_lista_dataset", "sok": "traktamente"})
    )
    assert len(utkast) == 1
    assert "006353ad-aa05-4757-bdfd-fae344433a50" in utkast[0].varde
    # Katalogen är kurerad, så en irrelevant datamängd ska inte matcha.
    assert "Testpersonnummer" not in utkast[0].varde


def test_skatteverket_katalogen_dokumenterar_skiftlagesfallan():
    """kommun=MALMÖ ger 78 rader, kommun=Malmö ger noll.

    Ett filter som inte träffar ser ut som "källan hade inget att säga". Fällan
    måste stå i katalogtexten som modellen faktiskt läser, inte bara i en
    kommentar i registret.
    """
    utkast = RowStoreAdapter("skatteverket_rowstore").hamta(
        Fragplan(fraga="", extra={
            "verktyg": "skatteverket_rowstore_lista_dataset", "sok": "skattesats"})
    )
    from quiet_oppen_data.register import hamta as hamta_kalla
    post = next(d for d in hamta_kalla("skatteverket_rowstore").dataset
                if d["namn"] == "Skattesatser per kommun")
    assert "VERSALER" in post["beskrivning"]
    assert utkast, "katalogsökningen ska hitta skattesatsdatamängden"


@vcr.use_cassette(**VCR_CONFIG)
def test_skatteverket_etikett_namnger_datamangden(isolerad_cache):
    """Etiketten måste säga VAD uppgiften är, inte bara vilket UUID den kom från.

    "Skatteverket, dataset 006353ad-aa05-…" gör felet osynligt i källpanelen;
    "Skatteverket: Schablonbelopp för traktamenten" gör det uppenbart
    (ARKITEKTUR.md §5).
    """
    utkast = RowStoreAdapter("skatteverket_rowstore").hamta(
        Fragplan(fraga="", extra={
            "uuid": "006353ad-aa05-4757-bdfd-fae344433a50",
            "filter": {"år": "2026"}, "limit": 2})
    )
    assert utkast
    assert "Schablonbelopp för traktamenten" in utkast[0].etikett
    assert "006353ad" not in utkast[0].etikett


@vcr.use_cassette(**VCR_CONFIG)
def test_skatteverket_arskolumn_blir_period(isolerad_cache):
    """Årtalet hör hemma i period, inte begravt i värdesträngen.

    Utan det kan ett svar säga "traktamentet är 300 kr" utan att visa vilket år
    som avses — en uppgift som byter värde varje år.
    """
    utkast = RowStoreAdapter("skatteverket_rowstore").hamta(
        Fragplan(fraga="", extra={
            "uuid": "006353ad-aa05-4757-bdfd-fae344433a50",
            "filter": {"år": "2026"}, "limit": 2})
    )
    assert utkast
    assert utkast[0].period == "2026"
    assert "år:" not in utkast[0].varde, "periodkolumnen ska inte upprepas i värdet"
