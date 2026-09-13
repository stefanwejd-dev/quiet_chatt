"""Statushändelser från fas A — servern berättar vad den gör medan man väntar.

Bakgrund: fas A kan ta 30–90 sekunder, och widgeten visade en statisk
"Tänker …" hela tiden. Nu rapporterar loopen varje verktygsanrop som en
SSE-händelse `status`.

Den ovillkorliga regeln som testerna vaktar: `text` byggs **enbart** ur
källregistrets myndighetsnamn eller ur den fasta frasordlistan i
motor/hamtning.py. Aldrig ur frågan, aldrig ur verktygens indata eller utdata —
kanalen ser systemgenererad ut för besökaren, och måste därför också vara det.

Testerna täcker:
  * statustext_for_verktyg för varje gren (myndighet, beräkning, okänt verktyg,
    känd källa utan myndighetsnamn)
  * att frågetexten aldrig kan läcka in i en statustext
  * FasALopp.hamta med attrapp-callback och attrapp-klient
  * att ett undantag i callbacken inte påverkar resultatet
  * att hamta() utan callback beter sig som förut
  * att SSE-strömmen bär minst en `status` före första `stycke`, och
    "Sammanställer svaret …" före `kallor`
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import quiet_oppen_data.api as api_module
import quiet_oppen_data.konfig as konfig_modul
import quiet_oppen_data.motor.hamtning as hamtning
from quiet_oppen_data.konfig import (
    IndexKonfig,
    Konfig,
    KvotKonfig,
    ModellKonfig,
    SiteKonfig,
)
from quiet_oppen_data.modeller import Faktaregister
from quiet_oppen_data.motor.hamtning import (
    STATUS_BERAKNAR,
    STATUS_SAMMANSTALLER,
    STATUS_SOKER_GENERISKT,
    FasALopp,
    statustext_for_verktyg,
)
from quiet_oppen_data.motor.syntes import Stycke, SyntesSvar

_HEMLIG_TESTNYCKEL = "sk-ant-test-hemlig-nyckel-som-aldrig-far-synas"


# ---------------------------------------------------------------------------
# statustext_for_verktyg — ren funktion, ingen klient
# ---------------------------------------------------------------------------

_VERKTYGSKALLOR = {
    "riksbanken_hamta_serie": "riksbanken",
    "utan_myndighet": "kalla_som_saknar_myndighet",
}
_MYNDIGHETSNAMN = {"riksbanken": "Sveriges riksbank"}


def test_kant_verktyg_ger_myndighetsnamnet():
    text, kalla_id = statustext_for_verktyg(
        "riksbanken_hamta_serie", _VERKTYGSKALLOR, _MYNDIGHETSNAMN
    )
    assert text == "Söker hos Sveriges riksbank …"
    assert kalla_id == "riksbanken"


def test_berakningsverktyg_rapporteras_utan_detaljer():
    """Vilken beräkning som görs är en del av svaret, inte av väntan."""
    verktygsnamn = sorted(hamtning.berakningar.VERKTYGSNAMN)[0]
    text, kalla_id = statustext_for_verktyg(verktygsnamn, _VERKTYGSKALLOR, _MYNDIGHETSNAMN)
    assert text == STATUS_BERAKNAR == "Beräknar …"
    assert kalla_id is None


def test_okant_verktyg_ger_generisk_fras():
    """Hellre trubbig än fel."""
    text, kalla_id = statustext_for_verktyg("nagot_helt_okant", _VERKTYGSKALLOR, _MYNDIGHETSNAMN)
    assert text == STATUS_SOKER_GENERISKT
    assert kalla_id is None


def test_kalla_utan_myndighetsnamn_ger_generisk_fras():
    text, kalla_id = statustext_for_verktyg("utan_myndighet", _VERKTYGSKALLOR, _MYNDIGHETSNAMN)
    assert text == STATUS_SOKER_GENERISKT
    assert kalla_id == "kalla_som_saknar_myndighet"


def test_fragetexten_kan_inte_lacka_in_i_statustexten():
    """Invarianten: texten byggs ur registret eller frasordlistan, inget annat.

    Funktionen tar inte ens emot frågan — testet vaktar signaturen lika mycket
    som utfallet, så att en framtida 'hjälpsam' utökning syns direkt.
    """
    import inspect

    parametrar = set(inspect.signature(statustext_for_verktyg).parameters)
    assert parametrar == {"verktygsnamn", "verktygskallor", "myndighetsnamn"}

    # Även med ett verktygsnamn som ser ut som en fråga blir utfallet en fras
    # ur ordlistan — verktygsnamnet självt skrivs aldrig ut.
    hemlig = "Vad är min sjukdomsdiagnos?"
    text, _ = statustext_for_verktyg(hemlig, _VERKTYGSKALLOR, _MYNDIGHETSNAMN)
    assert text == STATUS_SOKER_GENERISKT
    assert hemlig not in text


def test_alla_texter_kommer_ur_registret_eller_ordlistan():
    """Varje verkligt verktyg ska ge en text som går att härleda till en källa."""
    namn = hamtning._bygg_myndighetsnamn()
    tillatna_myndigheter = {f"Söker hos {m} …" for m in namn.values()}
    fasta = {STATUS_SOKER_GENERISKT, STATUS_BERAKNAR}

    verktygskallor = {f"verktyg_{kid}": kid for kid in namn}
    for verktygsnamn in list(verktygskallor) + sorted(hamtning.berakningar.VERKTYGSNAMN):
        text, _ = statustext_for_verktyg(verktygsnamn, verktygskallor, namn)
        assert text in tillatna_myndigheter | fasta, text


# ---------------------------------------------------------------------------
# FasALopp.hamta med attrapp-klient
# ---------------------------------------------------------------------------

class _FalskStrom:
    def __init__(self, svar):
        self._svar = svar

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self._svar


class _Verktygsblock:
    type = "tool_use"
    id = "toolu_attrapp"

    def __init__(self, namn):
        self.name = namn
        self.input = {}


class _SvarMedVerktyg:
    stop_reason = "tool_use"
    usage = None

    def __init__(self, *namn):
        self.content = [_Verktygsblock(n) for n in namn]


class _SvarSlut:
    stop_reason = "end_turn"
    content: list = []
    usage = None


class _FalskKlient:
    """Returnerar ett verktygsanrop först, sedan end_turn. Ingen nätverkstrafik."""

    def __init__(self, *verktygsnamn):
        self._svar = [_SvarMedVerktyg(*verktygsnamn), _SvarSlut()]
        self.messages = self

    def stream(self, **kwargs):
        return _FalskStrom(self._svar.pop(0) if self._svar else _SvarSlut())


@pytest.fixture
def lopp(monkeypatch):
    """FasALopp med attrapp-nyckel. Verktygskörningen stubbas bort — testet
    handlar om statuskanalen, inte om adaptrarna (och ska inte röra nätet)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-attrapp")
    monkeypatch.setattr(konfig_modul, "_cache", None, raising=False)
    monkeypatch.setattr(hamtning, "_kör_verktyg", lambda *a, **kw: "{}")
    yield FasALopp()
    konfig_modul.nollstall()


def _ett_riksbanksverktyg(lopp) -> str:
    namn = [v for v, kid in lopp._verktygskallor.items() if kid == "riksbanken"]
    assert namn, "riksbanken saknar verktyg — fixturen behöver ses över"
    return sorted(namn)[0]


def test_callback_anropas_per_verktygsanrop(lopp):
    verktyg = _ett_riksbanksverktyg(lopp)
    lopp._klient = _FalskKlient(verktyg)

    handelser = []
    lopp.hamta("Vad är referensräntan?", status_callback=lambda t, k: handelser.append((t, k)))

    assert handelser == [("Söker hos Sveriges riksbank …", "riksbanken")]


def test_callback_anropas_en_gang_per_block(lopp):
    verktyg = _ett_riksbanksverktyg(lopp)
    lopp._klient = _FalskKlient(verktyg, verktyg)

    handelser = []
    lopp.hamta("En fråga", status_callback=lambda t, k: handelser.append((t, k)))

    assert len(handelser) == 2


def test_undantag_i_callbacken_faller_inte_hamtningen(lopp):
    """Statusen är presentation; hämtningen är uppdraget."""
    verktyg = _ett_riksbanksverktyg(lopp)
    lopp._klient = _FalskKlient(verktyg)

    def trasig(text, kalla_id):
        raise RuntimeError("statuskanalen är trasig")

    resultat = lopp.hamta("En fråga", status_callback=trasig)

    assert isinstance(resultat, hamtning.HamtningsResultat)
    assert resultat.iterationer >= 1


def test_utan_callback_som_forut(lopp):
    """Direktanrop utan callback ska bete sig exakt som tidigare."""
    verktyg = _ett_riksbanksverktyg(lopp)
    lopp._klient = _FalskKlient(verktyg)

    resultat = lopp.hamta("En fråga")

    assert isinstance(resultat, hamtning.HamtningsResultat)
    assert resultat.register.ar_tom()  # _kör_verktyg är stubbad


# ---------------------------------------------------------------------------
# POST /fraga — statushändelserna i SSE-strömmen
# ---------------------------------------------------------------------------

@pytest.fixture
def klient(tmp_path, monkeypatch):
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
    yield TestClient(api_module.app)
    konfig_modul.nollstall()


def _parsa_sse(text: str) -> list[tuple[str, str]]:
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


@pytest.fixture
def strom(klient, monkeypatch):
    """Attrapp-motorer där fas A rapporterar två statushändelser."""
    register = Faktaregister()
    register.registrera(
        etikett="Referensränta",
        varde="2",
        enhet="procent",
        period="2026-08-13",
        kalla_id="riksbanken",
        myndighet="Sveriges riksbank",
        licens="CC0",
        lank_manniska="https://www.riksbank.se/",
        lank_maskin="https://api.riksbank.se/swea/v1/Observations/SECBREFEFF/latest",
    )
    f1 = register.alla()[0].id

    def falsk_hamta(fraga, status_callback=None):
        if status_callback is not None:
            status_callback("Söker hos Sveriges riksbank …", "riksbanken")
            status_callback(STATUS_BERAKNAR, None)
        return SimpleNamespace(
            register=register,
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
        )

    svar = SyntesSvar(
        kan_besvaras=True,
        stycken=(Stycke(text="Referensräntan är 2 procent.", kallor=(f1,)),),
        forbehall=None,
    )
    monkeypatch.setattr(api_module, "_fas_a", SimpleNamespace(hamta=falsk_hamta))
    monkeypatch.setattr(api_module, "_fas_c", SimpleNamespace(kor=lambda f, r: svar))
    return klient


def test_status_kommer_fore_forsta_stycket(strom):
    res = strom.post("/fraga", json={"fraga": "Vad är referensräntan?"})
    assert res.status_code == 200

    namn = [n for n, _ in _parsa_sse(res.text)]
    assert "status" in namn
    assert namn.index("status") < namn.index("stycke")


def test_statustexterna_nar_fram_oforandrade(strom):
    res = strom.post("/fraga", json={"fraga": "Vad är referensräntan?"})
    texter = [
        json.loads(d)["text"] for n, d in _parsa_sse(res.text) if n == "status"
    ]
    assert texter[:2] == ["Söker hos Sveriges riksbank …", STATUS_BERAKNAR]
    assert STATUS_SAMMANSTALLER in texter


def test_sammanstaller_kommer_fore_kallorna(strom):
    res = strom.post("/fraga", json={"fraga": "Vad är referensräntan?"})
    handelser = _parsa_sse(res.text)
    namn = [n for n, _ in handelser]

    index_sammanstaller = next(
        i for i, (n, d) in enumerate(handelser)
        if n == "status" and json.loads(d)["text"] == STATUS_SAMMANSTALLER
    )
    assert index_sammanstaller < namn.index("kallor")


def test_kalla_id_foljer_med(strom):
    res = strom.post("/fraga", json={"fraga": "Vad är referensräntan?"})
    data = [json.loads(d) for n, d in _parsa_sse(res.text) if n == "status"]
    assert data[0]["kalla_id"] == "riksbanken"
    assert data[1]["kalla_id"] is None


def test_ovriga_handelser_ar_oforandrade(strom):
    """Statuskanalen får inte rubba svarshändelserna."""
    res = strom.post("/fraga", json={"fraga": "Vad är referensräntan?"})
    namn = [n for n, _ in _parsa_sse(res.text)]
    assert [n for n in namn if n != "status"] == ["stycke", "kallor", "klart"]


def test_fragetexten_finns_inte_i_nagon_statushandelse(strom):
    """Ingen del av frågan får läcka ut i en kanal som ser systemgenererad ut."""
    fraga = "Har jag rätt till sjukpenning efter min operation?"
    res = strom.post("/fraga", json={"fraga": fraga})

    for n, d in _parsa_sse(res.text):
        if n == "status":
            assert "operation" not in d
            assert "sjukpenning" not in d
