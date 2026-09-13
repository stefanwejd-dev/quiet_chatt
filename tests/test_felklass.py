"""Felklassificering av Anthropic-fel — driftstopp ska synas, inte maskeras.

Bakgrund: 2026-09-13 svarade chatten på quiet.nu/juridik "Ett tekniskt fel
inträffade" på varje fråga. Orsaken var slut på kredit — ett förväntat
drifttillstånd som designen själv skapar (ARKITEKTUR.md §6a) — men i loggen
såg det likadant ut som ett nätverksglapp. Driftstoppet fick pågå i veckor.

Testerna täcker:
  * klassificera_anthropic_fel() för varje felklass i tabellen
  * POST /fraga → SSE-meddelandet är oförändrat generiskt OCH felklassen
    hamnar i driftfel_logg
  * GET /matning → fältet `driftfel`
  * GET /halsa svarar 200 utan ANTHROPIC_API_KEY — invarianten att
    modulnivå-importen av felklass.py inte kräver någon nyckel
"""

from __future__ import annotations

import dataclasses
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx
import pytest
from fastapi.testclient import TestClient

import quiet_oppen_data.api as api_module
import quiet_oppen_data.konfig as konfig_modul
import quiet_oppen_data.matning as mat_modul
from quiet_oppen_data.konfig import (
    IndexKonfig,
    Konfig,
    KvotKonfig,
    ModellKonfig,
    SiteKonfig,
)
from quiet_oppen_data.modeller import Faktaregister
from quiet_oppen_data.motor.felklass import klassificera_anthropic_fel

_BEGARAN = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _svar(status: int, text: str = "{}") -> httpx.Response:
    """Attrapp-httpx.Response — allt anthropics statusfel behöver för att byggas."""
    return httpx.Response(status, request=_BEGARAN, text=text)


# ---------------------------------------------------------------------------
# klassificera_anthropic_fel — en ren funktion, en felklass i taget
# ---------------------------------------------------------------------------

def test_auth_ar_401():
    fel = anthropic.AuthenticationError(
        "Error code: 401 - {'error': {'message': 'invalid x-api-key'}}",
        response=_svar(401),
        body=None,
    )
    assert klassificera_anthropic_fel(fel) == "auth"


def test_billing_pa_slut_kredit():
    """Det faktiska felet från 2026-09-13: HTTP 400 med 'credit balance' i kroppen."""
    fel = anthropic.BadRequestError(
        "Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
        "'message': 'Your credit balance is too low to access the Anthropic API.'}}",
        response=_svar(400),
        body=None,
    )
    assert klassificera_anthropic_fel(fel) == "billing"


def test_billing_pa_permission_denied():
    fel = anthropic.PermissionDeniedError(
        "Error code: 403 - {'error': {'message': 'Billing is disabled for this org.'}}",
        response=_svar(403),
        body=None,
    )
    assert klassificera_anthropic_fel(fel) == "billing"


def test_billing_matchar_skiftlagesokanslig():
    fel = anthropic.BadRequestError(
        "Error code: 400 - Your CREDIT BALANCE is too low.",
        response=_svar(400),
        body=None,
    )
    assert klassificera_anthropic_fel(fel) == "billing"


def test_billing_hittas_i_body_aven_utan_meddelande():
    """SDK:n väver in svarskroppen i meddelandet idag. Skulle en framtida
    version sluta göra det ska klassificeringen ändå hitta markören."""
    fel = anthropic.BadRequestError(
        "Error code: 400",
        response=_svar(400),
        body={"error": {"message": "Your credit balance is too low."}},
    )
    assert klassificera_anthropic_fel(fel) == "billing"


def test_vanligt_400_ar_inte_billing():
    """Ett formatfel är inte ett driftstopp och får inte larma som ett."""
    fel = anthropic.BadRequestError(
        "Error code: 400 - {'error': {'message': 'max_tokens: must be >= 1'}}",
        response=_svar(400),
        body=None,
    )
    assert klassificera_anthropic_fel(fel) == "api_ovrigt"


def test_rate_limit_ar_429():
    fel = anthropic.RateLimitError("Error code: 429", response=_svar(429), body=None)
    assert klassificera_anthropic_fel(fel) == "rate_limit"


def test_overloaded_pa_529():
    fel = anthropic.APIStatusError("Error code: 529", response=_svar(529), body=None)
    assert klassificera_anthropic_fel(fel) == "overloaded"


def test_overloaded_pa_internal_server_error():
    fel = anthropic.InternalServerError("Error code: 500", response=_svar(500), body=None)
    assert klassificera_anthropic_fel(fel) == "overloaded"


def test_natverk_pa_connection_error():
    fel = anthropic.APIConnectionError(request=_BEGARAN)
    assert klassificera_anthropic_fel(fel) == "natverk"


def test_natverk_pa_timeout():
    """APITimeoutError ärver APIConnectionError — timeout är ett nätverksfel."""
    fel = anthropic.APITimeoutError(request=_BEGARAN)
    assert klassificera_anthropic_fel(fel) == "natverk"


def test_api_ovrigt_pa_ovriga_apifel():
    fel = anthropic.NotFoundError("Error code: 404", response=_svar(404), body=None)
    assert klassificera_anthropic_fel(fel) == "api_ovrigt"


def test_internt_pa_icke_anthropicfel():
    """Ett kodfel är inte ett API-fel och ska inte gömma sig bland dem."""
    assert klassificera_anthropic_fel(ValueError("trasig faktapost")) == "internt"


def test_internt_pa_adapterfel():
    assert klassificera_anthropic_fel(RuntimeError("adaptern svarade inte")) == "internt"


# ---------------------------------------------------------------------------
# Fixturer för API-testerna
# ---------------------------------------------------------------------------

_HEMLIG_TESTNYCKEL = "sk-ant-test-hemlig-nyckel-som-aldrig-far-synas"


@pytest.fixture
def tmp_matning_db(tmp_path, monkeypatch):
    """Isolerar matning.py mot en temporär SQLite-fil (samma mönster som steg 15)."""
    db = tmp_path / "matning_test.sqlite"
    monkeypatch.setattr(mat_modul, "_db_sökväg", lambda: db)
    monkeypatch.setattr(mat_modul, "_initierade", set())
    mat_modul._anslut().close()
    return db


@pytest.fixture
def isolerad_konfig(tmp_path, monkeypatch):
    """Egen index-/kvotdatabas per test plus en attrapp-API-nyckel."""
    konfig = Konfig(
        site=SiteKonfig(domain="quiet.nu"),
        modell=ModellKonfig(
            namn="claude-opus-5",
            effort_hamtning="high",
            effort_syntes="medium",
            max_verktygsvarv=8,
        ),
        kvot=KvotKonfig(
            fragor_per_ip_per_dygn=50,
            fragor_totalt_per_dygn=2000,
            kostnadstak_sek_per_manad=1000,
        ),
        index=IndexKonfig(
            db=str(tmp_path / "index.sqlite"),
            embedding_modell="KBLab/sentence-bert-swedish-cased",
            embedding_dim=768,
        ),
        anthropic_api_key=_HEMLIG_TESTNYCKEL,
    )
    monkeypatch.setattr(konfig_modul, "_cache", konfig)
    monkeypatch.setattr(api_module, "_fas_a", None)
    monkeypatch.setattr(api_module, "_fas_c", None)
    yield konfig
    konfig_modul.nollstall()


def _parsa_sse(text: str) -> list[tuple[str, str]]:
    """Grov SSE-parser (samma som i steg 13-testerna)."""
    handelser = []
    for block in text.strip().split("\n\n"):
        if not block.strip():
            continue
        namn = None
        data = None
        for rad in block.strip().split("\n"):
            if rad.startswith("event: "):
                namn = rad[len("event: "):]
            elif rad.startswith("data: "):
                data = rad[len("data: "):]
        if namn:
            handelser.append((namn, data or ""))
    return handelser


# ---------------------------------------------------------------------------
# POST /fraga — klienten får generiskt, loggen får felklassen
# ---------------------------------------------------------------------------

def test_fraga_med_authfel_loggar_driftfel_och_svarar_generiskt(
    isolerad_konfig, tmp_matning_db, monkeypatch
):
    fel = anthropic.AuthenticationError(
        "Error code: 401 - {'error': {'message': 'invalid x-api-key'}}",
        response=_svar(401),
        body=None,
    )

    def fas_a_kastar(fraga):
        raise fel

    monkeypatch.setattr(api_module, "_fas_a", SimpleNamespace(hamta=fas_a_kastar))
    monkeypatch.setattr(
        api_module, "_fas_c", SimpleNamespace(kor=lambda fraga, reg: None)
    )

    klient = TestClient(api_module.app)
    res = klient.post("/fraga", json={"fraga": "Vad är referensräntan?"})

    assert res.status_code == 200
    handelser = _parsa_sse(res.text)
    assert [namn for namn, _ in handelser] == ["fel"]

    # (a) Klientmeddelandet är exakt det generiska — ingen felklass, ingen
    #     statuskod, ingen API-detalj läcker.
    assert json.loads(handelser[0][1]) == {
        "meddelande": "Ett tekniskt fel inträffade. Försök igen."
    }
    assert _HEMLIG_TESTNYCKEL not in res.text

    # (b) Felklassen finns i driftfel_logg.
    with sqlite3.connect(str(tmp_matning_db)) as kon:
        rader = kon.execute("SELECT tidpunkt, felklass FROM driftfel_logg").fetchall()
    assert len(rader) == 1
    assert rader[0][1] == "auth"
    assert rader[0][0]  # tidpunkten är satt


def test_fraga_med_kodfel_loggar_internt(isolerad_konfig, tmp_matning_db, monkeypatch):
    """Ett fel utanför anthropic ska klassas `internt`, inte gömmas bland API-felen."""
    def fas_c_kastar(fraga, register):
        raise ValueError("trasig faktapost")

    monkeypatch.setattr(
        api_module,
        "_fas_a",
        SimpleNamespace(hamta=lambda f: SimpleNamespace(register=Faktaregister())),
    )
    monkeypatch.setattr(api_module, "_fas_c", SimpleNamespace(kor=fas_c_kastar))

    klient = TestClient(api_module.app)
    res = klient.post("/fraga", json={"fraga": "Vad är referensräntan?"})

    assert res.status_code == 200
    with sqlite3.connect(str(tmp_matning_db)) as kon:
        rader = kon.execute("SELECT felklass FROM driftfel_logg").fetchall()
    assert [r[0] for r in rader] == ["internt"]


# ---------------------------------------------------------------------------
# matning.logga_driftfel / las_driftfel
# ---------------------------------------------------------------------------

def test_las_driftfel_tom_db(tmp_matning_db):
    resultat = mat_modul.las_driftfel(30)
    assert resultat["period_dagar"] == 30
    assert resultat["totalt"] == 0
    assert resultat["per_felklass"] == {}
    assert resultat["senaste"] is None


def test_las_driftfel_raknar_per_felklass(tmp_matning_db):
    for felklass in ("billing", "billing", "natverk"):
        mat_modul.logga_driftfel(felklass)

    resultat = mat_modul.las_driftfel(30)
    assert resultat["totalt"] == 3
    assert resultat["per_felklass"] == {"billing": 2, "natverk": 1}
    assert resultat["senaste"]["felklass"] == "natverk"


def test_logga_driftfel_blockerar_inte_vid_skrivfel(tmp_matning_db, monkeypatch):
    """Mätfel får aldrig bli ett fel för användaren — samma regel som §11."""
    def trasig():
        raise sqlite3.OperationalError("disk i/o error")

    monkeypatch.setattr(mat_modul, "_anslut", trasig)
    mat_modul.logga_driftfel("billing")  # ska inte kasta


# ---------------------------------------------------------------------------
# GET /matning — nytt toppnivåfält `driftfel`
# ---------------------------------------------------------------------------

def test_matning_innehaller_driftfel(isolerad_konfig, tmp_matning_db, monkeypatch):
    monkeypatch.setenv("MATNING_NYCKEL", "test")
    mat_modul.logga_driftfel("billing")
    mat_modul.logga_driftfel("auth")

    klient = TestClient(api_module.app)
    data = klient.get("/matning", headers={"x-matning-nyckel": "test"}).json()

    assert "driftfel" in data
    assert data["driftfel"]["totalt"] == 2
    assert data["driftfel"]["per_felklass"] == {"billing": 1, "auth": 1}
    assert data["driftfel"]["senaste"]["felklass"] == "auth"


# ---------------------------------------------------------------------------
# Invarianten: /halsa fungerar utan ANTHROPIC_API_KEY
# ---------------------------------------------------------------------------

_INVARIANTSKRIPT = """
from fastapi.testclient import TestClient
import quiet_oppen_data.konfig as konfig
import quiet_oppen_data.api as api

assert not konfig.las().anthropic_api_key, "nyckeln skulle vara tom"
svar = TestClient(api.app).get("/halsa")
assert svar.status_code == 200, svar.status_code
assert svar.json()["status"] == "ok"
print("OK")
"""

def test_halsa_svarar_200_utan_api_nyckel(tmp_path, monkeypatch):
    """felklass.py importeras på modulnivå i api.py. Den importen får inte
    dra in något som kräver en nyckel — /halsa ska fungera utan den."""
    # load_dotenv nollas: annars läser konfig.las() tillbaka nyckeln ur en
    # lokal .env och testet mäter utvecklarmaskinen i stället för koden.
    monkeypatch.setattr(konfig_modul, "load_dotenv", lambda *a, **kw: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(konfig_modul, "_cache", None)
    monkeypatch.setattr(api_module, "_fas_a", None)
    monkeypatch.setattr(api_module, "_fas_c", None)

    konfig = konfig_modul.las()
    assert konfig.anthropic_api_key is None

    # Peka om indexet så testet inte rör den riktiga databasen.
    monkeypatch.setattr(
        konfig_modul,
        "_cache",
        dataclasses.replace(
            konfig,
            index=dataclasses.replace(konfig.index, db=str(tmp_path / "index.sqlite")),
        ),
    )

    klient = TestClient(api_module.app)
    res = klient.get("/halsa")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
    # Ingen motor instansierades — /halsa rör aldrig fas A/C.
    assert api_module._fas_a is None
    assert api_module._fas_c is None

    konfig_modul.nollstall()


def test_api_importeras_i_process_utan_nyckel():
    """Skarpare version av invarianten ovan: en helt egen process där
    ANTHROPIC_API_KEY är tom (load_dotenv skriver inte över satta variabler).
    Går modulnivå-importen av felklass.py inte att göra utan nyckel faller
    den här — i testprocessen är nyckeln redan inläst och döljer felet."""
    miljo = dict(os.environ, ANTHROPIC_API_KEY="")
    res = subprocess.run(
        [sys.executable, "-c", _INVARIANTSKRIPT],
        capture_output=True,
        text=True,
        env=miljo,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert res.returncode == 0, res.stderr
    assert "OK" in res.stdout
