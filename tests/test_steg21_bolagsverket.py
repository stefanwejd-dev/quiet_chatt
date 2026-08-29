"""Acceptanstester för Steg 7 (återupptaget igen) — Bolagsverket HVD.

Källan kräver OAuth2 client_credentials, vilket ingen annan källa i registret
gör. Testerna mockar httpx direkt (samma mönster som test_steg5_transport.py)
i stället för VCR-kassetter — en cassette skulle annars behöva scrubbas för
Authorization-headern och access_token i svaret, och risken att glömma det är
inte värt vinsten här.
"""

import httpx
import pytest

from quiet_oppen_data.adaptrar import bolagsverket as bv
from quiet_oppen_data.adaptrar.bolagsverket import BolagsverketAdapter
from quiet_oppen_data.adaptrar.transport import EjAktiveradKalla
from quiet_oppen_data.modeller import Faktaregister, Faktautkast, Fragplan
from quiet_oppen_data.register import Kalla, Sparrad
from quiet_oppen_data.register import hamta as hamta_kalla

_ORGNR = "5560125790"  # Aktiebolaget Volvo — verifieringsanropet 2026-08-16.


@pytest.fixture(autouse=True)
def _tom_tokencache():
    """Token-cachen är modulglobal — töm den runt varje test."""
    bv._token_cache.clear()
    yield
    bv._token_cache.clear()


@pytest.fixture
def _miljo(monkeypatch):
    monkeypatch.setenv("BOLAGSVERKET_CLIENT_ID", "test-id")
    monkeypatch.setenv("BOLAGSVERKET_CLIENT_SECRET", "test-secret")


# ---------------------------------------------------------------------------
# Registret
# ---------------------------------------------------------------------------

def test_bolagsverket_hvd_ar_verifierad_och_aktiverad():
    """Regressionsvakt: källan aktiverades 2026-08-16 på beställarens instruktion."""
    k = hamta_kalla("bolagsverket_hvd")
    assert isinstance(k, Kalla)
    assert k.verifierad is True
    assert k.aktiverad is True
    assert k.bas_url == "https://gw.api.bolagsverket.se/vardefulla-datamangder/v1"
    assert k.token_url == "https://portal.api.bolagsverket.se/oauth2/token"
    assert "vardefulla-datamangder:read" in (k.oauth_scope or "")
    assert "vardefulla-datamangder:ping" in (k.oauth_scope or "")


def test_bolagsverket_verkliga_huvudman_forblir_sparrad():
    """En annan, separat källa — ska inte påverkas av att HVD aktiverades."""
    assert isinstance(hamta_kalla("bolagsverket_verkliga_huvudman"), Sparrad)


# ---------------------------------------------------------------------------
# Token-hämtning
# ---------------------------------------------------------------------------

def test_hamta_token_cachar_i_minnet(monkeypatch, _miljo):
    anrop = 0

    def mock_post(self, url, **kwargs):
        nonlocal anrop
        anrop += 1
        assert url == "https://portal.api.bolagsverket.se/oauth2/token"
        assert kwargs["data"]["scope"] == "vardefulla-datamangder:read vardefulla-datamangder:ping"
        return httpx.Response(
            200,
            json={"access_token": "fejktoken", "expires_in": 3600, "token_type": "Bearer"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.Client, "post", mock_post)

    k = hamta_kalla("bolagsverket_hvd")
    token1 = bv.hamta_token(k)
    token2 = bv.hamta_token(k)

    assert token1 == token2 == "fejktoken"
    assert anrop == 1, "andra anropet skulle ha använt den cachade token"


def test_hamta_token_utan_miljovariabler_ger_tydligt_fel(monkeypatch):
    monkeypatch.delenv("BOLAGSVERKET_CLIENT_ID", raising=False)
    monkeypatch.delenv("BOLAGSVERKET_CLIENT_SECRET", raising=False)

    k = hamta_kalla("bolagsverket_hvd")
    with pytest.raises(RuntimeError, match="BOLAGSVERKET_CLIENT_ID"):
        bv.hamta_token(k)


# ---------------------------------------------------------------------------
# Adaptern
# ---------------------------------------------------------------------------

_ORGSVAR = {
    "organisationer": [
        {
            "organisationsnamn": {
                "dataproducent": "Bolagsverket",
                "fel": None,
                "organisationsnamnLista": [
                    {"namn": "Aktiebolaget Volvo", "organisationsnamntyp": {"kod": "FORETAGSNAMN"}},
                ],
            },
            "organisationsform": {"kod": "AB", "klartext": "Aktiebolag", "dataproducent": "Bolagsverket", "fel": None},
            "juridiskForm": {"kod": "49", "klartext": "Övriga aktiebolag", "dataproducent": "SCB", "fel": None},
            "reklamsparr": None,
            "verksamOrganisation": {"kod": "JA", "dataproducent": "SCB", "fel": None},
            "avregistradOrganisation": None,
            "avregistreringsorsak": None,
            "pagaendeAvvecklingsEllerOmstruktureringsforfarande": None,
            "organisationsdatum": {
                "registreringsdatum": "1915-05-05", "dataproducent": "Bolagsverket", "fel": None,
                "infortHosScb": "1972-01-01",
            },
            "postadressOrganisation": {
                "postadress": {"postnummer": "40508", "postort": "GÖTEBORG", "coAdress": None, "utdelningsadress": None},
                "dataproducent": "Bolagsverket", "fel": None,
            },
            "naringsgrenOrganisation": {
                "sni": [{"kod": "70100", "klartext": "Verksamheter som utövas av huvudkontor"}],
                "dataproducent": "SCB", "fel": None,
            },
            "verksamhetsbeskrivning": {
                "beskrivning": "Bolaget skall bedriva verksamhet inom transportmedel.",
                "dataproducent": "Bolagsverket", "fel": None,
            },
        }
    ]
}


def _mocka_hamtning(monkeypatch, svar):
    monkeypatch.setattr(bv, "hamta_token", lambda kalla: "fejktoken")

    def mock_request(self, method, url, **kwargs):
        assert kwargs["headers"]["Authorization"] == "Bearer fejktoken"
        return httpx.Response(200, json=svar, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx.Client, "request", mock_request)


def test_organisation_hamtning_mappar_falt(monkeypatch, isolerad_cache):
    _mocka_hamtning(monkeypatch, _ORGSVAR)
    adapter = BolagsverketAdapter()

    plan = Fragplan(fraga="", extra={"identitetsbeteckning": "556012-5790"})
    utkast = adapter.hamta(plan)

    assert utkast, "inga utkast producerades"
    assert all(isinstance(u, Faktautkast) for u in utkast)
    assert all(u.kalla_id == "bolagsverket_hvd" for u in utkast)
    assert all(u.dataset == _ORGNR for u in utkast)  # bindestreck rensat

    etiketter = {u.etikett: u.varde for u in utkast}
    namn_etikett = next(e for e in etiketter if e.startswith("Organisationsnamn"))
    assert etiketter[namn_etikett] == "Aktiebolaget Volvo"

    form_etikett = next(e for e in etiketter if e.startswith("Organisationsform"))
    assert etiketter[form_etikett] == "Aktiebolag"

    sni_etikett = next(e for e in etiketter if "SNI-koder" in e)
    assert "70100" in etiketter[sni_etikett]

    # reklamsparr var None i svaret ovan. Tidigare emitterades då INGENTING,
    # och nekandet blev osynligt: syntesen får bara skriva det som finns som
    # Faktapost, så «ingen spärr är registrerad» gick inte att skilja från «vi
    # vet inte». Sedan 2026-08-29 emitteras nekandet uttryckligen.
    reklam_etikett = next(e for e in etiketter if "Reklamspärr" in e)
    assert etiketter[reklam_etikett].startswith("Nej")

    # Samma sak för de tre andra frånvarande uppgifterna.
    avreg_etikett = next(e for e in etiketter if e.startswith("Avregistrerad"))
    assert etiketter[avreg_etikett].startswith("Nej")
    avveckling_etikett = next(e for e in etiketter if e.startswith("Pågående avveckling"))
    assert etiketter[avveckling_etikett].startswith("Nej")

    # Ett fält som INTE ingick i svaret alls ger fortfarande ingen post —
    # skillnaden mellan «vi vet att det inte är så» och «vi vet inte» ska
    # finnas kvar åt andra hållet också.
    assert not any(e.startswith("Registreringsland") for e in etiketter)

    # Utkasten ska passera Faktaregistrets validering precis som andra adaptrar.
    reg = Faktaregister()
    poster = reg.registrera_alla(utkast)
    assert len(poster) == len(utkast)
    assert all(p.lank_manniska and p.lank_maskin for p in poster)


def test_organisation_utan_traff_ger_tom_lista(monkeypatch, isolerad_cache):
    _mocka_hamtning(monkeypatch, {"organisationer": []})
    adapter = BolagsverketAdapter()

    utkast = adapter.hamta(Fragplan(fraga="", extra={"identitetsbeteckning": _ORGNR}))
    assert utkast == []


def test_hamtning_utan_identitetsbeteckning_ger_tom_lista(isolerad_cache):
    adapter = BolagsverketAdapter()
    assert adapter.hamta(Fragplan(fraga="", extra={})) == []


def test_dokumentlista_hamtning(monkeypatch, isolerad_cache):
    svar = {"dokument": [{"typ": "arsredovisning", "rakenskapsar": "2025", "inkommet": "2026-05-01"}]}
    _mocka_hamtning(monkeypatch, svar)
    adapter = BolagsverketAdapter()

    plan = Fragplan(fraga="", extra={
        "verktyg": "bolagsverket_hvd_dokumentlista",
        "identitetsbeteckning": _ORGNR,
    })
    utkast = adapter.hamta(plan)

    assert len(utkast) == 1
    assert utkast[0].kalla_id == "bolagsverket_hvd"
    assert "2025" in utkast[0].varde
    assert "årsredovisning" in utkast[0].etikett.lower()


def test_beskriv_exponerar_bade_organisationer_och_dokumentlista(isolerad_cache):
    adapter = BolagsverketAdapter()
    namn = {spec["name"] for spec in adapter.beskriv()}
    assert namn == {
        "bolagsverket_hvd",
        "bolagsverket_hvd_dokumentlista",
        "bolagsverket_hvd_dokument",
    }


def test_transport_avvisar_om_kallan_stangs_av(monkeypatch, isolerad_cache):
    """Om aktiverad: false sätts tillbaka i registret ska anrop fortfarande
    stoppas i transportlagret — samma spärr som alla andra källor (se
    test_steg5_transport.py::test_ej_aktiverad_kalla_kastar)."""
    import quiet_oppen_data.adaptrar.transport as transport

    avstangd = Kalla(
        id="bolagsverket_hvd", adapter="bolagsverket", takt={}, cache_ttl=3600,
        aktiverad=False, verifierad=True,
    )
    monkeypatch.setattr(transport, "hamta", lambda kalla_id: avstangd if kalla_id == "bolagsverket_hvd" else None)

    with pytest.raises(EjAktiveradKalla):
        transport.hamta_json("bolagsverket_hvd", "POST", "https://gw.api.bolagsverket.se/x")
