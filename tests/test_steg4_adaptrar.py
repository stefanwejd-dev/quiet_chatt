import pytest
import vcr
from quiet_oppen_data.adaptrar.riksbanken import RiksbankenAdapter
from quiet_oppen_data.adaptrar.vies import ViesAdapter
from quiet_oppen_data.modeller import Fragplan

# VCR-konfiguration för att spara kassetter i tests/kassetter/
vcr_config = {
    "cassette_library_dir": "tests/kassetter",
    "record_mode": "once",
}

@vcr.use_cassette(**vcr_config)
def test_riksbanken_adapter_hamta():
    adapter = RiksbankenAdapter()
    
    # Skapar plan enligt acceptanskrav
    plan = Fragplan(fraga="", extra={"serie": "SEKEURPMI", "typ": "senaste"})
    poster = adapter.hamta(plan)
    
    assert len(poster) == 1
    post = poster[0]
    
    # Validera Faktapost-struktur och innehåll
    assert post.etikett == "Riksbanken: SEKEURPMI"
    assert post.kalla_id == "riksbanken"
    assert post.varde != ""
    assert post.lank_maskin == "https://api.riksbank.se/swea/v1/Observations/Latest/SEKEURPMI"
    assert "riksbank.se" in post.lank_manniska

@vcr.use_cassette(**vcr_config)
def test_vies_adapter_hamta():
    adapter = ViesAdapter()
    
    # Skapar plan enligt acceptanskrav för ett VIES momsnummer.
    # Exempel på giltigt: "556036111101" (KTH, m.m. om det är giltigt. Om inte är det False)
    plan = Fragplan(fraga="", extra={"momsnr": "556036111101", "land": "SE"})
    poster = adapter.hamta(plan)
    
    assert len(poster) == 1
    post = poster[0]
    
    # isValid är "true" eller "false" i vår implementation (`str(is_valid).lower()`)
    assert post.varde in ("true", "false")
    assert post.kalla_id == "vies"
    assert post.lank_maskin == "https://ec.europa.eu/taxation_customs/vies/rest-api/ms/SE/vat/556036111101"

def test_beskriv_innehaller_korrekt_format():
    r_adapter = RiksbankenAdapter()
    v_adapter = ViesAdapter()
    
    r_spec = r_adapter.beskriv()
    assert r_spec["name"] == "riksbanken"
    assert "serie" in r_spec["input_schema"]["properties"]
    
    v_spec = v_adapter.beskriv()
    assert v_spec["name"] == "vies"
    assert "momsnr" in v_spec["input_schema"]["properties"]
