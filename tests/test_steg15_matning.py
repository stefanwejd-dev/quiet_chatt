"""Tester för steg 15 — matning.py och nattlig_ingest.py.

Täcker:
  * logga_fraga() sparar rätt fält
  * rensa_gamla_fragor() sätter fraga_text = NULL för gamla rader
  * las_matpunkter() returnerar korrekta aggregat
  * niva3_andel räknas korrekt
  * GET /matning endpoint svarar 200
  * nattlig_ingest.kör_nattlig_ingest() rapporterar deltaformat
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Isolerad matningsdatabas per test
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_matning_db(tmp_path, monkeypatch):
    """Isolerar matning.py mot en temporär SQLite-fil."""
    db = tmp_path / "matning_test.sqlite"

    import quiet_oppen_data.matning as mat_modul
    monkeypatch.setattr(mat_modul, "_db_sökväg", lambda: db)
    # Schemat byggs numera vid första anslutningen, inte vid import. Nollställ
    # initieringscachen och öppna en anslutning så att tabellerna finns — flera
    # tester skriver direkt mot filen innan någon matning-funktion har körts.
    monkeypatch.setattr(mat_modul, "_initierade", set())
    mat_modul._anslut().close()
    return db


# ---------------------------------------------------------------------------
# matning.logga_fraga
# ---------------------------------------------------------------------------

def test_logga_fraga_sparar_ratt_falt(tmp_matning_db):
    import quiet_oppen_data.matning as mat
    mat.logga_fraga(
        fraga_text="Vad är referensräntan?",
        kan_besvaras=True,
        fas_c_forsok=1,
        antal_faktaposter=2,
        anvanda_kallor=["riksbanken"],
        token_fas_a_in=1000,
        token_fas_a_ut=500,
        token_fas_a_cache_read=800,
        token_fas_a_cache_write=200,
    )

    with sqlite3.connect(str(tmp_matning_db)) as kon:
        rad = kon.execute("SELECT * FROM fraga_logg LIMIT 1").fetchone()

    assert rad is not None
    # id, tidpunkt, fraga_text, kan_besvaras, fas_c_forsok, antal_faktaposter,
    # niva3_svar, token_fas_a_in, token_fas_a_ut, token_fas_a_cache_read, ...
    assert rad[2] == "Vad är referensräntan?"  # fraga_text
    assert rad[3] == 1                          # kan_besvaras
    assert rad[4] == 1                          # fas_c_forsok
    assert rad[5] == 2                          # antal_faktaposter
    assert rad[6] == 0                          # niva3_svar (riksbanken ≠ dataportal)
    assert rad[7] == 1000                       # token_fas_a_in


def test_logga_fraga_markerar_niva3(tmp_matning_db):
    """Frågor vars svar kommer från dataportal ska ha niva3_svar=1."""
    import quiet_oppen_data.matning as mat
    mat.logga_fraga(
        fraga_text="Hitta en datamängd",
        kan_besvaras=True,
        fas_c_forsok=1,
        antal_faktaposter=1,
        anvanda_kallor=["dataportal", "riksbanken"],
    )

    with sqlite3.connect(str(tmp_matning_db)) as kon:
        niva3 = kon.execute("SELECT niva3_svar FROM fraga_logg LIMIT 1").fetchone()[0]
    assert niva3 == 1


def test_logga_fraga_loggningsfel_blockerar_inte(tmp_matning_db, monkeypatch):
    """Loggningsfel ska inte kasta — svaret får aldrig blockeras av mätning."""
    import quiet_oppen_data.matning as mat
    monkeypatch.setattr(mat, "_anslut", lambda: (_ for _ in ()).throw(RuntimeError("DB nere")))
    # Ska inte kasta
    mat.logga_fraga(
        fraga_text="test",
        kan_besvaras=False,
        fas_c_forsok=0,
        antal_faktaposter=0,
        anvanda_kallor=[],
    )


# ---------------------------------------------------------------------------
# matning.rensa_gamla_fragor
# ---------------------------------------------------------------------------

def test_rensa_gamla_fragor_sätter_null(tmp_matning_db):
    import quiet_oppen_data.matning as mat

    gammal = (datetime.now(UTC) - timedelta(days=35)).isoformat()
    ny = datetime.now(UTC).isoformat()

    with sqlite3.connect(str(tmp_matning_db)) as kon:
        kon.execute(
            "INSERT INTO fraga_logg (tidpunkt, fraga_text, kan_besvaras, fas_c_forsok, "
            "antal_faktaposter, niva3_svar) VALUES (?,?,?,?,?,?)",
            (gammal, "Gammal fråga", 1, 1, 1, 0),
        )
        kon.execute(
            "INSERT INTO fraga_logg (tidpunkt, fraga_text, kan_besvaras, fas_c_forsok, "
            "antal_faktaposter, niva3_svar) VALUES (?,?,?,?,?,?)",
            (ny, "Ny fråga", 1, 1, 1, 0),
        )
        kon.commit()

    rensade = mat.rensa_gamla_fragor(dagar=30)
    assert rensade == 1

    with sqlite3.connect(str(tmp_matning_db)) as kon:
        rader = kon.execute("SELECT fraga_text FROM fraga_logg ORDER BY id").fetchall()

    assert rader[0][0] is None       # gammal → NULL
    assert rader[1][0] == "Ny fråga" # ny → orörd


def test_rensa_bevarar_ovrigt(tmp_matning_db):
    """Övriga kolumner ska vara oförändrade efter rensning."""
    import quiet_oppen_data.matning as mat

    gammal = (datetime.now(UTC) - timedelta(days=40)).isoformat()
    with sqlite3.connect(str(tmp_matning_db)) as kon:
        kon.execute(
            "INSERT INTO fraga_logg (tidpunkt, fraga_text, kan_besvaras, fas_c_forsok, "
            "antal_faktaposter, niva3_svar) VALUES (?,?,?,?,?,?)",
            (gammal, "Gammal", 1, 1, 3, 1),
        )
        kon.commit()

    mat.rensa_gamla_fragor(dagar=30)

    with sqlite3.connect(str(tmp_matning_db)) as kon:
        rad = kon.execute("SELECT kan_besvaras, antal_faktaposter FROM fraga_logg").fetchone()
    assert rad == (1, 3)


# ---------------------------------------------------------------------------
# matning.las_matpunkter
# ---------------------------------------------------------------------------

def test_las_matpunkter_tom_db(tmp_matning_db):
    import quiet_oppen_data.matning as mat
    punkter = mat.las_matpunkter(dygn=30)
    assert punkter["totalt_fragor"] == 0
    assert punkter["niva3_andel"] is None


def test_las_matpunkter_aggregat(tmp_matning_db):
    import quiet_oppen_data.matning as mat

    # Lägg in 3 frågor: 2 besvarade (1 niva3), 1 fail-closed
    for fraga, kan, niva3, fas_c in [
        ("f1", 1, 1, 1),
        ("f2", 1, 0, 2),
        ("f3", 0, 0, 0),
    ]:
        mat.logga_fraga(
            fraga_text=fraga,
            kan_besvaras=bool(kan),
            fas_c_forsok=fas_c,
            antal_faktaposter=2 if kan else 0,
            anvanda_kallor=["dataportal"] if niva3 else ["riksbanken"],
        )

    punkter = mat.las_matpunkter(dygn=30)
    assert punkter["totalt_fragor"] == 3
    assert punkter["besvarade"] == 2
    assert punkter["inte_besvarade"] == 1
    assert punkter["niva3_andel"] == 0.5        # 1 av 2 besvarade är niva3
    assert punkter["fas_c_fordelning"]["forsok_1_ok"] == 1
    assert punkter["fas_c_fordelning"]["forsok_2_ok"] == 1
    assert punkter["fas_c_fordelning"]["fail_closed"] == 1
    assert punkter["snitt_faktaposter_per_svar"] == 2.0


def test_las_matpunkter_kallor_topp(tmp_matning_db):
    import quiet_oppen_data.matning as mat

    mat.logga_fraga(
        fraga_text="q1", kan_besvaras=True, fas_c_forsok=1,
        antal_faktaposter=1, anvanda_kallor=["riksbanken", "scb_pxweb"],
    )
    mat.logga_fraga(
        fraga_text="q2", kan_besvaras=True, fas_c_forsok=1,
        antal_faktaposter=1, anvanda_kallor=["riksbanken"],
    )

    punkter = mat.las_matpunkter(dygn=30)
    topp = {k["kalla_id"]: k["antal_fragor"] for k in punkter["kallor_topp10"]}
    assert topp["riksbanken"] == 2
    assert topp["scb_pxweb"] == 1


# ---------------------------------------------------------------------------
# GET /matning — API-endpoint
# ---------------------------------------------------------------------------

@pytest.fixture
def api_klient(monkeypatch):
    """TestClient med matning mockad (inga DB-anrop)."""
    import quiet_oppen_data.api as api_modul
    import quiet_oppen_data.matning as mat

    # /matning kräver nyckel sedan granskningen 2026-08-14.
    monkeypatch.setenv("MATNING_NYCKEL", "test")

    mock_punkter = {
        "period_dagar": 30,
        "totalt_fragor": 10,
        "besvarade": 8,
        "inte_besvarade": 2,
        "niva3_andel": 0.25,
        "fas_c_fordelning": {"forsok_1_ok": 7, "forsok_2_ok": 1, "fail_closed": 2},
        "snitt_faktaposter_per_svar": 2.5,
        "tokens": {
            "fas_a_in_snitt": 5000,
            "fas_a_ut_snitt": 1200,
            "fas_a_cache_read_snitt": 4000,
            "fas_b_in_snitt": 2000,
            "fas_b_ut_snitt": 300,
        },
        "kallor_topp10": [{"kalla_id": "riksbanken", "antal_fragor": 6}],
    }
    mock_ingest = {
        "tidpunkt": "2026-08-13T03:00:00+00:00",
        "datamangder_totalt": 23289,
        "datamangder_nya": 12,
        "datamangder_uppdaterade": 0,
        "distributioner_totalt": 32518,
        "fel": 0,
        "varaktighet_sek": 182.3,
    }

    mock_lagkorpus_alder = [
        {"sfs": "1999:1229", "kortnamn": "IL", "hamtad": "2026-08-13T03:00:00+00:00",
         "dygn_sedan_hamtning": 1.0, "ligger_efter": False},
    ]
    mock_lagkontroll = {
        "tidpunkt": "2026-08-14T03:00:00+00:00",
        "kontrollerade": 62, "andrade": 1, "omingesterade": 1,
        "ingest_fel": 0, "fel_vid_kontroll": 0, "status": "ok",
        "varaktighet_sek": 12.3,
    }

    monkeypatch.setattr(mat, "las_matpunkter", lambda dygn=30: mock_punkter)
    monkeypatch.setattr(mat, "las_senaste_ingest", lambda: mock_ingest)
    monkeypatch.setattr(mat, "las_senaste_lagkontroll", lambda: mock_lagkontroll)
    monkeypatch.setattr(
        "quiet_oppen_data.index.lag_ingest.las_lagkorpus_alder",
        lambda: mock_lagkorpus_alder,
    )

    return TestClient(api_modul.app)


def test_matning_endpoint_svarar_200(api_klient):
    resp = api_klient.get("/matning", headers={"x-matning-nyckel": "test"})
    assert resp.status_code == 200


def test_matning_endpoint_struktur(api_klient):
    data = api_klient.get("/matning", headers={"x-matning-nyckel": "test"}).json()
    assert "matpunkter" in data
    assert "senaste_ingest" in data
    assert "lagkorpus_alder" in data
    assert "senaste_lagkontroll" in data
    assert data["matpunkter"]["totalt_fragor"] == 10
    assert data["matpunkter"]["niva3_andel"] == 0.25
    assert data["senaste_ingest"]["datamangder_totalt"] == 23289
    assert data["lagkorpus_alder"][0]["sfs"] == "1999:1229"
    assert data["senaste_lagkontroll"]["status"] == "ok"


# ---------------------------------------------------------------------------
# nattlig_ingest.kör_nattlig_ingest — deltaformat
# ---------------------------------------------------------------------------

def test_nattlig_ingest_delta(tmp_path, monkeypatch):
    """Verifierar att kör_nattlig_ingest returnerar rätt deltaformat."""
    from quiet_oppen_data.index.nattlig_ingest import kör_nattlig_ingest

    # Skapa en minimal SQLite med datamangd/distribution-tabeller
    db = tmp_path / "test_index.sqlite"
    with sqlite3.connect(str(db)) as kon:
        kon.execute("CREATE TABLE datamangd (id TEXT PRIMARY KEY)")
        kon.execute("CREATE TABLE distribution (id TEXT PRIMARY KEY)")
        # Lägg in 5 datamängder innan
        for i in range(5):
            kon.execute("INSERT INTO datamangd VALUES (?)", (str(i),))
        kon.commit()

    def fake_ingest(db_sokväg=None):
        # Simulera att ingest lägger till 3 nya datamängder
        with sqlite3.connect(str(db_sokväg)) as k:
            for i in range(5, 8):
                k.execute("INSERT OR IGNORE INTO datamangd VALUES (?)", (str(i),))
            k.commit()

    import quiet_oppen_data.matning as mat
    from quiet_oppen_data.index import nattlig_ingest as ni
    monkeypatch.setattr(mat, "logga_ingest", lambda **_: None)
    monkeypatch.setattr(mat, "rensa_gamla_fragor", lambda dagar=30: 0)
    monkeypatch.setattr(ni, "kör_nattlig_lagkontroll", lambda **_: {"status": "ok"})

    with patch("quiet_oppen_data.index.ingest.main", side_effect=fake_ingest):
        resultat = kör_nattlig_ingest(db_sökväg=db)

    assert resultat["datamangder_totalt"] == 8
    assert resultat["datamangder_nya"] == 3
    assert resultat["distributioner_totalt"] == 0
    assert resultat["fel"] == 0
    assert "varaktighet_sek" in resultat


def test_nattlig_ingest_rapporterar_fel(tmp_path, monkeypatch):
    """Ingest som kastar ska sätta fel=1 utan att krascha wrappern."""
    from quiet_oppen_data.index.nattlig_ingest import kör_nattlig_ingest

    db = tmp_path / "test_index.sqlite"
    with sqlite3.connect(str(db)) as kon:
        kon.execute("CREATE TABLE datamangd (id TEXT PRIMARY KEY)")
        kon.execute("CREATE TABLE distribution (id TEXT PRIMARY KEY)")
        kon.commit()

    import quiet_oppen_data.matning as mat
    from quiet_oppen_data.index import nattlig_ingest as ni
    monkeypatch.setattr(mat, "logga_ingest", lambda **_: None)
    monkeypatch.setattr(mat, "rensa_gamla_fragor", lambda dagar=30: 0)
    monkeypatch.setattr(ni, "kör_nattlig_lagkontroll", lambda **_: {"status": "ok"})

    with patch("quiet_oppen_data.index.ingest.main", side_effect=RuntimeError("Nätverksfel")):
        resultat = kör_nattlig_ingest(db_sökväg=db)

    assert resultat["fel"] == 1


# ---------------------------------------------------------------------------
# Tillagt vid granskningen 2026-08-14
# ---------------------------------------------------------------------------

def test_import_skapar_ingen_databas(tmp_path, monkeypatch):
    """Import ska inte röra disken.

    Schemat skapades tidigare på modulnivå, så ett blott
    `import quiet_oppen_data.matning` skapade data/matning.sqlite — även i
    testsviten, som därmed skrev till den riktiga databasen trots tmp-fixtur.
    """
    import importlib

    import quiet_oppen_data.matning as mat

    db = tmp_path / "ny" / "matning.sqlite"
    monkeypatch.setattr(mat, "_db_sökväg", lambda: db)
    monkeypatch.setattr(mat, "_initierade", set())
    importlib.reload  # noqa: B018 — dokumenterar att omladdning inte behövs

    assert not db.exists(), "import/omkonfigurering fick inte skapa filen"
    mat.rensa_gamla_fragor(dagar=30)
    assert db.exists(), "första faktiska användningen ska skapa schemat"


def test_matning_endpoint_kraver_nyckel(monkeypatch, tmp_path):
    """Mätvyn är driftdata, inte publikt innehåll."""
    from fastapi.testclient import TestClient

    import quiet_oppen_data.api as api_modul
    import quiet_oppen_data.matning as mat

    # Peka om databasen — annars skriver testet till den riktiga data/matning.sqlite
    # när 200-fallet faktiskt läser mätpunkter.
    monkeypatch.setattr(mat, "_db_sökväg", lambda: tmp_path / "matning.sqlite")
    monkeypatch.setattr(mat, "_initierade", set())

    klient = TestClient(api_modul.app)

    # Utan nyckel i miljön: stängd, inte öppen (fail-closed).
    monkeypatch.delenv("MATNING_NYCKEL", raising=False)
    assert klient.get("/matning").status_code == 503

    monkeypatch.setenv("MATNING_NYCKEL", "hemlig")
    assert klient.get("/matning").status_code == 401
    assert klient.get("/matning", headers={"x-matning-nyckel": "fel"}).status_code == 401
    assert klient.get("/matning", headers={"x-matning-nyckel": "hemlig"}).status_code == 200


def test_rensning_sker_aven_om_ingest_statistiken_kastar(monkeypatch, tmp_path):
    """Bevarandeplikten får inte hänga på att mätningsloggningen lyckas."""
    from quiet_oppen_data.index import nattlig_ingest as ni

    rensat = []
    import quiet_oppen_data.matning as mat
    monkeypatch.setattr(mat, "rensa_gamla_fragor",
                        lambda dagar=30: rensat.append(dagar) or 7)
    monkeypatch.setattr(mat, "logga_ingest",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("sabotage")))
    monkeypatch.setattr(ni, "_räkna_rader", lambda p: (0, 0))
    monkeypatch.setattr("quiet_oppen_data.index.ingest.main", lambda **kw: None)
    monkeypatch.setattr(ni, "kör_nattlig_lagkontroll", lambda **_: {"status": "ok"})

    res = ni.kör_nattlig_ingest(db_sökväg=tmp_path / "index.sqlite")
    assert rensat == [30], "raderingen ska ha körts trots undantaget i loggningen"
    assert res["datamangder_totalt"] == 0
