import time
import httpx
import pytest
from quiet_oppen_data.adaptrar.transport import (
    EjAktiveradKalla,
    SparradKalla,
    hamta_json,
)
from quiet_oppen_data.register import Kalla


def test_transport_sparrad_kastar_undantag():
    with pytest.raises(SparradKalla):
        hamta_json("polisen_efterlysta", "GET", "https://polisen.se/test")

def test_transport_cache_fungerar(monkeypatch, isolerad_cache):
    anrop = 0
    def mock_request(*args, **kwargs):
        nonlocal anrop
        anrop += 1
        class MockRes:
            status_code = 200
            headers: dict = {}
            request = None
            def raise_for_status(self): pass
            def json(self): return {"data": "ok"}
        return MockRes()
        
    monkeypatch.setattr(httpx.Client, "request", mock_request)
    
    # Kör via riksbanken som har cache_ttl > 0
    res1 = hamta_json("riksbanken", "GET", "https://api.riksbank.se/mock_1")
    assert res1 == {"data": "ok"}
    assert anrop == 1
    
    res2 = hamta_json("riksbanken", "GET", "https://api.riksbank.se/mock_1")
    assert res2 == {"data": "ok"}
    assert anrop == 1  # Inget nytt anrop pga cache!

def test_generisk_json_avvisar_okand_vard():
    with pytest.raises(ValueError, match="tillåten"):
        hamta_json("_generisk_json", "GET", "https://exempel.invalid/x")


@pytest.mark.parametrize("kalla_id", ["_generisk_rowstore", "_generisk_pxweb"])
def test_alla_generiska_avvisar_okand_vard(kalla_id):
    """Värdkontrollen gäller varje generisk adapter, inte bara _generisk_json.

    Risken är densamma oavsett protokoll: URL:en kommer från modellen, och utan
    kontrollen kan den peka på vad som helst.
    """
    with pytest.raises(ValueError, match="tillåten"):
        hamta_json(kalla_id, "GET", "https://exempel.invalid/x")


def test_ej_aktiverad_kalla_kastar(monkeypatch):
    """En källa med aktiverad: false får inte anropas.

    bolagsverket_hvd har ingen bekräftad sökväg (ARKITEKTUR.md §0). Utan den
    här spärren skulle ett anrop gå ut mot en gissad endpoint.
    """
    anrop = []

    def sabotage(*args, **kwargs):
        anrop.append(args)
        raise AssertionError("HTTP-anrop skulle aldrig ha gjorts")

    monkeypatch.setattr(httpx.Client, "request", sabotage)

    with pytest.raises(EjAktiveradKalla, match="inte aktiverad"):
        hamta_json("bolagsverket_hvd", "GET", "https://gw.api.bolagsverket.se/x")

    assert anrop == [], "spärren måste slå till innan nätverkstrafik"

def test_transport_ko_haller_takt(monkeypatch, isolerad_cache):
    """Testar att token bucket blockerar vid för många anrop."""
    anrop = 0
    def mock_request(*args, **kwargs):
        nonlocal anrop
        anrop += 1
        class MockRes:
            status_code = 200
            headers: dict = {}
            request = None
            def raise_for_status(self): pass
            def json(self): return {"data": "ok"}
        return MockRes()
        
    monkeypatch.setattr(httpx.Client, "request", mock_request)
    
    # Mocka time
    virtual_time = 0.0
    sleeps = []
    
    def mock_monotonic():
        return virtual_time
        
    def mock_sleep(s):
        nonlocal virtual_time
        sleeps.append(s)
        virtual_time += s
        
    monkeypatch.setattr(time, "monotonic", mock_monotonic)
    monkeypatch.setattr(time, "sleep", mock_sleep)
    
    # scb_pxweb har { anrop: 30, per_sekunder: 10 } => kapacitet 30, påfyllnad 3/s.
    # 40 OLIKA URL:er, annars svarar cachen och kön testas aldrig.
    for i in range(40):
        hamta_json("scb_pxweb", "GET", f"https://api.scb.se/mock_{i}")
        
    assert anrop == 40
    # De första 30 går igenom direkt (capacity=30)
    # De sista 10 kostar 10 / 3 = 3.33 sekunder total sleep
    tot_sleep = sum(sleeps)
    assert tot_sleep >= 3.0, f"Totalt sleep var {tot_sleep}, förväntat >= 3.0"


def test_omforsok_vid_429(monkeypatch, isolerad_cache):
    """Ett 429 ska ge omförsök, inte tolkas som 'källan hade inget att säga'.

    Utan detta blev Riksbankens rate limit till ett tomt svar, och boten
    rapporterade att den inte hittade något trots att uppgiften fanns.
    """
    import quiet_oppen_data.adaptrar.transport as transport

    svar = [429, 429, 200]
    sovit: list[float] = []
    monkeypatch.setattr(transport.time, "sleep", lambda s: sovit.append(s))

    class _Res:
        def __init__(self, kod):
            self.status_code = kod
            self.headers = {}
            self.request = None
        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError("fel", request=None, response=self)
        def json(self):
            return {"ok": True}

    monkeypatch.setattr(httpx.Client, "request", lambda *a, **k: _Res(svar.pop(0)))

    assert hamta_json("riksbanken", "GET", "https://api.riksbank.se/mock_429") == {"ok": True}
    assert svar == [], "alla tre svaren ska ha konsumerats"
    assert sovit == [1.0, 2.0], f"exponentiell backoff förväntad, fick {sovit}"


def test_permanent_fel_gor_inga_omforsok(monkeypatch, isolerad_cache):
    """404 blir inte bättre av att göras om — kasta direkt."""
    import quiet_oppen_data.adaptrar.transport as transport

    anrop = {"n": 0}
    monkeypatch.setattr(transport.time, "sleep", lambda s: pytest.fail("skulle inte sova"))

    class _Res:
        status_code = 404
        headers: dict = {}
        request = None
        def raise_for_status(self):
            raise httpx.HTTPStatusError("404", request=None, response=self)

    def rakna(*a, **k):
        anrop["n"] += 1
        return _Res()

    monkeypatch.setattr(httpx.Client, "request", rakna)

    with pytest.raises(httpx.HTTPStatusError):
        hamta_json("riksbanken", "GET", "https://api.riksbank.se/mock_404")
    assert anrop["n"] == 1
