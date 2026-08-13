import pytest
import vcr
from quiet_oppen_data.adaptrar.pxweb import PxWebAdapter
from quiet_oppen_data.modeller import Fragplan

vcr_config = {
    "cassette_library_dir": "tests/kassetter",
    "record_mode": "once",
}

@vcr.use_cassette(**vcr_config)
def test_pxweb_lista_dimensioner():
    adapter = PxWebAdapter("scb_pxweb")
    
    # Anrop utan dimensioner (borde returnera valalternativ)
    plan = Fragplan(fraga="", extra={"tabell": "TAB6445"})
    poster = adapter.hamta(plan)
    
    # För TAB6445 (Snabb-KPI) finns det 3 dimensioner (PrelAggr, ContentsCode, Tid)
    assert len(poster) == 3
    assert "Tillåtna värden" in poster[0].varde
    # Ett av valalternativen ska vara Tid
    tider = [p for p in poster if "Tid" in p.etikett]
    assert len(tider) == 1
    assert "2026M07" in tider[0].varde or "2024" in tider[0].varde

@vcr.use_cassette(**vcr_config)
def test_pxweb_hamta_data():
    adapter = PxWebAdapter("scb_pxweb")
    
    # KPIF-XE för en månad
    plan = Fragplan(fraga="", extra={
        "tabell": "TAB6445",
        "dimensioner": {
            "PrelAggr": "SKPI03",
            "ContentsCode": "000007PM",
            "Tid": "2026M07"
        }
    })
    
    poster = adapter.hamta(plan)
    assert len(poster) == 1
    post = poster[0]
    
    assert "PxWeb-data" in post.etikett
    assert post.lank_maskin.endswith("/data")
    assert post.dimensioner["Tid"] == "2026M07"

@vcr.use_cassette(**vcr_config)
def test_pxweb_avvisar_stora_uttag():
    adapter = PxWebAdapter("scb_pxweb")
    
    # Simulera ett uttag på 200 000 celler
    # dimensioner är inte strikt validerade här utan räknas bara (len av listan)
    plan = Fragplan(fraga="", extra={
        "tabell": "TAB6445",
        "dimensioner": {
            "PrelAggr": ["val1"] * 200,
            "ContentsCode": ["val2"] * 10,
            "Tid": ["val3"] * 100 # 200 * 10 * 100 = 200 000
        }
    })
    
    poster = adapter.hamta(plan)
    assert len(poster) == 1
    assert "överskrider 150 000 celler" in poster[0].varde
