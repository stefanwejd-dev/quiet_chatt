"""Acceptanstester för Steg 9 — Fas A: planerare och hämtningsloop.

Dessa tester gör RIKTIGA API-anrop mot Anthropic och mot myndigheternas API:er.
De är märkta med @pytest.mark.live och hoppas normalt över i CI.
Kör dem manuellt: pytest -m live -v

Acceptanskriterier från PLAN.md §9:
  1. "Vad är referensräntan?" → ≥1 Faktapost från Riksbanken
  2. "Hur många upphandlingar annonserades i Skåne senaste månaden?" → ≥1 från TED
  3. "Vad är meningen med livet?" → 0 Faktaposter, loopen avslutas rent
  4. cache_read_input_tokens > 0 på andra frågan i rad (prompt-cache träffar)
"""

import logging

import pytest

from quiet_oppen_data.motor.hamtning import FasALopp, HamtningsResultat

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enhetstest — ingen nätverkstrafik, ingen API-nyckel
# ---------------------------------------------------------------------------

def test_hamtningsresultat_är_dataclass():
    """Strukturellt test — HamtningsResultat är rätt typ."""
    from quiet_oppen_data.modeller import Faktaregister
    res = HamtningsResultat(
        register=Faktaregister(),
        cache_read_tokens=0,
        cache_write_tokens=0,
        input_tokens=0,
        output_tokens=0,
        iterationer=0,
    )
    assert res.register.ar_tom()
    assert res.iterationer == 0


def test_fas_a_lopp_kräver_api_nyckel(monkeypatch):
    """FasALopp ska kasta om ANTHROPIC_API_KEY saknas."""
    import quiet_oppen_data.konfig as konfig_modul
    from quiet_oppen_data.konfig import Konfig, SiteKonfig, ModellKonfig, KvotKonfig, IndexKonfig

    konfig_modul._cache = Konfig(
        site=SiteKonfig(domain="quiet.nu"),
        modell=ModellKonfig(
            namn="claude-opus-5",
            effort_hamtning="high",
            effort_syntes="medium",
            max_verktygsvarv=8
        ),
        kvot=KvotKonfig(
            fragor_per_ip_per_dygn=50,
            fragor_totalt_per_dygn=2000,
            kostnadstak_sek_per_manad=1000
        ),
        index=IndexKonfig(
            db="data/index.sqlite",
            embedding_modell="KBLab/sentence-bert-swedish-cased",
            embedding_dim=768
        ),
        anthropic_api_key=None,   # saknas
    )
    try:
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            FasALopp()
    finally:
        konfig_modul._cache = None


# ---------------------------------------------------------------------------
# Livetester — kräver ANTHROPIC_API_KEY i miljön
# ---------------------------------------------------------------------------

@pytest.mark.live
class TestFasALive:
    """Samlade live-tester som återanvänder en enda FasALopp-instans."""

    @pytest.fixture(scope="class")
    def lopp(self):
        """Instansiera FasALopp en gång per testklass — dyrt men nödvändigt."""
        return FasALopp()

    def test_referensranta_ger_riksbanken_post(self, lopp):
        """Acceptans 1: referensräntan → minst en Faktapost med kalla_id riksbanken."""
        res = lopp.hamta("Vad är referensräntan just nu?")

        assert isinstance(res, HamtningsResultat)
        riksbanken_poster = [
            p for p in res.register.alla()
            if p.kalla_id == "riksbanken"
        ]
        assert len(riksbanken_poster) >= 1, (
            f"Förväntades minst 1 Riksbanken-post men fick {len(res.register.alla())} total: "
            f"{[p.kalla_id for p in res.register.alla()]}"
        )
        logger.info(
            "Referensränta: %s poster, kalla_id=%s, varde=%s",
            len(riksbanken_poster),
            riksbanken_poster[0].kalla_id,
            riksbanken_poster[0].varde,
        )

    def test_upphandlingar_skane_ger_ted_post(self, lopp):
        """Acceptans 2: Skåne-upphandlingar → minst en Faktapost med kalla_id ted."""
        res = lopp.hamta(
            "Hur många upphandlingar annonserades i Skåne senaste månaden?"
        )

        ted_poster = [p for p in res.register.alla() if p.kalla_id == "ted"]
        assert len(ted_poster) >= 1, (
            f"Förväntades minst 1 TED-post men fick {len(res.register.alla())} total."
        )

    def test_meningslosa_fragan_ger_noll_poster(self, lopp):
        """Acceptans 3: meningslös fråga → 0 Faktaposter, loopen avslutas rent."""
        res = lopp.hamta("Vad är meningen med livet?")

        assert len(res.register) == 0, (
            f"Förväntades 0 poster men fick {len(res.register)}: "
            f"{[p.etikett for p in res.register.alla()]}"
        )
        # Loopen ska ha avslutats utan att krascha
        assert res.iterationer >= 1

    def test_andra_fragan_träffar_cache(self, lopp):
        """Acceptans 4: andra frågan i rad → cache_read_input_tokens > 0."""
        # Ställ fråga 1 (värmer cachen)
        lopp.hamta("Vad är styrräntan?")
        # Ställ fråga 2 (ska träffa cachen)
        res2 = lopp.hamta("Vad är referensräntan?")

        assert res2.cache_read_tokens > 0, (
            f"Förväntades cache_read_tokens > 0 på andra anropet, "
            f"fick {res2.cache_read_tokens}. "
            "Systemprompten eller verktygslistan är troligen inte frusen."
        )
        logger.info(
            "Cache-bevis: cache_read=%d, cache_write=%d",
            res2.cache_read_tokens,
            res2.cache_write_tokens,
        )


# ---------------------------------------------------------------------------
# Regressionstest — anropsformen mot Anthropic
#
# Bakgrund: den först incheckade versionen skickade thinking.budget_tokens och
# beta-flaggorna "extended-thinking-*"/"prompt-caching-*". Båda ger HTTP 400 på
# Opus 5 — fas A kunde alltså inte köras alls. Ingenting fångade det, eftersom
# de enda testerna som rörde API:t var @pytest.mark.live och hoppades över.
#
# Testet nedan gör ingen nätverkstrafik. Det inspekterar bara vad koden SKULLE
# ha skickat, vilket räcker för att fånga hela den här felklassen i CI.
# ---------------------------------------------------------------------------

class _FalskStröm:
    def __init__(self, svar):
        self._svar = svar

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self._svar


class _FalsktSvar:
    stop_reason = "end_turn"
    content: list = []
    usage = None


class _FalskKlient:
    """Spelar in anropsargumenten i stället för att kontakta Anthropic."""

    def __init__(self):
        self.anrop: list[dict] = []
        self.messages = self
        # Om koden råkar gå via beta-namnrymden vill vi upptäcka det.
        self.beta = self

    def stream(self, **kwargs):
        self.anrop.append(kwargs)
        return _FalskStröm(_FalsktSvar())


def _lopp_med_falsk_klient(monkeypatch):
    import quiet_oppen_data.konfig as konfig_modul
    from quiet_oppen_data.motor.hamtning import FasALopp

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-attrapp")
    monkeypatch.setattr(konfig_modul, "_cache", None, raising=False)

    lopp = FasALopp()
    klient = _FalskKlient()
    lopp._klient = klient
    return lopp, klient


def test_anropsform_saknar_borttagna_parametrar(monkeypatch):
    lopp, klient = _lopp_med_falsk_klient(monkeypatch)
    lopp.hamta("testfråga")

    assert klient.anrop, "inget anrop gjordes"
    kw = klient.anrop[0]

    # budget_tokens är borttaget på Opus 5 och ger 400.
    assert "budget_tokens" not in kw.get("thinking", {}), (
        "thinking.budget_tokens ger HTTP 400 på Opus 5 — djupet styrs med effort"
    )
    # Prompt-caching och thinking är GA; de gamla beta-flaggorna ger 400.
    assert "betas" not in kw, "beta-flaggor ska inte skickas — funktionerna är GA"

    assert kw["thinking"] == {"type": "adaptive"}


def test_anropsform_har_effort_och_taket_ur_config(monkeypatch):
    from quiet_oppen_data.konfig import las

    lopp, klient = _lopp_med_falsk_klient(monkeypatch)
    lopp.hamta("testfråga")
    kw = klient.anrop[0]
    modell = las().modell

    # Modulens docstring lovade effort men koden skickade det aldrig.
    assert kw["output_config"] == {"effort": modell.effort_hamtning}
    assert kw["max_tokens"] == modell.max_tokens_hamtning
    assert kw["model"] == modell.namn


def test_cache_brytpunkter_och_deterministisk_ordning(monkeypatch):
    lopp, klient = _lopp_med_falsk_klient(monkeypatch)
    lopp.hamta("testfråga")
    kw = klient.anrop[0]

    assert kw["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert kw["tools"][-1]["cache_control"] == {"type": "ephemeral"}

    namn = [t["name"] for t in kw["tools"]]
    assert namn == sorted(namn), "verktygslistan måste vara sorterad — annars slås cachen sönder"
