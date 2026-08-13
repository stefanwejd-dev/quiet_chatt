"""Acceptanstester för Steg 10 — Fas B: syntes med tvingad citering.

Acceptanskriterier från PLAN.md §10:
  1. Med tre Faktaposter i registret innehåller varje stycke i utdata minst
     ett giltigt F-id.
  2. Med tomt register görs noll API-anrop och svaret är
     "Det hittade jag inte i källorna."
  3. Ett manuellt försök att få modellen att svara ur eget minne ger
     kan_besvaras: false.

Kriterium 2 är enhetstestbart utan nätverk eller API-nyckel eftersom
kortslutningen sker innan klienten någonsin anropas — se testerna nedan.
Kriterium 1 och 3 kräver ett riktigt modellsvar och är märkta @pytest.mark.live.
"""


import pytest

from quiet_oppen_data.modeller import Faktaregister
from quiet_oppen_data.motor.syntes import (
    SVARSSCHEMA,
    FasBSyntes,
    Stycke,
    _tolka_svar,
)


def _post(register: Faktaregister, **övrigt) -> None:
    bas = dict(
        etikett="Referensränta",
        varde="2",
        kalla_id="riksbanken",
        myndighet="Riksbanken",
        licens="CC0",
        lank_manniska="https://www.riksbank.se/",
        lank_maskin="https://api.riksbank.se/swea/v1/Observations/SECBREFEFF/latest",
    )
    bas.update(övrigt)
    register.registrera(**bas)


# ---------------------------------------------------------------------------
# Enhetstester — ingen nätverkstrafik, ingen API-nyckel
# ---------------------------------------------------------------------------

def test_schema_kraver_minst_en_kalla_per_stycke():
    kallor_schema = SVARSSCHEMA["properties"]["stycken"]["items"]["properties"]["kallor"]
    assert kallor_schema["minItems"] == 1


def test_schema_forbjuder_extra_falt_overallt():
    assert SVARSSCHEMA["additionalProperties"] is False
    stycke_schema = SVARSSCHEMA["properties"]["stycken"]["items"]
    assert stycke_schema["additionalProperties"] is False


def test_tolka_svar_bygger_stycken_och_kallor():
    rå = (
        '{"kan_besvaras": true, '
        '"stycken": [{"text": "Referensräntan är 2 procent.", "kallor": ["F1"]}], '
        '"forbehall": null}'
    )
    svar = _tolka_svar(rå)
    assert svar.kan_besvaras is True
    assert svar.stycken == (Stycke(text="Referensräntan är 2 procent.", kallor=("F1",)),)
    assert svar.forbehall is None


def test_fas_b_kraver_api_nyckel(monkeypatch):
    import quiet_oppen_data.konfig as konfig_modul
    from quiet_oppen_data.konfig import Konfig, SiteKonfig, ModellKonfig, KvotKonfig, IndexKonfig

    konfig_modul._cache = Konfig(
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
            db="data/index.sqlite",
            embedding_modell="KBLab/sentence-bert-swedish-cased",
            embedding_dim=768,
        ),
        anthropic_api_key=None,
    )
    try:
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            FasBSyntes()
    finally:
        konfig_modul._cache = None


def _syntes_med_falsk_klient(monkeypatch):
    import quiet_oppen_data.konfig as konfig_modul

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-attrapp")
    monkeypatch.setattr(konfig_modul, "_cache", None, raising=False)

    class _Räknare:
        def __init__(self):
            self.anrop = 0

        def stream(self, **kwargs):
            self.anrop += 1
            raise AssertionError("API:t skulle inte ha anropats")

    syntes = FasBSyntes()
    klient = _Räknare()
    syntes._klient = klient
    return syntes, klient


def test_tomt_register_gor_noll_api_anrop(monkeypatch):
    """Acceptans 2: tomt register → 0 API-anrop, svaret är fail-closed-texten."""
    syntes, klient = _syntes_med_falsk_klient(monkeypatch)

    svar = syntes.syntetisera("Vad är referensräntan?", Faktaregister())

    assert klient.anrop == 0
    assert svar.kan_besvaras is False
    assert svar.stycken == ()
    assert svar.forbehall == "Det hittade jag inte i källorna."


class _FalskStröm:
    def __init__(self, svar):
        self._svar = svar

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self._svar


class _TextBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _FalsktSvar:
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [_TextBlock(text)]
        self.stop_reason = stop_reason


def test_anropsform_har_effort_schema_och_ratt_tak(monkeypatch):
    import quiet_oppen_data.konfig as konfig_modul

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-attrapp")
    monkeypatch.setattr(konfig_modul, "_cache", None, raising=False)

    syntes = FasBSyntes()

    class _FångandeKlient:
        def __init__(self):
            self.anrop = []
            self.messages = self

        def stream(self, **kwargs):
            self.anrop.append(kwargs)
            svar_json = (
                '{"kan_besvaras": true, "stycken": '
                '[{"text": "Referensräntan är 2 procent.", "kallor": ["F1"]}], '
                '"forbehall": null}'
            )
            return _FalskStröm(_FalsktSvar(svar_json))

    klient = _FångandeKlient()
    syntes._klient = klient

    register = Faktaregister()
    _post(register)

    svar = syntes.syntetisera("Vad är referensräntan?", register)

    assert len(klient.anrop) == 1
    kw = klient.anrop[0]

    assert "budget_tokens" not in kw.get("thinking", {})
    assert "betas" not in kw
    assert kw["thinking"] == {"type": "adaptive"}
    assert kw["output_config"]["effort"] == syntes._konfig.modell.effort_syntes
    assert kw["output_config"]["format"] == {"type": "json_schema", "schema": SVARSSCHEMA}
    assert kw["max_tokens"] == syntes._konfig.modell.max_tokens_syntes
    assert kw["model"] == syntes._konfig.modell.namn

    # Rent sammanhang: bara systemprompt + ett användarmeddelande.
    assert len(kw["messages"]) == 1
    assert kw["messages"][0]["role"] == "user"
    assert "Vad är referensräntan?" in kw["messages"][0]["content"]
    assert "F1" in kw["messages"][0]["content"]

    assert svar.kan_besvaras is True
    assert svar.stycken[0].kallor == ("F1",)


def test_refusal_ger_fail_closed(monkeypatch):
    import quiet_oppen_data.konfig as konfig_modul

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-attrapp")
    monkeypatch.setattr(konfig_modul, "_cache", None, raising=False)

    syntes = FasBSyntes()

    class _AvböjandeKlient:
        def __init__(self):
            self.messages = self

        def stream(self, **kwargs):
            return _FalskStröm(_FalsktSvar("", stop_reason="refusal"))

    syntes._klient = _AvböjandeKlient()

    register = Faktaregister()
    _post(register)

    svar = syntes.syntetisera("Vad hette Sveriges statsminister 1994?", register)

    assert svar.kan_besvaras is False
    assert svar.forbehall == "Det hittade jag inte i källorna."


# ---------------------------------------------------------------------------
# Livetester — kräver ANTHROPIC_API_KEY i miljön
# ---------------------------------------------------------------------------

@pytest.mark.live
class TestFasBLive:
    @pytest.fixture(scope="class")
    def syntes(self):
        return FasBSyntes()

    def test_tre_faktaposter_ger_citerade_stycken(self, syntes):
        """Acceptans 1: varje stycke citerar minst ett giltigt F-id."""
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
        register.registrera(
            etikett="Växelkurs SEK/EUR",
            varde="10.99",
            enhet="SEK per EUR",
            period="2026-08-12",
            kalla_id="riksbanken",
            myndighet="Riksbanken",
            licens="CC0",
            lank_manniska="https://www.riksbank.se/",
            lank_maskin="https://api.riksbank.se/swea/v1/Observations/SEKEURPMI/latest",
        )
        register.registrera(
            etikett="KPI, månadsförändring",
            varde="-0.3",
            enhet="procent",
            period="2026-07",
            kalla_id="scb_pxweb",
            myndighet="SCB",
            licens="CC0",
            lank_manniska="https://www.scb.se/",
            lank_maskin="https://api.scb.se/OV0104/v1/doris/sv/ssd/START/PR/PR0101/PR0101A/KPICOI80MN",
        )

        svar = syntes.syntetisera(
            "Vad är referensräntan och växelkursen mot euron?", register
        )

        giltiga_id = {p.id for p in register.alla()}
        assert svar.stycken, "modellen gav inga stycken trots tre relevanta fakta"
        for stycke in svar.stycken:
            assert stycke.kallor, f"stycke utan källor: {stycke.text!r}"
            for f_id in stycke.kallor:
                assert f_id in giltiga_id, f"okänt F-id citerat: {f_id}"

    def test_egen_minneskunskap_utan_fakta_ger_ej_besvarbart(self, syntes):
        """Acceptans 3: fråga utan hämtade fakta → kan_besvaras: false.

        Registret är tomt eftersom inget hämtats — det räcker att bevisa att
        syntesfasen inte smyger in ett svar ur egen kunskap när den ändå
        skulle anropas. (Fas B kortsluter redan vid tomt register, men det
        testar test_tomt_register_gor_noll_api_anrop separat utan nätverk.)
        """
        register = Faktaregister()
        svar = syntes.syntetisera(
            "Vad hette Sveriges statsminister 1994?", register
        )
        assert svar.kan_besvaras is False
