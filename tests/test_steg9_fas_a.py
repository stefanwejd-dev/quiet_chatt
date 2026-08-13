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
