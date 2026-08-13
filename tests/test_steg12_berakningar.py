"""Acceptanstester för Steg 12 — Beräkningsmodul.

Acceptanskriterier från PLAN.md §12:
  1. pytest: procentuell_forandring("F1","F2") ger en Faktapost vars
     lank_manniska pekar på den första ingångens källa och vars harledd_av
     är ("F1","F2").
  2. pytest: beräkning på Faktaposter med olika enheter kastar.
  3. Frontend visar härledda poster med en tydlig markering — INTE testbart
     här. Frontend byggs i steg 14 och finns inte än; se rapporten.
"""

import pytest

from quiet_oppen_data.modeller import Faktaregister
from quiet_oppen_data.motor import berakningar


def _register_med(*poster: dict) -> Faktaregister:
    register = Faktaregister()
    for falt in poster:
        bas = dict(
            kalla_id="scb_pxweb",
            myndighet="SCB",
            licens="CC0",
            lank_manniska="https://www.scb.se/tabell/1",
            lank_maskin="https://api.scb.se/exempel/1",
        )
        bas.update(falt)
        register.registrera(**bas)
    return register


# ---------------------------------------------------------------------------
# Acceptans 1 — procentuell_forandring
# ---------------------------------------------------------------------------

def test_procentuell_forandring_ger_ratt_lank_och_harledning():
    register = _register_med(
        dict(etikett="KPI juni", varde="100", enhet="index",
             lank_manniska="https://www.scb.se/kpi-juni"),
        dict(etikett="KPI juli", varde="102", enhet="index",
             lank_manniska="https://www.scb.se/kpi-juli"),
    )
    f1, f2 = [p.id for p in register.alla()]

    resultat = berakningar.procentuell_forandring(register, f1, f2)

    assert resultat.harledd is True
    assert resultat.harledd_av == (f1, f2)
    # "vars lank_manniska pekar på den första ingångens källa"
    assert resultat.lank_manniska == "https://www.scb.se/kpi-juni"
    assert float(resultat.varde) == pytest.approx(2.0)
    assert resultat.enhet == "procent"


def test_procentuell_forandring_registreras_i_faktaregistret():
    register = _register_med(
        dict(etikett="KPI juni", varde="100", enhet="index"),
        dict(etikett="KPI juli", varde="102", enhet="index"),
    )
    f1, f2 = [p.id for p in register.alla()]

    resultat = berakningar.procentuell_forandring(register, f1, f2)

    # Den härledda posten har fått ett eget F-id och finns i registret,
    # precis som en hämtad Faktapost — Faktaregister är den enda vägen in.
    assert resultat.id not in (f1, f2)
    assert register.hamta(resultat.id) is resultat


def test_procentuell_forandring_med_noll_kastar():
    register = _register_med(
        dict(etikett="Bas", varde="0", enhet="index"),
        dict(etikett="Ny", varde="5", enhet="index"),
    )
    f1, f2 = [p.id for p in register.alla()]

    with pytest.raises(ZeroDivisionError):
        berakningar.procentuell_forandring(register, f1, f2)


# ---------------------------------------------------------------------------
# Acceptans 2 — olika enheter kastar
# ---------------------------------------------------------------------------

def test_differens_med_olika_enheter_kastar():
    register = _register_med(
        dict(etikett="Ränta", varde="2", enhet="procent"),
        dict(etikett="Kurs", varde="10.99", enhet="SEK per EUR"),
    )
    f1, f2 = [p.id for p in register.alla()]

    with pytest.raises(ValueError):
        berakningar.differens(register, f1, f2)


def test_procentuell_forandring_med_olika_enheter_kastar():
    register = _register_med(
        dict(etikett="Ränta", varde="2", enhet="procent"),
        dict(etikett="Kurs", varde="10.99", enhet="SEK per EUR"),
    )
    f1, f2 = [p.id for p in register.alla()]

    with pytest.raises(ValueError):
        berakningar.procentuell_forandring(register, f1, f2)


def test_indexupprakning_med_olika_indexenheter_kastar():
    register = _register_med(
        dict(etikett="Hyra 2020", varde="10000", enhet="SEK"),
        dict(etikett="KPI 2020", varde="100", enhet="index"),
        dict(etikett="Löneindex 2024", varde="115", enhet="löneindex"),
    )
    belopp, ibas, iny = [p.id for p in register.alla()]

    with pytest.raises(ValueError):
        berakningar.indexupprakning(register, belopp, ibas, iny)


# ---------------------------------------------------------------------------
# differens, kvot, indexupprakning — grundfunktion
# ---------------------------------------------------------------------------

def test_differens_raknar_ratt_och_behaller_enhet():
    register = _register_med(
        dict(etikett="Export", varde="120", enhet="mdr SEK"),
        dict(etikett="Import", varde="95", enhet="mdr SEK"),
    )
    export, imp = [p.id for p in register.alla()]

    resultat = berakningar.differens(register, export, imp)

    assert float(resultat.varde) == pytest.approx(25.0)
    assert resultat.enhet == "mdr SEK"
    assert resultat.harledd_av == (export, imp)


def test_kvot_tillater_olika_enheter_och_kombinerar_dem():
    register = _register_med(
        dict(etikett="Skatteintäkter", varde="1000000", enhet="SEK"),
        dict(etikett="Invånare", varde="10000", enhet="invånare"),
    )
    skatt, inv = [p.id for p in register.alla()]

    resultat = berakningar.kvot(register, skatt, inv)

    assert float(resultat.varde) == pytest.approx(100.0)
    assert resultat.enhet == "SEK per invånare"
    assert resultat.harledd_av == (skatt, inv)


def test_kvot_med_noll_namnare_kastar():
    register = _register_med(
        dict(etikett="A", varde="10", enhet="SEK"),
        dict(etikett="B", varde="0", enhet="st"),
    )
    a, b = [p.id for p in register.alla()]

    with pytest.raises(ZeroDivisionError):
        berakningar.kvot(register, a, b)


def test_indexupprakning_raknar_ratt_och_behaller_beloppets_enhet():
    register = _register_med(
        dict(etikett="Hyra 2020", varde="10000", enhet="SEK"),
        dict(etikett="KPI 2020", varde="100", enhet="index"),
        dict(etikett="KPI 2024", varde="110", enhet="index"),
    )
    belopp, ibas, iny = [p.id for p in register.alla()]

    resultat = berakningar.indexupprakning(register, belopp, ibas, iny)

    assert float(resultat.varde) == pytest.approx(11000.0)
    assert resultat.enhet == "SEK"
    assert resultat.harledd_av == (belopp, ibas, iny)


def test_okant_f_id_kastar_value_error():
    register = _register_med(dict(etikett="A", varde="10", enhet="SEK"))
    (f1,) = [p.id for p in register.alla()]

    with pytest.raises(ValueError):
        berakningar.differens(register, f1, "F99")


def test_icke_numeriskt_varde_kastar():
    register = _register_med(
        dict(etikett="Text", varde="ej tillgängligt", enhet="SEK"),
        dict(etikett="Tal", varde="10", enhet="SEK"),
    )
    text, tal = [p.id for p in register.alla()]

    with pytest.raises(ValueError):
        berakningar.differens(register, text, tal)


# ---------------------------------------------------------------------------
# Verktygsexponering i fas A
# ---------------------------------------------------------------------------

def test_verktygsspecar_har_alla_fyra_funktionerna():
    namn = {spec["name"] for spec in berakningar.VERKTYGSSPECAR}
    assert namn == {
        "berakna_differens",
        "berakna_procentuell_forandring",
        "berakna_kvot",
        "berakna_indexupprakning",
    }
    assert namn == berakningar.VERKTYGSNAMN


def test_kor_verktyg_dispatchar_till_ratt_funktion():
    register = _register_med(
        dict(etikett="KPI juni", varde="100", enhet="index"),
        dict(etikett="KPI juli", varde="102", enhet="index"),
    )
    f1, f2 = [p.id for p in register.alla()]

    post = berakningar.kor_verktyg(
        "berakna_procentuell_forandring", register, {"id_forsta": f1, "id_andra": f2}
    )

    assert post.harledd is True
    assert float(post.varde) == pytest.approx(2.0)


def test_kor_verktyg_okant_namn_kastar_key_error():
    register = Faktaregister()
    with pytest.raises(KeyError):
        berakningar.kor_verktyg("berakna_nagot_okant", register, {})


def test_hamtningsloopen_exponerar_berakningsverktygen():
    """Beräkningsverktygen ska synas i fas A:s verktygslista (utan nätverk)."""
    from quiet_oppen_data.motor.hamtning import _bygg_verktygsspecar

    specs = _bygg_verktygsspecar({})
    namn = {s["name"] for s in specs}

    assert berakningar.VERKTYGSNAMN <= namn
