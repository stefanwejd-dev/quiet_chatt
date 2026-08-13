"""Acceptanstester för Steg 11 — Fas C: validator (fail-closed).

Acceptanskriterier från PLAN.md §11:
  1. pytest: ett svar som citerar F99 (finns inte) avvisas.
  2. pytest: ett svar där en CC-BY-källa citeras utan attribution avvisas.
  3. pytest: efter två misslyckade försök returneras fail-closed-svaret,
     inte ett obelagt.

Inga livetester behövs — validatorns fyra kontroller är rena funktioner mot
Faktaregister och SyntesSvar, och omförsöksflödet testas med en attrapp-
syntetiserare (inget API-anrop).
"""

from quiet_oppen_data.modeller import Faktaregister
from quiet_oppen_data.motor.syntes import INGET_HITTAT, Stycke, SyntesSvar
from quiet_oppen_data.motor.validator import (
    FasCValidator,
    Valideringsfel,
    validera,
)


def _register_med_post(**övrigt) -> tuple[Faktaregister, str]:
    register = Faktaregister()
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
    post = register.registrera(**bas)
    return register, post.id


# ---------------------------------------------------------------------------
# Acceptans 1 — okänt F-id avvisas
# ---------------------------------------------------------------------------

def test_okant_f_id_avvisas():
    register, f1 = _register_med_post()
    svar = SyntesSvar(
        kan_besvaras=True,
        stycken=(Stycke(text="Referensräntan är 2 procent.", kallor=("F99",)),),
    )

    fel = validera(svar, register)

    assert any(f.kontroll == "okänd_kalla" for f in fel)
    assert any("F99" in f.meddelande for f in fel)


def test_giltigt_f_id_ger_inget_fel_for_den_kontrollen():
    register, f1 = _register_med_post()
    svar = SyntesSvar(
        kan_besvaras=True,
        stycken=(Stycke(text="Referensräntan är 2 procent.", kallor=(f1,)),),
    )

    fel = validera(svar, register)

    assert not any(f.kontroll == "okänd_kalla" for f in fel)


# ---------------------------------------------------------------------------
# Acceptans 2 — CC-BY utan attribution avvisas
# ---------------------------------------------------------------------------

def test_cc_by_kalla_utan_attribution_avvisas():
    register, f1 = _register_med_post(licens="CC-BY", attribution=None)
    svar = SyntesSvar(
        kan_besvaras=True,
        stycken=(Stycke(text="Referensräntan är 2 procent.", kallor=(f1,)),),
    )

    fel = validera(svar, register)

    assert any(f.kontroll == "saknar_attribution" for f in fel)


def test_cc_by_kalla_med_attribution_ger_inget_fel():
    register, f1 = _register_med_post(licens="CC-BY", attribution="Källa: Riksbanken (CC-BY)")
    svar = SyntesSvar(
        kan_besvaras=True,
        stycken=(Stycke(text="Referensräntan är 2 procent.", kallor=(f1,)),),
    )

    fel = validera(svar, register)

    assert not any(f.kontroll == "saknar_attribution" for f in fel)


def test_cc0_kalla_utan_attribution_ger_inget_fel():
    """CC0 kräver ingen attribution — bara CC-BY-kontrollen ska slå till."""
    register, f1 = _register_med_post(licens="CC0", attribution=None)
    svar = SyntesSvar(
        kan_besvaras=True,
        stycken=(Stycke(text="Referensräntan är 2 procent.", kallor=(f1,)),),
    )

    fel = validera(svar, register)

    assert not any(f.kontroll == "saknar_attribution" for f in fel)


# ---------------------------------------------------------------------------
# Kontroll 3 — lank_manniska (defensiv; Faktaregister.registrera hindrar
# normalt att en sådan post någonsin uppstår)
# ---------------------------------------------------------------------------

class _TrasigtRegister:
    """Attrapp som kringgår Faktaregistrets invariant för att testa kontroll 3
    isolerat — en riktig Faktaregister kan inte producera en post utan
    lank_manniska, se modeller.py."""

    def __init__(self, post):
        self._post = post

    def hamta(self, fid):
        return self._post if fid == self._post.id else None


def test_post_utan_lank_manniska_avvisas():
    from dataclasses import replace as dc_replace

    register, f1 = _register_med_post()
    äkta_post = register.hamta(f1)
    trasig_post = dc_replace(äkta_post, lank_manniska="")
    trasigt_register = _TrasigtRegister(trasig_post)

    svar = SyntesSvar(
        kan_besvaras=True,
        stycken=(Stycke(text="Referensräntan är 2 procent.", kallor=(f1,)),),
    )

    fel = validera(svar, trasigt_register)

    assert any(f.kontroll == "saknar_lank_manniska" for f in fel)


# ---------------------------------------------------------------------------
# Kontroll 2 — ociterat stycke med sakuppgift (schema-drift-backstop)
# ---------------------------------------------------------------------------

def test_ociterat_stycke_med_siffra_flaggas():
    register = Faktaregister()
    svar = SyntesSvar(
        kan_besvaras=True,
        stycken=(Stycke(text="Referensräntan är 2 procent.", kallor=()),),
    )

    fel = validera(svar, register)

    assert any(f.kontroll == "ociterat_stycke" for f in fel)


def test_ociterat_stycke_utan_sakuppgift_ger_inget_fel():
    register = Faktaregister()
    svar = SyntesSvar(
        kan_besvaras=False,
        stycken=(),
        forbehall=INGET_HITTAT,
    )

    fel = validera(svar, register)

    assert fel == []


# ---------------------------------------------------------------------------
# FasCValidator — omförsök och fail-closed
# ---------------------------------------------------------------------------

class _Attrapp:
    """Falsk syntetiserare — spelar in anrop, returnerar förprogrammerade svar."""

    def __init__(self, svar_sekvens):
        self._svar_sekvens = list(svar_sekvens)
        self.anrop: list[dict] = []

    def syntetisera(self, fraga, register, felmeddelande=None):
        self.anrop.append({"fraga": fraga, "felmeddelande": felmeddelande})
        return self._svar_sekvens[len(self.anrop) - 1]


def test_giltigt_forsta_svar_kraver_inget_omforsok():
    register, f1 = _register_med_post()
    giltigt = SyntesSvar(
        kan_besvaras=True,
        stycken=(Stycke(text="Referensräntan är 2 procent.", kallor=(f1,)),),
    )
    attrapp = _Attrapp([giltigt])
    validator = FasCValidator(syntes=attrapp)

    resultat = validator.kor("Vad är referensräntan?", register)

    assert len(attrapp.anrop) == 1
    assert attrapp.anrop[0]["felmeddelande"] is None
    assert resultat.kan_besvaras is True
    assert resultat.stycken == giltigt.stycken


def test_forsta_felet_ger_omforsok_med_felmeddelande():
    register, f1 = _register_med_post()
    ogiltigt = SyntesSvar(
        kan_besvaras=True,
        stycken=(Stycke(text="Referensräntan är 2 procent.", kallor=("F99",)),),
    )
    giltigt = SyntesSvar(
        kan_besvaras=True,
        stycken=(Stycke(text="Referensräntan är 2 procent.", kallor=(f1,)),),
    )
    attrapp = _Attrapp([ogiltigt, giltigt])
    validator = FasCValidator(syntes=attrapp)

    resultat = validator.kor("Vad är referensräntan?", register)

    assert len(attrapp.anrop) == 2
    assert attrapp.anrop[0]["felmeddelande"] is None
    assert attrapp.anrop[1]["felmeddelande"] is not None
    assert "F99" in attrapp.anrop[1]["felmeddelande"]
    assert resultat.kan_besvaras is True
    assert resultat.stycken == giltigt.stycken


def test_tva_misslyckade_forsok_ger_fail_closed():
    """Acceptans 3: efter två misslyckade försök returneras fail-closed,
    inte ett obelagt svar."""
    register, f1 = _register_med_post()
    alltid_ogiltigt = SyntesSvar(
        kan_besvaras=True,
        stycken=(Stycke(text="Referensräntan är 2 procent.", kallor=("F99",)),),
    )
    attrapp = _Attrapp([alltid_ogiltigt, alltid_ogiltigt])
    validator = FasCValidator(syntes=attrapp)

    resultat = validator.kor("Vad är referensräntan?", register)

    assert len(attrapp.anrop) == 2
    assert resultat.kan_besvaras is False
    assert resultat.stycken == ()
    assert resultat.forbehall == INGET_HITTAT


def test_attribution_fylls_i_deterministiskt_vid_giltigt_svar():
    register, f1 = _register_med_post(licens="CC-BY", attribution="Källa: Riksbanken (CC-BY)")
    giltigt = SyntesSvar(
        kan_besvaras=True,
        stycken=(Stycke(text="Referensräntan är 2 procent.", kallor=(f1,)),),
    )
    attrapp = _Attrapp([giltigt])
    validator = FasCValidator(syntes=attrapp)

    resultat = validator.kor("Vad är referensräntan?", register)

    assert resultat.attribution == ("Källa: Riksbanken (CC-BY)",)
