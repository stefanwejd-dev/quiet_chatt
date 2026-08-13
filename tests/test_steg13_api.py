"""Acceptanstester för Steg 13 — HTTP-API och kvoter.

Acceptanskriterier från PLAN.md §13:
  1. `POST /fraga` strömmar ett svar med fotnoter (SSE).
  2. Ett anrop från fel origin avvisas.
  3. Anrop 51 från samma IP samma dygn avvisas med kvotmeddelande.
  4. Nyckeln syns inte i något svar och inte i någon logg.

Fas A/C mockas i samtliga tester nedan — inget nätverk, inget API-anrop.
Varje test får en egen temporär index-/kvotdatabas via `_isolerad_konfig`,
så kvoträkningen inte läcker mellan tester.
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import quiet_oppen_data.api as api_module
import quiet_oppen_data.konfig as konfig_modul
from quiet_oppen_data.konfig import Konfig, IndexKonfig, KvotKonfig, ModellKonfig, SiteKonfig
from quiet_oppen_data.modeller import Faktaregister
from quiet_oppen_data.motor.syntes import INGET_HITTAT, Stycke, SyntesSvar

_HEMLIG_TESTNYCKEL = "sk-ant-test-hemlig-nyckel-som-aldrig-far-synas"


@pytest.fixture
def isolerad_konfig(tmp_path, monkeypatch):
    """Egen index-/kvotdatabas per test plus en attrapp-API-nyckel.

    Nyckeln är en igenkännbar sträng så testerna kan bevisa att den aldrig
    dyker upp i ett svar (acceptans 4).
    """
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
    konfig_modul._cache = None


@pytest.fixture
def client(isolerad_konfig):
    return TestClient(api_module.app)


def _sla_in_falska_motorer(monkeypatch, register: Faktaregister, svar: SyntesSvar):
    fas_a = SimpleNamespace(hamta=lambda fraga: SimpleNamespace(register=register))
    fas_c = SimpleNamespace(kor=lambda fraga, reg: svar)
    monkeypatch.setattr(api_module, "_fas_a", fas_a)
    monkeypatch.setattr(api_module, "_fas_c", fas_c)


def _parsa_sse(text: str) -> list[tuple[str, str]]:
    """Grov SSE-parser: returnerar [(event, data), …] för teständamål."""
    handelser = []
    for block in text.strip().split("\n\n"):
        if not block.strip():
            continue
        rader = block.strip().split("\n")
        namn = None
        data = None
        for rad in rader:
            if rad.startswith("event: "):
                namn = rad[len("event: "):]
            elif rad.startswith("data: "):
                data = rad[len("data: "):]
        if namn:
            handelser.append((namn, data or ""))
    return handelser


# ---------------------------------------------------------------------------
# /halsa
# ---------------------------------------------------------------------------

def test_halsa_svarar_200_med_status_ok(client):
    res = client.get("/halsa")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "kallor" in data
    # Riksbanken finns i registret och ska synas, även utan trafik än.
    assert "riksbanken" in data["kallor"]
    assert data["kallor"]["riksbanken"]["cache_traffar"] == 0


# ---------------------------------------------------------------------------
# /kallor
# ---------------------------------------------------------------------------

def test_kallor_ar_publikt_och_utesluter_sparrade(client):
    res = client.get("/kallor")
    assert res.status_code == 200
    ider = {k["id"] for k in res.json()["kallor"]}

    assert "riksbanken" in ider
    # Spärrade källor (ARKITEKTUR.md §7) ska inte synas alls.
    assert "polisen_efterlysta" not in ider
    assert "bolagsverket_verkliga_huvudman" not in ider


# ---------------------------------------------------------------------------
# Acceptans 2 — fel ursprung avvisas
# ---------------------------------------------------------------------------

def test_fraga_fel_ursprung_avvisas(client):
    res = client.post(
        "/fraga",
        json={"fraga": "Vad är referensräntan?"},
        headers={"origin": "https://evil.example"},
    )
    assert res.status_code == 403


def test_fraga_ratt_ursprung_slapps_igenom(client, monkeypatch):
    register = Faktaregister()
    svar = SyntesSvar(kan_besvaras=False, stycken=(), forbehall=INGET_HITTAT)
    _sla_in_falska_motorer(monkeypatch, register, svar)

    res = client.post(
        "/fraga",
        json={"fraga": "Vad är referensräntan?"},
        headers={"origin": "https://quiet.nu"},
    )
    assert res.status_code == 200


# ---------------------------------------------------------------------------
# Acceptans 1 — SSE-ström med stycken och källpanel
# ---------------------------------------------------------------------------

def test_fraga_strommar_stycken_och_kallpanel(client, monkeypatch):
    register = Faktaregister()
    register.registrera(
        etikett="Referensränta",
        varde="2",
        enhet="procent",
        period="2026-08-13",
        kalla_id="riksbanken",
        myndighet="Riksbanken",
        licens="CC0",
        lank_manniska="https://www.riksbank.se/",
        lank_maskin="https://api.riksbank.se/swea/v1/Observations/SECBREFEFF/latest",
    )
    f1 = register.alla()[0].id

    svar = SyntesSvar(
        kan_besvaras=True,
        stycken=(Stycke(text="Referensräntan är 2 procent.", kallor=(f1,)),),
        forbehall=None,
    )
    _sla_in_falska_motorer(monkeypatch, register, svar)

    res = client.post("/fraga", json={"fraga": "Vad är referensräntan?"})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")

    handelser = _parsa_sse(res.text)
    namn = [h[0] for h in handelser]
    assert "stycke" in namn
    assert "kallor" in namn
    assert "klart" in namn

    stycke_data = next(d for n, d in handelser if n == "stycke")
    assert "Referensräntan är 2 procent." in stycke_data
    assert f1 in stycke_data

    kallor_data = next(d for n, d in handelser if n == "kallor")
    assert "Riksbanken" in kallor_data
    assert "lank_maskin" in kallor_data
    assert "SECBREFEFF" in kallor_data


def test_fraga_ej_besvarbar_strommar_fail_closed_text(client, monkeypatch):
    register = Faktaregister()
    svar = SyntesSvar(kan_besvaras=False, stycken=(), forbehall=INGET_HITTAT)
    _sla_in_falska_motorer(monkeypatch, register, svar)

    res = client.post("/fraga", json={"fraga": "Vad är meningen med livet?"})
    assert res.status_code == 200

    handelser = _parsa_sse(res.text)
    svar_data = next(d for n, d in handelser if n == "svar")
    assert INGET_HITTAT in svar_data
    assert '"kan_besvaras": false' in svar_data


# ---------------------------------------------------------------------------
# Acceptans 3 — kvot: anrop 51 avvisas
# ---------------------------------------------------------------------------

def test_femtioforsta_anropet_samma_dygn_avvisas(client, monkeypatch):
    register = Faktaregister()
    svar = SyntesSvar(kan_besvaras=False, stycken=(), forbehall=INGET_HITTAT)
    _sla_in_falska_motorer(monkeypatch, register, svar)

    for i in range(1, 51):
        res = client.post("/fraga", json={"fraga": f"Fråga nummer {i}"})
        assert res.status_code == 200, f"anrop {i} avvisades oväntat"

    res_51 = client.post("/fraga", json={"fraga": "Fråga nummer 51"})
    assert res_51.status_code == 429
    assert "gräns" in res_51.json()["detail"]


# ---------------------------------------------------------------------------
# Acceptans 4 — nyckeln syns aldrig
# ---------------------------------------------------------------------------

def test_nyckeln_syns_inte_i_nagot_svar(client, monkeypatch):
    register = Faktaregister()
    register.registrera(
        etikett="Referensränta", varde="2", kalla_id="riksbanken",
        myndighet="Riksbanken", licens="CC0",
        lank_manniska="https://www.riksbank.se/",
        lank_maskin="https://api.riksbank.se/swea/v1/Observations/SECBREFEFF/latest",
    )
    f1 = register.alla()[0].id
    svar = SyntesSvar(
        kan_besvaras=True,
        stycken=(Stycke(text="Referensräntan är 2 procent.", kallor=(f1,)),),
    )
    _sla_in_falska_motorer(monkeypatch, register, svar)

    for res in (
        client.get("/halsa"),
        client.get("/kallor"),
        client.post("/fraga", json={"fraga": "Vad är referensräntan?"}),
    ):
        assert _HEMLIG_TESTNYCKEL not in res.text


def test_klient_ip_litar_inte_pa_header_utan_betrodd_proxy(monkeypatch):
    """X-Forwarded-For sätts av vem som helst som når porten.

    Litar api:et alltid på den blir per-IP-kvoten verkningslös: en ny slumpad
    adress per anrop ger obegränsat antal frågor, bara bromsat av dygnstotalen.
    """
    import dataclasses
    import quiet_oppen_data.api as api_modul

    riktig = api_modul.las_konfig()

    class _Req:
        headers = {"x-forwarded-for": "9.9.9.9"}
        class client:  # noqa: N801
            host = "10.0.0.5"

    for betrodd, forvantad in ((True, "9.9.9.9"), (False, "10.0.0.5")):
        konfig = dataclasses.replace(
            riktig, site=dataclasses.replace(riktig.site, betrodd_proxy=betrodd)
        )
        monkeypatch.setattr(api_modul, "las_konfig", lambda k=konfig: k)
        assert api_modul._klient_ip(_Req()) == forvantad
