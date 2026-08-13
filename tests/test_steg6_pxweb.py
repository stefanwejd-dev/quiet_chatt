import logging

import vcr

from quiet_oppen_data.adaptrar.pxweb import PxWebAdapter
from quiet_oppen_data.modeller import Faktaregister, Fragplan

vcr_config = {
    "cassette_library_dir": "tests/kassetter",
    "record_mode": "once",
}

# TAB6445 = Snabb-KPI, tre dimensioner: PrelAggr, ContentsCode, Tid.
TABELL = "TAB6445"


@vcr.use_cassette(**vcr_config)
def test_pxweb_lista_dimensioner(isolerad_cache):
    """Utan angivna dimensioner ska adaptern visa valen, inte gissa en skiva."""
    utkast = PxWebAdapter("scb_pxweb").hamta(Fragplan(fraga="", extra={"tabell": TABELL}))

    assert len(utkast) == 3
    assert all(u.dataset == TABELL for u in utkast)

    tid = [u for u in utkast if u.dimensioner.get("dimension") == "Tid"]
    assert len(tid) == 1
    assert "2026M07" in tid[0].varde
    # Maskinlänken ska peka på metadata-anropet som gav valen.
    assert tid[0].lank_maskin.endswith("/metadata?lang=sv")


@vcr.use_cassette(**vcr_config)
def test_pxweb_saknad_dimension_ger_valalternativ(isolerad_cache):
    """En ofullständig dimensionsangivelse får inte tolkas som ett giltigt uttag."""
    utkast = PxWebAdapter("scb_pxweb").hamta(
        Fragplan(fraga="", extra={"tabell": TABELL, "dimensioner": {"Tid": ["2026M07"]}})
    )
    assert len(utkast) == 3
    assert all("Giltiga värden" in u.etikett for u in utkast)


@vcr.use_cassette(**vcr_config)
def test_pxweb_hamta_data(isolerad_cache):
    """Ett fullständigt uttag ger en post per cell, med dimensioner utskrivna."""
    plan = Fragplan(
        fraga="",
        extra={
            "tabell": TABELL,
            "dimensioner": {
                "PrelAggr": ["SKPI01"],
                "ContentsCode": ["000007PK"],
                "Tid": ["2026M07"],
            },
        },
    )
    utkast = PxWebAdapter("scb_pxweb").hamta(plan)

    assert len(utkast) == 1
    u = utkast[0]

    # Värdet ska vara ett enskilt tal, inte en datablob.
    assert float(u.varde) == -0.3
    assert u.period == "2026M07"
    assert u.dataset == TABELL

    # Dimensionerna ska vara läsbara etiketter, inte koder — de renderas i
    # källpanelen och är det som gör att användaren kan se vilken skiva som togs.
    assert "månad" in u.dimensioner
    assert u.dimensioner["månad"] == "2026M07"
    assert len(u.dimensioner) == 3

    # json-stat2 måste begäras via query-parametern. Med responseFormat i
    # POST-kroppen svarar SCB med PX i iso-8859-1 i stället.
    assert "outputFormat=json-stat2" in u.lank_maskin

    # Utkastet ska gå rakt in i registret.
    post = Faktaregister().registrera_utkast(u)
    assert post.id == "F1"


@vcr.use_cassette(**vcr_config)
def test_pxweb_avvisar_stora_uttag(isolerad_cache, caplog):
    """Ett uttag över takregeln avvisas före anropet — och ger ingen faktapost.

    Tidigare returnerades felet som en Faktapost med tomma länkar. Ett fel är
    inte ett faktum: hade det citerats hade svaret innehållit ett felmeddelande
    presenterat som en uppgift, utan källa att klicka på.
    """
    plan = Fragplan(
        fraga="",
        extra={
            "tabell": TABELL,
            "dimensioner": {
                "PrelAggr": ["x"] * 200,
                "ContentsCode": ["y"] * 10,
                "Tid": ["z"] * 100,  # 200 * 10 * 100 = 200 000 celler
            },
        },
    )
    with caplog.at_level(logging.WARNING):
        utkast = PxWebAdapter("scb_pxweb").hamta(plan)

    assert utkast == []
    assert "200000 celler" in caplog.text
    assert "avvisat" in caplog.text


def test_pxweb_utan_tabell_ger_tomt():
    assert PxWebAdapter("scb_pxweb").hamta(Fragplan(fraga="", extra={})) == []
