import vcr

from quiet_oppen_data.adaptrar.riksbanken import RiksbankenAdapter
from quiet_oppen_data.adaptrar.vies import ViesAdapter
from quiet_oppen_data.modeller import Faktaregister, Faktautkast, Fragplan

vcr_config = {
    "cassette_library_dir": "tests/kassetter",
    "record_mode": "once",
}


@vcr.use_cassette(**vcr_config)
def test_riksbanken_adapter_hamta(isolerad_cache):
    adapter = RiksbankenAdapter()

    plan = Fragplan(fraga="", extra={"serie": "SEKEURPMI"})
    utkast = adapter.hamta(plan)

    assert len(utkast) == 1
    u = utkast[0]

    # Adaptrar returnerar utkast, aldrig färdiga Faktaposter — bara
    # Faktaregister får mynta F-id (ARKITEKTUR.md §3.4).
    assert isinstance(u, Faktautkast)
    assert not hasattr(u, "id")

    assert u.kalla_id == "riksbanken"
    assert "SEKEURPMI" in u.etikett
    assert u.varde != ""
    assert u.lank_maskin == "https://api.riksbank.se/swea/v1/Observations/Latest/SEKEURPMI"
    assert "riksbank.se" in u.lank_manniska


@vcr.use_cassette(**vcr_config)
def test_riksbanken_utkast_gar_att_registrera(isolerad_cache):
    """Utkastet ska passera Faktaregistrets validering och få ett F-id."""
    utkast = RiksbankenAdapter().hamta(Fragplan(fraga="", extra={"serie": "SEKEURPMI"}))

    reg = Faktaregister()
    poster = reg.registrera_alla(utkast)

    assert [p.id for p in poster] == ["F1"]
    assert poster[0].lank_maskin == utkast[0].lank_maskin
    assert poster[0].hamtad is not None


@vcr.use_cassette(**vcr_config)
def test_vies_adapter_hamta(isolerad_cache):
    adapter = ViesAdapter()

    plan = Fragplan(fraga="", extra={"momsnr": "556036111101", "land": "SE"})
    utkast = adapter.hamta(plan)

    # Minst kontrollposten; namnposten tillkommer bara om VIES lämnar ut namnet.
    assert len(utkast) >= 1
    kontroll = utkast[0]

    assert kontroll.kalla_id == "vies"
    # Etiketten beskriver vad som mätts, värdet bär utfallet. Etiketten får
    # aldrig påstå giltighet — "…giltigt = ogiltigt" är en läsfälla för
    # syntesmodellen.
    assert "giltigt" not in kontroll.etikett
    assert kontroll.varde in ("giltigt", "ogiltigt")
    assert kontroll.lank_maskin == (
        "https://ec.europa.eu/taxation_customs/vies/rest-api/ms/SE/vat/556036111101"
    )


def test_adaptrar_returnerar_tomt_utan_parametrar():
    """Utan obligatorisk parameter ska adaptern returnera tomt, inte gissa."""
    assert RiksbankenAdapter().hamta(Fragplan(fraga="", extra={})) == []
    assert ViesAdapter().hamta(Fragplan(fraga="", extra={})) == []


def test_beskriv_innehaller_korrekt_format():
    r_spec = RiksbankenAdapter().beskriv()[0]
    assert r_spec["name"] == "riksbanken"
    assert "serie" in r_spec["input_schema"]["properties"]

    v_spec = ViesAdapter().beskriv()[0]
    assert v_spec["name"] == "vies"
    assert "momsnr" in v_spec["input_schema"]["properties"]
