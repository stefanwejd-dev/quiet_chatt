"""Acceptanstester för Steg 0 — register.las()."""

import pytest
from quiet_oppen_data import register
from quiet_oppen_data.register import Kalla, Sparrad


# ---------------------------------------------------------------------------
# Rätt antal och typer
# ---------------------------------------------------------------------------

def test_las_returnerar_lista():
    poster = register.las()
    assert isinstance(poster, list)
    assert len(poster) > 0


def test_blockerade_ar_sparrad():
    """Blockerade källor (blockerad: true) ska vara Sparrad-objekt."""
    poster = register.las()
    sparrade_ids = {p.id for p in poster if isinstance(p, Sparrad)}
    assert "polisen_efterlysta" in sparrade_ids
    assert "bolagsverket_verkliga_huvudman" in sparrade_ids


def test_blockerade_ar_inte_kalla():
    """En blockerad källa får aldrig returneras som Kalla."""
    poster = register.las()
    kalla_ids = {p.id for p in poster if isinstance(p, Kalla)}
    assert "polisen_efterlysta" not in kalla_ids
    assert "bolagsverket_verkliga_huvudman" not in kalla_ids


def test_verifierade_kallas_ar_kalla():
    """Verifierade, icke-blockerade källor ska vara Kalla-objekt."""
    poster = register.las()
    kalla_map = {p.id: p for p in poster if isinstance(p, Kalla)}
    assert "riksbanken" in kalla_map
    assert "scb_pxweb" in kalla_map
    assert "ted" in kalla_map
    assert kalla_map["riksbanken"].verifierad is True
    assert kalla_map["scb_pxweb"].verifierad is True


def test_ej_verifierade_ar_inte_aktiverade():
    """INVARIANT: ingen källa får vara aktiverad utan att vara verifierad.

    Testet räknar medvetet INTE upp enskilda käll-id:n. Registret ändras varje
    gång en källa verifieras (ARKITEKTUR.md §0), och en test som namnger källor
    skulle fallera vid varje sådan verifiering — vilket gör den till brus i
    stället för ett skydd. Det som ska hålla över tid är regeln, inte listan.

    Generiska protokolladaptrar (generisk: true) är undantagna. De har ingen
    bas_url och därmed ingen endpoint att verifiera — verifieringen sker per
    instansierad värd vid körning, och deras säkerhetskontroll är att de bara
    får anropa värdnamn som finns i katalogindexet (ARKITEKTUR.md §3.3, testas
    i test_steg5_transport.py). Att kräva verifierad: ja av dem vore att mäta
    fel sak.
    """
    poster = register.las()
    kallor = [p for p in poster if isinstance(p, Kalla) and not p.generisk]
    assert kallor, "registret innehöll inga konkreta Kalla-objekt — läsningen är trasig"

    overtradelser = [
        k.id for k in kallor if not k.verifierad and k.aktiverad
    ]
    assert not overtradelser, (
        "Källor är aktiverade utan att vara verifierade: "
        f"{', '.join(sorted(overtradelser))}. "
        "En overifierad källa har inte bekräftad sökväg eller svarsformat och "
        "får inte anropas. Verifiera den först, eller sätt aktiverad: false."
    )


# ---------------------------------------------------------------------------
# Invarianter
# ---------------------------------------------------------------------------

def test_kastar_vid_saknat_id(tmp_path):
    """register.las() ska kasta ValueError om en post saknar 'id'."""
    yaml_utan_id = tmp_path / "register.yaml"
    yaml_utan_id.write_text(
        "- myndighet: Teststyrelsen\n  adapter: test\n  takt: {}\n  cache_ttl: 60\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="saknar obligatoriskt fält 'id'"):
        register.las(yaml_utan_id)


def test_alla_poster_har_id():
    """Alla returnerade objekt ska ha ett id-fält som är en sträng."""
    for post in register.las():
        assert isinstance(post.id, str)
        assert len(post.id) > 0


def test_inga_hardkodade_urler():
    """Ingen Kalla får ha bas_url=None OCH verifierad=True (sanity-check)."""
    for post in register.las():
        if isinstance(post, Kalla) and post.verifierad and not post.generisk:
            assert post.bas_url is not None, (
                f"{post.id}: verifierad källa saknar bas_url"
            )


# ---------------------------------------------------------------------------
# hamta()
# ---------------------------------------------------------------------------

def test_hamta_riksbanken():
    riksbanken = register.hamta("riksbanken")
    assert riksbanken is not None
    assert isinstance(riksbanken, Kalla)
    assert riksbanken.bas_url == "https://api.riksbank.se/swea/v1"


def test_hamta_okand_returnerar_none():
    assert register.hamta("finns_inte") is None


# ---------------------------------------------------------------------------
# bara_aktiva()
# ---------------------------------------------------------------------------

def test_bara_aktiva_innehaller_bara_kalla():
    aktiva = register.bara_aktiva()
    for k in aktiva:
        assert isinstance(k, Kalla)
        assert k.aktiverad is True
        assert k.verifierad is True
