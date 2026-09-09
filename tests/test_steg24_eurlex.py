"""Acceptanstester för Steg 24 — EU-rättsakter ur EUR-Lex.

Tyngdpunkten ligger på lydelsevalet. CELEX-numret i registret pekar på
rättsaktens URSPRUNGLIGA lydelse; den konsoliderade har ett annat nummer med
konsolideringsdatum i. Att indexera ursprungslydelsen som om den vore gällande
vore samma fel som att tillämpa en upphävd paragraf — och svårare att
upptäcka, eftersom källänken skulle vara korrekt.

Framtida konsolideringar förekommer (32013L0034 hade 2026-09-09 en daterad
2027-01-30) och får aldrig väljas.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from quiet_oppen_data import euregister
from quiet_oppen_data.index import eu_ingest, eu_parser
from quiet_oppen_data.index.db import oppna_db
from quiet_oppen_data.index.korpus import KorpusChunk, KorpusDokument, skriv_dokument
from quiet_oppen_data.register import Kalla, hamta


# ---------------------------------------------------------------------------
# Registret och källan
# ---------------------------------------------------------------------------


def test_euregistret_laser_och_harleder_lankar():
    akter = euregister.las()
    assert akter, "registret ska innehålla rättsakter"
    moms = euregister.hamta("Momsdirektivet")
    assert moms is not None
    assert moms.celex == "32006L0112"
    # Länkarna härleds ur CELEX — två sanningar om samma adress är en för många.
    assert moms.lank_maskin.endswith("/32006L0112")
    assert "CELEX:32006L0112" in moms.lank_manniska


def test_luckorna_redovisas_och_hamtas_inte():
    """En redovisad lucka är ett giltigt utfall — en tyst är det inte."""
    luckor = euregister.luckor()
    assert luckor, "fördragen och OECD-materialet ska stå som kända luckor"
    celex_som_hamtas = {a.celex for a in euregister.las()}
    text = " ".join(lucka.identitet for lucka in luckor)
    assert "fördraget" in text.lower() or "FEU" in text
    # Ingen lucka får smyga in bland det som hämtas.
    for lucka in luckor:
        assert lucka.identitet not in celex_som_hamtas
        assert lucka.skal, "en lucka utan skäl är bara en tom rad"


def test_kallan_finns_och_ar_verifierad():
    k = hamta("eurlex")
    assert isinstance(k, Kalla)
    assert k.verifierad and k.aktiverad
    assert k.adapter == "eurlex"
    assert "publications.europa.eu" in (k.bas_url or ""), (
        "eur-lex.europa.eu svarar 202 med tom kropp på maskinella anrop — "
        "resursen på publications.europa.eu är den som fungerar"
    )


# ---------------------------------------------------------------------------
# Lydelsevalet
# ---------------------------------------------------------------------------


def _monkeypatcha_konsolideringar(monkeypatch, datum: list[str]):
    monkeypatch.setattr(eu_ingest, "hitta_konsolideringar", lambda celex: datum)


def test_valjer_senaste_konsolidering_som_tratt_i_kraft(monkeypatch):
    _monkeypatcha_konsolideringar(monkeypatch, ["20130719", "20230105", "20260318"])
    resurs, lydelse, framtida = eu_ingest.valj_lydelse("32013L0034", idag=date(2026, 9, 9))
    assert resurs == "02013L0034-20260318"
    assert lydelse == "2026-03-18"
    assert framtida == []


def test_framtida_konsolidering_valjs_aldrig(monkeypatch):
    """Den viktigaste regeln i steget.

    En regel som ännu inte trätt i kraft får inte tillämpas på dagens fråga.
    Samma sak som `status: framtida` i systerprojektets regelverkskontrakt.
    """
    _monkeypatcha_konsolideringar(monkeypatch, ["20260318", "20270130"])
    resurs, lydelse, framtida = eu_ingest.valj_lydelse("32013L0034", idag=date(2026, 9, 9))
    assert resurs == "02013L0034-20260318", "den framtida lydelsen får inte väljas"
    assert lydelse == "2026-03-18"
    assert framtida == ["20270130"], "men den ska redovisas"


def test_okonsoliderad_rattsakt_far_ursprunglig_lydelse_utskriven(monkeypatch):
    """Tomt fält skulle se ut som en okänd konsolidering. Det gissas aldrig."""
    _monkeypatcha_konsolideringar(monkeypatch, [])
    resurs, lydelse, framtida = eu_ingest.valj_lydelse("32014L0055", idag=date(2026, 9, 9))
    assert resurs == "32014L0055", "utan konsolidering gäller ursprungslydelsen"
    assert lydelse == "ursprunglig lydelse"


def test_enbart_framtida_konsolideringar_ger_ursprungslydelsen(monkeypatch):
    _monkeypatcha_konsolideringar(monkeypatch, ["20270130"])
    resurs, lydelse, framtida = eu_ingest.valj_lydelse("32013L0034", idag=date(2026, 9, 9))
    assert resurs == "32013L0034"
    assert lydelse == "ursprunglig lydelse"
    assert framtida == ["20270130"]


def test_misslyckad_forteckning_hamtar_inte_ursprungslydelsen(monkeypatch):
    """Hellre inget svar än fel lydelse.

    Om konsolideringsförteckningen inte går att läsa vet vi inte om
    ursprungslydelsen är ersatt. Att falla tillbaka på den vore att gissa —
    och gissningen skulle bära en korrekt källänk.
    """
    def sprangs(celex):
        raise RuntimeError("EUR-Lex svarade inte")

    monkeypatch.setattr(eu_ingest, "hitta_konsolideringar", sprangs)
    with pytest.raises(RuntimeError):
        eu_ingest.valj_lydelse("32013L0034", idag=date(2026, 9, 9))


def test_konsoliderad_bas_byter_sektorssiffra():
    assert eu_ingest._konsoliderad_bas("32013L0034") == "02013L0034"
    assert eu_ingest._konsoliderad_bas("32006L0112") == "02006L0112"


# ---------------------------------------------------------------------------
# Parsern
# ---------------------------------------------------------------------------

# Officiella tidningens mall (klass oj-*).
_OJ = """<html><body>
<div id="cpt_3">
  <p class="oj-ti-section-1">KAPITEL 3</p>
  <div class="eli-title" id="cpt_3.tit_1"><p class="oj-ti-section-2">BALANSRÄKNING</p></div>
  <div class="eli-subdivision" id="art_9">
    <p class="oj-ti-art">Artikel 9</p>
    <div class="eli-title" id="art_9.tit_1"><p class="oj-sti-art">Allmänna bestämmelser</p></div>
    <div><p class="oj-normal">1. Uppställningsformen får inte ändras.</p></div>
  </div>
</div></body></html>"""

# Den konsoliderade mallen (klass norm/title-article-norm).
_KONS = """<html><head><title>Konsoliderad TEXT: 32013L0034 — SV — 18.03.2026</title></head><body>
<div id="cpt_3">
  <p class="title-division-1">KAPITEL 3</p>
  <div class="eli-title" id="cpt_3.tit_1"><p class="title-division-2">BALANSRÄKNING</p></div>
  <div class="eli-subdivision" id="art_9">
    <p class="title-article-norm">Artikel 9</p>
    <div class="eli-title" id="art_9.tit_1"><p class="stitle-article-norm">Allmänna bestämmelser</p></div>
    <div class="norm"><span class="no-parag">1.</span><div class="norm inline-element">Uppställningsformen får inte ändras.</div></div>
  </div>
</div></body></html>"""


@pytest.mark.parametrize("markup", [_OJ, _KONS], ids=["officiella-tidningen", "konsoliderad"])
def test_parsern_klarar_bada_mallarna(markup):
    """Klassnamnen skiljer sig mellan mallarna; ELI-id:na gör det inte."""
    p = eu_parser.parsa(markup, "32013L0034")
    artiklar = [c for c in p.chunkar if c.blocktyp == "artikel"]
    assert len(artiklar) == 1
    a = artiklar[0]
    assert a.beteckning == "Artikel 9"
    assert a.avsnitt == "Allmänna bestämmelser"
    assert a.kapitel_nr == "Kapitel 3"
    assert "BALANSRÄKNING" in (a.kapitel_rubrik or "")
    assert "Uppställningsformen får inte ändras" in a.text
    # Rubriken får inte ligga kvar i brödtexten också.
    assert a.text.count("Allmänna bestämmelser") == 0


def test_konsolideringsdatum_lases_ur_dokumentets_eget_huvud():
    assert eu_parser.parsa(_KONS, "32013L0034").konsolideringsdatum == "2026-03-18"
    assert eu_parser.parsa(_OJ, "32013L0034").konsolideringsdatum == ""


def test_titelbehallare_raknas_inte_som_egen_indelning():
    """`cpt_3.tit_1` är kapitlets rubrik, inte ett eget kapitel.

    Behandlades den som en indelning började den EFTER kapitlet, och varje
    artikel fick rubriken men tappade numret.
    """
    p = eu_parser.parsa(_OJ, "32013L0034")
    a = next(c for c in p.chunkar if c.blocktyp == "artikel")
    assert a.kapitel_nr == "Kapitel 3", "numret ska överleva titelbehållaren"


def test_tom_markup_ger_anmarkning_inte_tystnad():
    p = eu_parser.parsa("<html><body><p>Ingenting</p></body></html>", "32013L0034")
    assert p.chunkar == []
    assert "ELI" in p.parsningsanmarkning


def test_skal_skiljs_fran_artiklar():
    markup = """<html><body>
    <div id="rct_12"><p>Av tydlighetsskäl bör bestämmelserna förenklas.</p></div>
    <div class="eli-subdivision" id="art_1"><p>Artikel 1</p><div><p>Detta direktiv gäller.</p></div></div>
    </body></html>"""
    p = eu_parser.parsa(markup, "32013L0034")
    typer = {c.blocktyp for c in p.chunkar}
    assert typer == {"skal", "artikel"}
    skal = next(c for c in p.chunkar if c.blocktyp == "skal")
    assert skal.beteckning == "Skäl 12"


# ---------------------------------------------------------------------------
# Adaptern
# ---------------------------------------------------------------------------


def _eu_traff(blocktyp: str, beteckning: str):
    from quiet_oppen_data.modeller import KorpusSokresultat

    return KorpusSokresultat(
        chunk_id="eu:32006L0112#Artikel168~1", korpus="eu",
        dokument_id="eu:32006L0112", dok_id="32006L0112",
        titel="Rådets direktiv 2006/112/EG om ett gemensamt system för mervärdesskatt",
        kortnamn="Momsdirektivet",
        utgivare="Europeiska unionens publikationsbyrå (EUR-Lex)",
        blocktyp=blocktyp, beteckning=beteckning,
        kapitel_nr="Kapitel 1", kapitel_rubrik="AVDRAG — Avdragsrättens inträde",
        avsnitt=None,
        text="I den mån varorna används för beskattade transaktioner ...",
        sida=None, lydelse="2025-04-14", hamtad=datetime.now(UTC).isoformat(),
        lank_manniska="https://eur-lex.europa.eu/legal-content/SV/TXT/?uri=CELEX:32006L0112",
        lank_maskin="http://publications.europa.eu/resource/celex/02006L0112-20250414",
        licens="© Europeiska unionen", attribution="Källa: EUR-Lex, Europeiska unionen",
        relevans=1.0, full_text="...",
    )


def test_adaptern_pekar_maskinlanken_pa_den_hamtade_lydelsen(monkeypatch):
    """Beviset måste peka på den lydelse som citeras, inte på bas-CELEX."""
    from quiet_oppen_data.adaptrar import eurlex as eurlex_adapter
    from quiet_oppen_data.modeller import Fragplan

    monkeypatch.setattr(eurlex_adapter, "sok_korpus",
                        lambda **kw: [_eu_traff("artikel", "Artikel 168")])

    utkast = eurlex_adapter.EurlexAdapter().hamta(
        Fragplan(fraga="avdragsrätt", extra={"sok": "avdragsrätt"})
    )
    assert utkast
    u = utkast[0]
    assert u.lank_maskin.endswith("02006L0112-20250414"), (
        "maskinlänken ska peka på den konsoliderade lydelse som faktiskt hämtades"
    )
    assert u.period == "2025-04-14"
    assert u.dataset == "32006L0112"
    assert u.dimensioner["blocktyp"] == "artikel"
    assert u.dimensioner["artikel"] == "Artikel 168"
    assert "Momsdirektivet" in u.etikett


def test_adaptern_markerar_skal_som_ej_bindande(monkeypatch):
    from quiet_oppen_data.adaptrar import eurlex as eurlex_adapter
    from quiet_oppen_data.modeller import Fragplan

    monkeypatch.setattr(eurlex_adapter, "sok_korpus",
                        lambda **kw: [_eu_traff("skal", "Skäl 12")])
    utkast = eurlex_adapter.EurlexAdapter().hamta(
        Fragplan(fraga="x", extra={"sok": "x", "blocktyp": "skal"})
    )
    assert "ej bindande" in utkast[0].etikett


# ---------------------------------------------------------------------------
# Indexering
# ---------------------------------------------------------------------------


def test_eu_dokument_skrivs_med_lydelse_och_kalluppgifter(tmp_path):
    conn = oppna_db(tmp_path / "index.sqlite")
    try:
        dok = KorpusDokument(
            korpus="eu", dok_id="32006L0112",
            titel="Momsdirektivet", kortnamn="Momsdirektivet",
            utgivare="Europeiska unionens publikationsbyrå (EUR-Lex)",
            lydelse="2025-04-14", version="b" * 64,
            licens="© Europeiska unionen",
            attribution="Källa: EUR-Lex, Europeiska unionen",
            hamtad=datetime.now(UTC).isoformat(),
            lank_manniska="https://eur-lex.europa.eu/legal-content/SV/TXT/?uri=CELEX:32006L0112",
            lank_maskin="http://publications.europa.eu/resource/celex/02006L0112-20250414",
            chunkar=[KorpusChunk(beteckning="Artikel 168", blocktyp="artikel",
                                 text="Avdragsrätt.", kapitel_nr="Kapitel 1")],
        )
        assert skriv_dokument(conn, dok, generera_vektorer=False) == 1
        rad = conn.execute(
            "SELECT korpus, lydelse, lank_maskin FROM korpus_dokument WHERE id = ?",
            ("eu:32006L0112",),
        ).fetchone()
        assert rad[0] == "eu"
        assert rad[1] == "2025-04-14"
        assert "02006L0112-20250414" in rad[2]
    finally:
        conn.close()


def test_korpusen_haller_isar_varandra(tmp_path):
    """En sökning i BFN får aldrig plocka upp EU-stycken och tvärtom."""
    from quiet_oppen_data.index.korpus import statistik

    conn = oppna_db(tmp_path / "index.sqlite")
    try:
        for korpus, dok_id in (("bfn", "vl12-1-k3"), ("eu", "32006L0112")):
            skriv_dokument(conn, KorpusDokument(
                korpus=korpus, dok_id=dok_id, titel=f"{korpus}-dokument",
                utgivare="x", lank_manniska="https://x", lank_maskin="https://x",
                chunkar=[KorpusChunk(beteckning="1", blocktyp="artikel", text="text")],
            ), generera_vektorer=False)

        s = statistik(db_conn=conn)
        assert s["bfn"]["dokument"] == 1
        assert s["eu"]["dokument"] == 1
        for korpus in ("bfn", "eu"):
            antal = conn.execute(
                "SELECT COUNT(*) FROM korpus_chunk WHERE korpus = ?", (korpus,)
            ).fetchone()[0]
            assert antal == 1
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Den tredje mallen och reservlydelsen (rättat efter första skarpa körningen)
# ---------------------------------------------------------------------------

# De äldre konsolideringarna saknar ELI-avdelningar helt. Strukturen bärs bara
# av styckets klass, och id:na är slumpade UUID:n. Trimmat ur
# 02016L1164-20220101 (ATAD), avläst 2026-09-09.
_PLATT = """<html><body>
<p class="title-division-1" id="id-df870855">KAPITEL II</p>
<p class="title-division-2" id="id-d9ab7e41"><span class="boldface">ÅTGÄRDER MOT SKATTEFLYKT</span></p>
<p class="title-article-norm" id="id-4d0bf5f9">Artikel 4</p>
<p class="stitle-article-norm">Räntebegränsningsregel</p>
<div class="norm"><span class="no-parag">1.</span><div class="norm inline-element">Överstigande lånekostnader ska vara avdragsgilla.</div></div>
<p class="title-article-norm" id="id-aa11">Artikel 5</p>
<p class="stitle-article-norm">Utflyttningsbeskattning</p>
<p class="norm">En skattskyldig ska beskattas vid utflyttning.</p>
</body></html>"""


def test_platt_markup_utan_eli_parsas_via_reservvagen():
    """Fyra av nio rättsakter gav noll artiklar vid första skarpa körningen.

    De äldre konsolideringarna saknar `eli-subdivision` helt. Reservvägen
    läser klasserna i stället — men bara när ELI-ankarna saknas, eftersom
    klassnamn hör till presentationen och är en svagare grund.
    """
    p = eu_parser.parsa(_PLATT, "32016L1164")
    artiklar = [c for c in p.chunkar if c.blocktyp == "artikel"]
    assert [a.beteckning for a in artiklar] == ["Artikel 4", "Artikel 5"]
    assert p.parsningsanmarkning == ""

    a = artiklar[0]
    assert a.avsnitt == "Räntebegränsningsregel"
    assert a.kapitel_nr == "Kapitel II"
    assert a.kapitel_rubrik == "ÅTGÄRDER MOT SKATTEFLYKT"
    assert "Överstigande lånekostnader" in a.text
    # Beteckningen och rubriken får inte ligga kvar i brödtexten.
    assert "Artikel 4" not in a.text
    assert "Räntebegränsningsregel" not in a.text


def test_eli_gar_fore_klassnamn():
    """Reservvägen får aldrig konkurrera med den stabila vägen."""
    blandat = _KONS.replace("</body>", _PLATT.split("<body>")[1].split("</body>")[0] + "</body>")
    p = eu_parser.parsa(blandat, "32013L0034")
    artiklar = [c for c in p.chunkar if c.blocktyp == "artikel"]
    # ELI gav en artikel; reservvägen ska då inte ha körts alls.
    assert [a.beteckning for a in artiklar] == ["Artikel 9"]


def test_listad_men_ohamtbar_konsolidering_faller_tillbaka_med_anmarkning(monkeypatch):
    """Källan kan lista en konsolidering som aldrig publicerats.

    Kontrollerat 2026-09-09: e-fakturadirektivet listar konsolideringen
    20140526, men `02014L0055-20140526` svarar 404. Rättsakten har aldrig
    konsoliderats. Kandidaterna prövas nyast först och ursprungslydelsen sist
    — och att det skedde ska synas, inte tigas ihjäl.
    """
    from quiet_oppen_data.adaptrar import transport

    monkeypatch.setattr(eu_ingest, "hitta_konsolideringar", lambda celex: ["20140526"])

    forsokta: list[str] = []

    def falsk_hamta(kalla_id, method, url, **kwargs):
        forsokta.append(url.rsplit("/", 1)[-1])
        if url.endswith("02014L0055-20140526"):
            raise RuntimeError("404 Not Found")
        return transport.RaSvar(innehall=b"<html/>", huvuden={}, url=url)

    monkeypatch.setattr(eu_ingest.transport, "hamta_ocachat", falsk_hamta)

    resurs, lydelse, framtida, svar, not_ = eu_ingest._hamta_lydelse("32014L0055")
    assert forsokta == ["02014L0055-20140526", "32014L0055"], (
        "konsolideringen ska prövas först, originalet sist"
    )
    assert resurs == "32014L0055"
    assert lydelse == "ursprunglig lydelse"
    assert "kunde inte hämtas" in not_, "reservvägen måste lämna spår"


def test_reservlydelse_anvands_inte_nar_forsta_valet_svarar(monkeypatch):
    from quiet_oppen_data.adaptrar import transport

    monkeypatch.setattr(eu_ingest, "hitta_konsolideringar", lambda celex: ["20260318"])
    monkeypatch.setattr(
        eu_ingest.transport, "hamta_ocachat",
        lambda k, m, url, **kw: transport.RaSvar(innehall=b"<html/>", huvuden={}, url=url),
    )
    resurs, lydelse, framtida, svar, not_ = eu_ingest._hamta_lydelse("32013L0034")
    assert resurs == "02013L0034-20260318"
    assert lydelse == "2026-03-18"
    assert not_ == "", "ingen anmärkning när det första valet fungerade"
