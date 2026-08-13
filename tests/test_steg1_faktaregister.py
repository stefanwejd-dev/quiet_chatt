"""Acceptanstester för Steg 1 — Faktapost och Faktaregister."""

from datetime import datetime, timezone

import pytest

from quiet_oppen_data.modeller import Faktapost, Faktaregister


# ---------------------------------------------------------------------------
# Hjälpfunktion
# ---------------------------------------------------------------------------

def _minipost(reg: Faktaregister, **extra) -> Faktapost:
    """Skapar en minimal giltig Faktapost via registret."""
    bas = dict(
        etikett="Referensränta",
        varde="3.5",
        enhet="%",
        period="2026-08-12",
        kalla_id="riksbanken",
        myndighet="Riksbanken",
        licens="okänd",
        lank_manniska="https://www.riksbank.se/sv/statistik/rantor-och-valutakurser/",
        lank_maskin="https://api.riksbank.se/swea/v1/Observations/Latest/SECRINTP",
    )
    bas.update(extra)
    return reg.registrera(**bas)


# ---------------------------------------------------------------------------
# Validering vid registrering
# ---------------------------------------------------------------------------

def test_registrering_utan_lank_manniska_kastar():
    reg = Faktaregister()
    with pytest.raises(ValueError, match="lank_manniska"):
        reg.registrera(
            etikett="Test",
            varde="1.0",
            kalla_id="riksbanken",
            myndighet="Riksbanken",
            licens="okänd",
            lank_maskin="https://api.riksbank.se/test",
            # lank_manniska utelämnad
        )


def test_registrering_med_tom_lank_manniska_kastar():
    reg = Faktaregister()
    with pytest.raises(ValueError, match="lank_manniska"):
        reg.registrera(
            etikett="Test",
            varde="1.0",
            kalla_id="riksbanken",
            myndighet="Riksbanken",
            licens="okänd",
            lank_manniska="",           # tom sträng
            lank_maskin="https://api.riksbank.se/test",
        )


def test_registrering_utan_lank_maskin_kastar():
    reg = Faktaregister()
    with pytest.raises(ValueError, match="lank_maskin"):
        reg.registrera(
            etikett="Test",
            varde="1.0",
            kalla_id="riksbanken",
            myndighet="Riksbanken",
            licens="okänd",
            lank_manniska="https://www.riksbank.se/",
            # lank_maskin utelämnad
        )


def test_registrering_med_tom_lank_maskin_kastar():
    reg = Faktaregister()
    with pytest.raises(ValueError, match="lank_maskin"):
        reg.registrera(
            etikett="Test",
            varde="1.0",
            kalla_id="riksbanken",
            myndighet="Riksbanken",
            licens="okänd",
            lank_manniska="https://www.riksbank.se/",
            lank_maskin="",             # tom sträng
        )


# ---------------------------------------------------------------------------
# ID-tilldelning — stabila och stigande
# ---------------------------------------------------------------------------

def test_id_ar_f1_f2_f3():
    reg = Faktaregister()
    p1 = _minipost(reg, etikett="Post ett")
    p2 = _minipost(reg, etikett="Post två")
    p3 = _minipost(reg, etikett="Post tre")
    assert p1.id == "F1"
    assert p2.id == "F2"
    assert p3.id == "F3"


def test_id_ar_stabila_efter_registrering():
    """Ett F-id ändras inte efter att posten registrerats."""
    reg = Faktaregister()
    p1 = _minipost(reg, etikett="Första")
    _minipost(reg, etikett="Andra")
    _minipost(reg, etikett="Tredje")
    # p1.id ska fortfarande vara F1
    assert reg.hamta("F1") is p1
    assert reg.hamta("F1").id == "F1"  # type: ignore[union-attr]


def test_id_ar_stigande():
    reg = Faktaregister()
    poster = [_minipost(reg, etikett=f"Post {i}") for i in range(5)]
    numrar = [int(p.id[1:]) for p in poster]
    assert numrar == sorted(numrar)
    assert numrar[0] == 1


def test_separata_register_startar_om_fran_f1():
    """Varje Faktaregister-instans har en oberoende räknare."""
    reg1 = Faktaregister()
    reg2 = Faktaregister()
    _minipost(reg1)
    _minipost(reg1)
    p = _minipost(reg2)
    assert p.id == "F1"


# ---------------------------------------------------------------------------
# hamta() och alla()
# ---------------------------------------------------------------------------

def test_hamta_returnerar_ratt_post():
    reg = Faktaregister()
    p = _minipost(reg, etikett="Rätt post")
    assert reg.hamta("F1") is p
    assert reg.hamta("F1").etikett == "Rätt post"  # type: ignore[union-attr]


def test_hamta_okant_id_returnerar_none():
    reg = Faktaregister()
    assert reg.hamta("F99") is None
    assert reg.hamta("") is None


def test_alla_returnerar_i_registreringsordning():
    reg = Faktaregister()
    etiketter = ["Ränta", "Inflation", "BNP"]
    for e in etiketter:
        _minipost(reg, etikett=e)
    assert [p.etikett for p in reg.alla()] == etiketter


def test_ar_tom_och_len():
    reg = Faktaregister()
    assert reg.ar_tom()
    assert len(reg) == 0
    _minipost(reg)
    assert not reg.ar_tom()
    assert len(reg) == 1


# ---------------------------------------------------------------------------
# serialisera_for_syntes()
# ---------------------------------------------------------------------------

def test_serialisera_innehaller_fid():
    reg = Faktaregister()
    _minipost(reg)
    text = reg.serialisera_for_syntes()
    assert "F1" in text


def test_serialisera_innehaller_etikett():
    reg = Faktaregister()
    _minipost(reg, etikett="Referensränta")
    text = reg.serialisera_for_syntes()
    assert "Referensränta" in text


def test_serialisera_innehaller_varde():
    reg = Faktaregister()
    _minipost(reg, varde="3.5")
    text = reg.serialisera_for_syntes()
    assert "3.5" in text


def test_serialisera_innehaller_enhet():
    reg = Faktaregister()
    _minipost(reg, enhet="procent")
    text = reg.serialisera_for_syntes()
    assert "procent" in text


def test_serialisera_innehaller_period():
    reg = Faktaregister()
    _minipost(reg, period="2026-08-12")
    text = reg.serialisera_for_syntes()
    assert "2026-08-12" in text


def test_serialisera_innehaller_myndighet():
    reg = Faktaregister()
    _minipost(reg, myndighet="Riksbanken")
    text = reg.serialisera_for_syntes()
    assert "Riksbanken" in text


def test_serialisera_innehaller_dimensioner():
    reg = Faktaregister()
    _minipost(reg, dimensioner={"region": "Malmö", "kön": "totalt"})
    text = reg.serialisera_for_syntes()
    assert "region" in text
    assert "Malmö" in text
    assert "kön" in text


def test_serialisera_innehaller_inte_lank_maskin():
    """Råa maskinlänkar ska INTE nå syntesmodellen."""
    reg = Faktaregister()
    _minipost(
        reg,
        lank_maskin="https://api.riksbank.se/swea/v1/Observations/Latest/SECRINTP",
    )
    text = reg.serialisera_for_syntes()
    assert "api.riksbank.se" not in text


def test_serialisera_innehaller_inte_lank_manniska():
    """Källpanelslänkar hanteras av frontend, inte syntesmodellen."""
    reg = Faktaregister()
    _minipost(
        reg,
        lank_manniska="https://www.riksbank.se/sv/statistik/rantor-och-valutakurser/",
    )
    text = reg.serialisera_for_syntes()
    assert "riksbank.se/sv/statistik" not in text


def test_serialisera_tomt_register_ger_tom_strang():
    reg = Faktaregister()
    assert reg.serialisera_for_syntes() == ""


def test_serialisera_flera_poster_ger_flera_rader():
    reg = Faktaregister()
    _minipost(reg, etikett="Post ett")
    _minipost(reg, etikett="Post två")
    _minipost(reg, etikett="Post tre")
    rader = reg.serialisera_for_syntes().splitlines()
    assert len(rader) == 3
    assert "F1" in rader[0]
    assert "F2" in rader[1]
    assert "F3" in rader[2]


def test_serialisera_harledd_post():
    reg = Faktaregister()
    _minipost(reg, etikett="Ingång 1", varde="100")
    _minipost(reg, etikett="Ingång 2", varde="110")
    reg.registrera(
        etikett="Förändring",
        varde="10",
        enhet="%",
        kalla_id="riksbanken",
        myndighet="Riksbanken",
        licens="okänd",
        lank_manniska="https://www.riksbank.se/",
        lank_maskin="beraknat://F1+F2",
        harledd=True,
        harledd_av=("F1", "F2"),
    )
    text = reg.serialisera_for_syntes()
    assert "F1" in text
    assert "F2" in text
    assert "härlett" in text


# ---------------------------------------------------------------------------
# Faktapost är fryst
# ---------------------------------------------------------------------------

def test_faktapost_ar_fryst():
    reg = Faktaregister()
    p = _minipost(reg)
    with pytest.raises((AttributeError, TypeError)):
        p.varde = "99"  # type: ignore[misc]


def test_hamtad_sätts_automatiskt_om_den_utelamnas():
    reg = Faktaregister()
    p = _minipost(reg)
    assert isinstance(p.hamtad, datetime)
