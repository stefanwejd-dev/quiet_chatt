"""Acceptanstester för Steg 20 — Skatteverkets rättsliga regelfiler.

Kravet som gör steget värt något: ett utfall ur regelfilen får aldrig
presenteras utan de lagrum det vilar på, och skillnaden mellan "reglerna
säger nej" och "reglerna räckte inte till" måste synas i svaret.

Nätverksberoende tester tar isolerad_cache-fixturen (se conftest.py).
VCR-kassetter spelas in i tests/kassetter/ vid första körning.
"""

import pytest
import vcr

from quiet_oppen_data.adaptrar.skatteverket_rattsligaregler import (
    SkatteverketRattsligaReglerAdapter,
)
from quiet_oppen_data.modeller import Fragplan
from quiet_oppen_data.register import hamta as hamta_kalla

VCR_CONFIG = {
    "cassette_library_dir": "tests/kassetter",
    "record_mode": "once",
    "match_on": ["method", "scheme", "host", "port", "path", "query"],
}

_ID = "skatteverket_rattsligaregler"


def _kor(verktyg: str | None = None, **extra):
    plan = Fragplan(fraga="", extra={"verktyg": verktyg or _ID, **extra})
    return SkatteverketRattsligaReglerAdapter().hamta(plan)


# ---------------------------------------------------------------------------
# Registret
# ---------------------------------------------------------------------------

def test_kallan_finns_och_ar_aktiv():
    k = hamta_kalla(_ID)
    assert k is not None, "källan saknas i kallregister.yaml"
    assert k.aktiverad and k.verifierad


def test_licensen_ar_okand_inte_gissad():
    """Datasetet bär accessRights=PUBLIC men ingen dcterms:license.

    Åtkomst är belagd, användningsvillkor inte. Att skriva CC0 vore att smuggla
    in en gissning i ett fält som resten av bygget förutsätter är sant.
    """
    assert hamta_kalla(_ID).licens == "okänd"


def test_alla_regelfiler_har_resurs_och_version():
    for post in hamta_kalla(_ID).dataset:
        assert post.get("resurs"), f"{post.get('id')} saknar resurs-id"
        assert post.get("regelversion"), f"{post.get('id')} saknar regelversion"
        assert post.get("schema") in ("nytt", "gammalt"), (
            f"{post.get('id')} har oavläst schema — de två formaten skiljer sig "
            "i var källhänvisningarna sitter"
        )


def test_versionsvalsfiler_exponeras_inte_som_regelomraden():
    """Versionsvalsfilerna avgör vilken version som gäller ett visst år.

    De besvarar inga sakfrågor och ska inte kunna väljas som regelområde —
    annars får modellen tillbaka ett filnamn där den väntar sig ett skattesvar.
    """
    adapter = SkatteverketRattsligaReglerAdapter()
    exponerade = {d["id"] for d in adapter._omraden}
    assert not any(i.startswith("versionsval") for i in exponerade)
    # …men de ska finnas kvar i registret, annars tappas versionsinformationen.
    i_registret = {d["id"] for d in hamta_kalla(_ID).dataset}
    assert any(i.startswith("versionsval") for i in i_registret)


# ---------------------------------------------------------------------------
# Verktygsdefinitioner
# ---------------------------------------------------------------------------

def test_tre_verktyg_exponeras():
    namn = [s["name"] for s in SkatteverketRattsligaReglerAdapter().beskriv()]
    assert namn == [f"{_ID}_lista_omraden", f"{_ID}_fragor", _ID]


def test_omrades_id_ar_enum_inte_fritext():
    """Modellen ska inte kunna hitta på ett områdes-id (ARKITEKTUR.md §5 regel 7)."""
    for spec in SkatteverketRattsligaReglerAdapter().beskriv():
        if spec["name"] == _ID:
            enum = spec["input_schema"]["properties"]["omrade"].get("enum")
            assert enum and "gavor" in enum


# ---------------------------------------------------------------------------
# Exekvering — nya schemat (gåvor)
# ---------------------------------------------------------------------------

@vcr.use_cassette(**VCR_CONFIG)
def test_kontant_gava_ar_skattepliktig_med_lagrum(isolerad_cache):
    """Ett enda villkor avgör saken — och lagrummen måste följa med."""
    utkast = _kor(omrade="gavor", svar={"Ges gåvan i form av pengar?": "Ja"})
    assert len(utkast) == 1
    post = utkast[0]
    assert "skattepliktig" in post.varde.lower()
    assert "11 kap. 14 § IL" in post.dimensioner["lagrum"]
    assert post.lank_manniska and post.lank_maskin


@vcr.use_cassette(**VCR_CONFIG)
def test_julgava_under_gransen_ar_skattefri(isolerad_cache):
    utkast = _kor(omrade="gavor", svar={
        "Ges gåvan i form av pengar?": "Nej",
        "Vad är det för typ av gåva?": "Julgåva",
        "Är gåvans marknadsvärde högre än 600 kr inklusive mervärdesskatt?": "Nej",
        "Ges gåvan till alla anställda alternativt en större grupp av anställda?": "Ja",
    })
    assert len(utkast) == 1
    assert utkast[0].varde == "Gåvan är skattefri"


@vcr.use_cassette(**VCR_CONFIG)
def test_forutsattningarna_bars_med_utfallet(isolerad_cache):
    """"Gåvan är skattefri" utan förutsättningar är inte kontrollerbart."""
    utkast = _kor(omrade="gavor", svar={"Ges gåvan i form av pengar?": "Ja"})
    assert utkast[0].dimensioner["Ges gåvan i form av pengar?"] == "Ja"


@vcr.use_cassette(**VCR_CONFIG)
def test_otillrackliga_svar_ger_inte_ett_utfall(isolerad_cache):
    """Skillnaden mot "reglerna säger nej" måste synas — annars blir ett
    ofullständigt underlag till ett skattebesked."""
    utkast = _kor(omrade="gavor", svar={"Ges gåvan i form av pengar?": "Nej"})
    assert len(utkast) == 1
    assert "kan inte avgöra" in utkast[0].varde
    assert "Vad är det för typ av gåva?" in utkast[0].varde
    assert "lagrum" not in utkast[0].dimensioner


# ---------------------------------------------------------------------------
# Exekvering — gamla schemat (representation, utlägg)
# ---------------------------------------------------------------------------

@vcr.use_cassette(**VCR_CONFIG)
def test_gamla_schemat_ger_lagrum_ur_kommastrang(isolerad_cache):
    """Gamla filer bär källorna som en kommaseparerad sträng under "Källor",
    nya som en lista under results[].sources. Båda ska bli samma fält."""
    utkast = _kor(omrade="utlagg", svar={"Avser ersättningen bara utlägg?": "Ja"})
    assert len(utkast) == 1
    lagrum = utkast[0].dimensioner["lagrum"]
    assert "10 kap. 1 § IL" in lagrum and "2 kap. 1 § SAL" in lagrum


@vcr.use_cassette(**VCR_CONFIG)
def test_representation_bar_avdragstak_i_kronor(isolerad_cache):
    """Takbeloppen är fakta ur regelfilen och får inte tappas bort."""
    utkast = _kor(omrade="representation", svar={
        "Vid vilket av följande tillfällen utövades representationen?":
            "Affärsförhandling / Personalfest",
        "Är det ett tillfälle som skatterättsligt brukar betecknas som lyx?": "Nej",
        "Vilken typ av inköp avser representationsutgiften?": "Förtäring",
        "Avser inköpet förtäring?": "Ja",
        "Vilken typ av förtäring handlar det om, enklare förtäring eller måltid?":
            "Enklare förtäring",
        "Är det en kostnad för en personalfest?": "Nej",
        "Är det en kostnad för en anställds resa och/eller boende?": "Nej",
    })
    assert len(utkast) == 1
    tak = utkast[0].dimensioner["Avdragsgill kostnad för enklare förtäring, tak"]
    assert "60 kr" in tak


# ---------------------------------------------------------------------------
# Frågeverktyget
# ---------------------------------------------------------------------------

@vcr.use_cassette(**VCR_CONFIG)
def test_svarsalternativ_citeras_ett_och_ett(isolerad_cache):
    """"Affärsförhandling / Personalfest" är ETT alternativ i
    representationsfilen. Utan citattecken går gränsen inte att se, och ett
    svar som modellen delar på egen hand matchar inget villkor."""
    utkast = _kor(f"{_ID}_fragor", omrade="representation")
    assert '"Affärsförhandling / Personalfest"' in utkast[0].varde


@vcr.use_cassette(**VCR_CONFIG)
def test_svar_med_radbrytning_matchar_sitt_villkor(isolerad_cache):
    """Några alternativ bär radbrytning mitt i sig. _lista_fragor visar dem på
    en rad, så jämförelsen måste normalisera inre blanktecken — annars matchar
    det svar modellen fått tillbaka aldrig sitt eget villkor."""
    utkast = _kor(omrade="gavor", svar={
        "Ges gåvan i form av pengar?": "Nej",
        "Vad är det för typ av gåva?": "Minnesgåva",
        "Lämnas gåvan till en varaktigt anställd?": "Ja",
        "Vid vilket tillfället ges gåvan?":
            "Efter en längre tids anställning (minst 20 år)",
        "Är gåvans marknadsvärde högre än 15 000 kr inklusive mervärdesskatt?": "Nej",
        "Har minnesgåva redan lämnats vid ett tidigare tillfälle?": "Nej",
    })
    assert utkast
    assert "kan inte avgöra" not in utkast[0].varde, (
        "villkoret bär radbrytning — svaret ska ändå matcha"
    )


# ---------------------------------------------------------------------------
# Vägran framför gissning
# ---------------------------------------------------------------------------

def test_okand_operator_avvisas():
    """Bara "equal" förekommer i dagens filer. Skulle Skatteverket införa en
    jämförelseoperator ska adaptern vägra — ett feltolkat villkor ger ett tyst
    felaktigt skattebesked."""
    adapter = SkatteverketRattsligaReglerAdapter()
    with pytest.raises(ValueError):
        adapter._matchar(
            {"all": [{"fact": "Belopp?", "operator": "greaterThan", "value": "600"}]},
            {"Belopp?": "700"},
        )


def test_okand_villkorstyp_avvisas():
    adapter = SkatteverketRattsligaReglerAdapter()
    with pytest.raises(ValueError):
        adapter._matchar(
            {"any": [{"fact": "X?", "operator": "equal", "value": "Ja"}]},
            {"X?": "Ja"},
        )


@vcr.use_cassette(**VCR_CONFIG)
def test_okant_omrade_ger_tomt_inte_undantag(isolerad_cache):
    """Adapterkontraktet: fel loggas och ger tom lista, aldrig ett påhittat
    utkast (adaptrar/bas.py)."""
    assert _kor(omrade="finns_inte", svar={"X?": "Ja"}) == []
    assert _kor(f"{_ID}_fragor", omrade="finns_inte") == []
