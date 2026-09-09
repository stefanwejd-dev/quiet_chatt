"""Acceptanstester för Steg 23 — BFN-korpuset.

Proven täcker de fyra ställen där steget kan gå sönder tyst:

  1. Registret. `indexera_nivaer` är den enda spärren mot att BFN:s 700+
     remissvar hamnar i indexet och citeras som god redovisningssed. Den
     prövas som en invariant, inte som en avsikt.
  2. Parsern. Ett allmänt råd och BFN:s kommentar till det ska bli SKILDA
     block med olika typ — sudda ut den skillnaden och svaret blir fel med
     rätt källänk.
  3. Uppräkningen. bfn.se lämnar färre poster än X-WP-Total uppger; regeln
     "färre än per_page betyder sista sidan" hade tappat nio tiondelar av
     materialet.
  4. Adaptern. Blocktypen måste följa med hela vägen ut i Faktautkastet.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from quiet_oppen_data import bfnregister
from quiet_oppen_data.index import bfn_parser
from quiet_oppen_data.index.db import oppna_db
from quiet_oppen_data.index.korpus import KorpusChunk, KorpusDokument, skriv_dokument
from quiet_oppen_data.register import Kalla, hamta


# ---------------------------------------------------------------------------
# 1. Registret
# ---------------------------------------------------------------------------


def test_bfnregistret_laser_och_har_fangstnat():
    reg = bfnregister.las()
    assert reg.kategorier, "registret måste ha kategorier"
    assert reg.kategorier[-1].prefix == "", (
        "sista kategorin ska vara fångstnätet (prefix ''), annars kastar "
        "kategorisera_sidvag för okända sökvägar"
    )
    assert reg.nyckeldokument, "nyckeldokumenten är skördens minimikrav"


def test_kategorisering_valjer_forsta_traffen():
    reg = bfnregister.las()
    kat = reg.kategorisera_sidvag("redovisningsregler/vagledningar/k-regelverk")
    assert kat.kategori == "k_regelverk"
    assert kat.niva == "karna"
    # Mindre specifik sökväg ska INTE stjäla träffen från den mer specifika.
    assert reg.kategorisera_sidvag("redovisningsregler/uttalanden").kategori == "uttalande"
    # Okänd sökväg fångas av fångstnätet i stället för att kasta.
    assert reg.kategorisera_sidvag("nagot-nytt-hos-bfn").kategori == "ovrigt"


def test_remissvar_far_inte_indexeras():
    """Den viktigaste invarianten i steget.

    BFN publicerar remisser och remissvar — vad andra TYCKER om ett
    regelförslag. Hamnar de i indexet blir de citerade som om de vore
    gällande rätt, med en korrekt källänk till bfn.se, vilket gör felet
    svårare att upptäcka och inte lättare.
    """
    reg = bfnregister.las()
    remiss = reg.kategorisera_sidvag("skrivelser/remissvar")
    assert remiss.niva == "bakgrund"
    assert not reg.far_indexeras(remiss.niva), (
        "remissvar ligger på nivå bakgrund och får inte indexeras"
    )
    assert not reg.far_indexeras("bakgrund")
    assert not reg.far_indexeras("admin")
    assert reg.far_indexeras("karna")
    assert reg.far_indexeras("stod")


def test_kallan_finns_och_ar_verifierad():
    k = hamta("bokforingsnamnden")
    assert isinstance(k, Kalla)
    assert k.verifierad and k.aktiverad
    assert k.adapter == "bfn"
    # BFN:s PSI-villkor kräver att datum anges. Attributionen måste därför ha
    # en plats för det.
    assert "{datum}" in (k.attribution or "")
    # Takten är sänkt därför att bfn.se svarar 429. En höjning ska kräva ett
    # medvetet beslut, inte gå igenom obemärkt.
    assert k.takt.get("per_sekunder", 0) >= 2, "bfn.se ger 429 vid snabbare takt"


# ---------------------------------------------------------------------------
# 2. Parsern
# ---------------------------------------------------------------------------

# Trimmat ur K3-vägledningens mall (BFNAR 2012:1), som den ser ut efter
# textextraktion: blockmarkörerna står ensamma på rad.
_K3_SIDOR = [
    "Vägledning\nK3 Årsredovisning och koncernredovisning\nUppdaterad 2025-12-15\nBFNAR 2012:1",
    (
        "Kapitel 12 – Materiella anläggningstillgångar\n"
        "Anskaffningsvärde\n"
        "Lagtext\n"
        "4 kap. 3 § årsredovisningslagen\n"
        "Anläggningstillgångar skall tas upp till belopp som motsvarar utgifterna.\n"
        "Allmänt råd\n"
        "12.7 I anskaffningsvärdet ingår inköpspriset och utgifter som är direkt\n"
        "hänförliga till förvärvet.\n"
        "Kommentar\n"
        "Med anskaffningsvärde avses enligt 4 kap. 3 § ÅRL det belopp som\n"
        "motsvarar utgifterna för tillgångens förvärv.\n"
    ),
]


def _parsa_k3():
    return bfn_parser.parsa(
        Path("vl12-1-k3-kons20251215.pdf"),
        dok_id="vl12-1-k3-kons20251215",
        titel="K3 Årsredovisning och koncernredovisning",
        typ="vagledning", kategori="k_regelverk", niva="karna",
        url="https://www.bfn.se/wp-content/uploads/vl12-1-k3-kons20251215.pdf",
        sha256="0" * 64,
        sidor=_K3_SIDOR,
    )


def test_parsern_skiljer_allmant_rad_fran_kommentar():
    """Det bindande och det förklarande får aldrig slås ihop."""
    dok = _parsa_k3()
    typer = {b.typ for b in dok.block}
    assert {"lagtext", "allmant_rad", "kommentar"} <= typer

    rad = [b for b in dok.block if b.typ == "allmant_rad"]
    kommentar = [b for b in dok.block if b.typ == "kommentar"]
    assert len(rad) == 1 and len(kommentar) == 1
    assert rad[0].punkt == "12.7"
    assert "inköpspriset" in rad[0].text
    # Kommentaren får INTE ha punktnummer — den är inte rådet.
    assert kommentar[0].punkt is None
    assert "Med anskaffningsvärde avses" in kommentar[0].text
    assert "Med anskaffningsvärde avses" not in rad[0].text


def test_punktmonstret_ater_inte_radets_forsta_ord():
    """Regressionsprov för ett fel i den källa parsern portades från.

    "12.7 I anskaffningsvärdet ingår ..." tolkades som punkt "12.7I" med
    texten "anskaffningsvärdet ingår ..." — förstaordet försvann. Felet syns
    inte i någon räkning: antalet råd blir rätt, bara texten blir fel.
    Gemena bokstäver är däremot BFN:s riktiga underpunkter och ska behållas.
    """
    dok = _parsa_k3()
    rad = next(b for b in dok.block if b.typ == "allmant_rad")
    assert rad.punkt == "12.7"
    assert rad.text.startswith("I anskaffningsvärdet")

    # Vidhängande bokstav är en ÄKTA underpunkt — i båda skiftlägena. BFN bär
    # 228 rader med versal underpunkt (1.1A, 2.1B, 20.9A), de flesta ur
    # BFNAR 2025:2. En regel på skiftläge hade tappat dem.
    underpunkt = bfn_parser.parsa(
        Path("x.pdf"), dok_id="x", titel="X", typ="allmant_rad",
        kategori="allmant_rad", niva="karna", url="", sha256="",
        sidor=["""Kapitel 11 – Finansiella instrument
11.39a En finansiell tillgång ska tas bort ur balansräkningen.
"""],
    )
    punkter = [b.punkt for b in underpunkt.block if b.typ == "allmant_rad"]
    assert punkter == ["11.39a"], "gement bokstavstillägg är en riktig underpunkt"

    versal = bfn_parser.parsa(
        Path("y.pdf"), dok_id="y", titel="Y", typ="allmant_rad",
        kategori="allmant_rad", niva="karna", url="", sha256="",
        sidor=["""Kapitel 1 – Tillämpning
1.1A Följande mindre företag får inte tillämpa detta allmänna råd.
1.1B Inte heller följande mindre företag får tillämpa det.
"""],
    )
    assert [b.punkt for b in versal.block if b.typ == "allmant_rad"] == ["1.1A", "1.1B"], (
        "versala underpunkter finns på riktigt och får inte falla bort"
    )

    # En bokstav EFTER mellanslag hör däremot till texten — det är en
    # hänvisning som inleder en mening, inte en egen punkt.
    hanvisning = bfn_parser.parsa(
        Path("z.pdf"), dok_id="z", titel="Z", typ="allmant_rad",
        kategori="allmant_rad", niva="karna", url="", sha256="",
        sidor=["""Kapitel 11 – Finansiella instrument
11.39 a omfattar ett sådant förfarande.
"""],
    )
    rad_h = [b for b in hanvisning.block if b.typ == "allmant_rad"]
    assert [b.punkt for b in rad_h] == ["11.39"]
    assert rad_h[0].text.startswith("a omfattar")


def test_parsern_laser_kapitel_avsnitt_och_version():
    dok = _parsa_k3()
    assert dok.uppdaterad == "2025-12-15", "titelsidans versionsstämpel ska läsas"
    assert dok.bfnar == "BFNAR 2012:1"
    assert len(dok.kapitel) == 1
    assert dok.kapitel[0]["nr"] == "12"
    assert "Materiella anläggningstillgångar" in dok.kapitel[0]["rubrik"]
    rad = next(b for b in dok.block if b.typ == "allmant_rad")
    assert rad.kapitel_nr == "12"
    assert rad.avsnitt == "Anskaffningsvärde"


def test_inskannad_pdf_ger_anmarkning_inte_tystnad():
    """En pdf utan textlager ska rapporteras, inte försvinna.

    Cirka en åttondel av bfn.se:s pdf:er är inskannade. Systemet gör inte OCR
    — men ett dokument som tyst faller bort ur indexet är osynligt, medan ett
    med anmärkning syns i GET /matning.
    """
    dok = bfn_parser.parsa(
        Path("inskannad.pdf"), dok_id="inskannad", titel="Inskannad",
        typ="okant", kategori="ovrigt", niva="stod", url="", sha256="",
        sidor=["", "   ", ""],
    )
    assert dok.block == []
    assert "textlager" in dok.parsningsanmarkning
    assert "OCR" in dok.parsningsanmarkning


def test_fristaende_bfnar_utan_blockmarkorer_far_numrerade_rad():
    """De fristående allmänna råden saknar markörer — hela dokumentet ÄR rådet.

    Utan den grenen i parsern blev ett helt BFNAR ett enda brödtextblock.
    """
    dok = bfn_parser.parsa(
        Path("bfnar16-10-grund.pdf"), dok_id="bfnar16-10-grund",
        titel="K2", typ="allmant_rad", kategori="allmant_rad", niva="karna",
        url="", sha256="",
        sidor=[
            "Bokföringsnämndens allmänna råd BFNAR 2016:10",
            "Kapitel 4 – Uppställningsformer\n"
            "4.1 En årsredovisning ska upprättas på svenska.\n"
            "4.2 Beloppen ska anges i svenska kronor.\n",
        ],
    )
    rad = [b for b in dok.block if b.typ == "allmant_rad"]
    assert [b.punkt for b in rad] == ["4.1", "4.2"]


# ---------------------------------------------------------------------------
# 3. Uppräkningen
# ---------------------------------------------------------------------------


def test_uppräkningen_foljer_sidhuvudet_inte_antalet_poster(monkeypatch):
    """X-WP-TotalPages styr, inte "färre än per_page betyder sista sidan".

    bfn.se lämnar 93 poster på en sida med per_page=100 mitt i en följd om
    950 — WordPress filtrerar efter att sidan skurits ut. Den trubbiga
    regeln hade avslutat uppräkningen på första sidan.
    """
    from quiet_oppen_data.adaptrar import transport
    from quiet_oppen_data.index import bfn_skord

    anrop: list[int] = []

    def falsk_hamta(kalla_id, method, url, **kwargs):
        sida = kwargs["params"]["page"]
        anrop.append(sida)
        # Sida 1 och 2 är "korta" — precis det som lurade den gamla regeln.
        poster = [{"id": i} for i in range(93 if sida < 3 else 40)]
        return transport.RaSvar(
            innehall=json.dumps(poster).encode("utf-8"),
            huvuden={"x-wp-totalpages": "3", "x-wp-total": "226"},
            url=url,
        )

    monkeypatch.setattr(bfn_skord.transport, "hamta_ocachat", falsk_hamta)
    ut = bfn_skord._rest_alla("media", "id")

    assert anrop == [1, 2, 3], "alla tre sidorna ska hämtas"
    assert len(ut) == 226


def test_externa_lankar_hamtas_inte():
    """BFN länkar till andra utgivares pdf:er. De får inte hämtas härifrån.

    De omfattas inte av BFN:s PSI-villkor, och en fil hämtad via den här
    källan skulle bära attributionen "Källa: Bokföringsnämnden" — vilket vore
    fel om dokumentet är Regeringskansliets.
    """
    from quiet_oppen_data.index import bfn_skord

    reg = bfnregister.las()
    extern = bfn_skord.Bilaga(
        filnamn="sou-2021-x.pdf",
        url="https://www.regeringen.se/contentassets/sou-2021-x.pdf",
        titel="SOU", kategori="remissvar", typ="skrivelse", niva="karna",
    )
    rakning = bfn_skord.ladda_ner({"sou-2021-x.pdf": extern}, reg)
    assert rakning["externa"] == 1
    assert rakning["hamtade"] == 0
    assert extern.status == "extern_lank"


# ---------------------------------------------------------------------------
# 4. Indexering och adapter
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path):
    conn = oppna_db(tmp_path / "index.sqlite")
    yield conn
    conn.close()


def _skriv_provdokument(conn):
    dok = KorpusDokument(
        korpus="bfn",
        dok_id="vl12-1-k3-kons20251215",
        titel="K3 Årsredovisning och koncernredovisning",
        kortnamn="BFNAR 2012:1",
        utgivare="Bokföringsnämnden (BFN)",
        lydelse="2025-12-15",
        version="a" * 64,
        licens="PSI",
        attribution="Källa: Bokföringsnämnden (BFN), uppdaterad 2025-12-15",
        hamtad=datetime.now(UTC).isoformat(),
        lank_manniska="https://www.bfn.se/redovisningsregler/vagledningar/k-regelverk/",
        lank_maskin="https://www.bfn.se/wp-content/uploads/vl12-1-k3-kons20251215.pdf",
        chunkar=[
            KorpusChunk(beteckning="12.7", blocktyp="allmant_rad",
                        text="I anskaffningsvärdet ingår inköpspriset.",
                        kapitel_nr="12", kapitel_rubrik="Materiella anläggningstillgångar",
                        avsnitt="Anskaffningsvärde", sida=88),
            KorpusChunk(beteckning=None, blocktyp="kommentar",
                        text="Med anskaffningsvärde avses utgifterna för förvärvet.",
                        kapitel_nr="12", kapitel_rubrik="Materiella anläggningstillgångar",
                        avsnitt="Anskaffningsvärde", sida=88),
        ],
    )
    return dok, skriv_dokument(conn, dok, generera_vektorer=False)


def test_skriv_dokument_lagrar_blocktyp_och_kallfalt(db):
    dok, antal = _skriv_provdokument(db)
    assert antal == 2

    rad = db.execute(
        "SELECT titel, kortnamn, lydelse, licens, attribution, lank_maskin "
        "FROM korpus_dokument WHERE id = ?", (dok.id,)
    ).fetchone()
    assert rad[1] == "BFNAR 2012:1"
    assert rad[2] == "2025-12-15"
    assert rad[5].endswith(".pdf")

    typer = [r[0] for r in db.execute(
        "SELECT blocktyp FROM korpus_chunk WHERE dokument_id = ? ORDER BY blocktyp", (dok.id,)
    )]
    assert typer == ["allmant_rad", "kommentar"]


def test_omindexering_ersatter_i_stallet_for_att_dubblera(db):
    dok, _ = _skriv_provdokument(db)
    skriv_dokument(db, dok, generera_vektorer=False)
    antal = db.execute(
        "SELECT COUNT(*) FROM korpus_chunk WHERE dokument_id = ?", (dok.id,)
    ).fetchone()[0]
    assert antal == 2, "en omindexering får inte lämna kvar de gamla styckena"
    fts = db.execute(
        "SELECT COUNT(*) FROM korpus_chunk_fts WHERE id LIKE ?", (f"{dok.id}#%",)
    ).fetchone()[0]
    assert fts == 2, "FTS-tabellen ska rensas tillsammans med chunkarna"


def test_dokument_utan_chunkar_skrivs_med_anmarkning(db):
    """En inskannad pdf ska finnas i indexet som en känd lucka."""
    dok = KorpusDokument(
        korpus="bfn", dok_id="inskannad", titel="Inskannad vägledning",
        utgivare="Bokföringsnämnden (BFN)",
        lank_manniska="https://www.bfn.se/", lank_maskin="https://www.bfn.se/x.pdf",
        anmarkning="Pdf:en saknar textlager (troligen inskannad).",
        chunkar=[],
    )
    assert skriv_dokument(db, dok, generera_vektorer=False) == 0
    rad = db.execute(
        "SELECT anmarkning FROM korpus_dokument WHERE id = ?", (dok.id,)
    ).fetchone()
    assert rad is not None, "dokumentet ska finnas kvar trots noll chunkar"
    assert "textlager" in rad[0]


def _sokresultat(blocktyp: str, beteckning: str | None):
    """Ett träffobjekt som om det kommit ur indexet.

    Adaptertesterna konstruerar träffen direkt i stället för att gå via
    sok_korpus. Det som prövas här är översättningen träff -> Faktautkast, och
    att blanda in embedding-modellen i det provet hade gjort det långsamt utan
    att pröva något mer.
    """
    from quiet_oppen_data.modeller import KorpusSokresultat

    return KorpusSokresultat(
        chunk_id="bfn:vl12-1-k3#x~1", korpus="bfn",
        dokument_id="bfn:vl12-1-k3-kons20251215",
        dok_id="vl12-1-k3-kons20251215",
        titel="K3 Årsredovisning och koncernredovisning",
        kortnamn="BFNAR 2012:1", utgivare="Bokföringsnämnden (BFN)",
        blocktyp=blocktyp, beteckning=beteckning,
        kapitel_nr="12", kapitel_rubrik="Materiella anläggningstillgångar",
        avsnitt="Anskaffningsvärde",
        text="I anskaffningsvärdet ingår inköpspriset.", sida=88,
        lydelse="2025-12-15", hamtad=datetime.now(UTC).isoformat(),
        lank_manniska="https://www.bfn.se/redovisningsregler/vagledningar/k-regelverk/",
        lank_maskin="https://www.bfn.se/wp-content/uploads/vl12-1-k3-kons20251215.pdf",
        licens="PSI", attribution="Källa: Bokföringsnämnden (BFN), uppdaterad 2025-12-15",
        relevans=1.0, full_text="...",
    )


def test_adaptern_for_blocktypen_vidare_till_faktautkastet(monkeypatch):
    """Blocktypen måste överleva hela vägen ut, annars är den verkningslös."""
    from quiet_oppen_data.adaptrar import bfn as bfn_adapter
    from quiet_oppen_data.modeller import Fragplan

    monkeypatch.setattr(bfn_adapter, "sok_korpus",
                        lambda **kw: [_sokresultat("allmant_rad", "12.7")])

    utkast = bfn_adapter.BfnAdapter().hamta(
        Fragplan(fraga="anskaffningsvärde", extra={"sok": "anskaffningsvärde"})
    )
    assert utkast, "ett allmänt råd ska hittas"
    u = utkast[0]
    assert u.dimensioner["blocktyp"] == "allmant_rad"
    assert u.dimensioner["punkt"] == "12.7"
    assert u.dimensioner["bfnar"] == "BFNAR 2012:1"
    assert u.dimensioner["sida"] == "88"
    assert u.period == "2025-12-15", "period ska bära dokumentets lydelse"
    assert u.lank_manniska and u.lank_maskin
    assert "BFN" in (u.attribution or "")
    # Ett bindande råd ska INTE märkas som något annat.
    assert "ej bindande" not in u.etikett
    assert "12.7" in u.etikett


def test_adaptern_markerar_kommentar_som_ej_bindande(monkeypatch):
    """BFN:s kommentar är inte normgivning och får inte läsas som om den vore det."""
    from quiet_oppen_data.adaptrar import bfn as bfn_adapter
    from quiet_oppen_data.modeller import Fragplan

    monkeypatch.setattr(bfn_adapter, "sok_korpus",
                        lambda **kw: [_sokresultat("kommentar", None)])

    utkast = bfn_adapter.BfnAdapter().hamta(
        Fragplan(fraga="x", extra={"sok": "anskaffningsvärde", "blocktyp": "kommentar"})
    )
    assert utkast
    assert "ej bindande" in utkast[0].etikett, (
        "en kommentar måste märkas ut i etiketten — den syns i svaret, "
        "till skillnad från dimensionerna"
    )
    assert utkast[0].dimensioner["blocktyp"] == "kommentar"


def test_adaptern_ignorerar_okand_blocktyp(monkeypatch):
    """Ett okänt filter skulle annars se ut som att BFN inget hade att säga."""
    from quiet_oppen_data.adaptrar import bfn as bfn_adapter
    from quiet_oppen_data.modeller import Fragplan

    sedda: dict = {}

    def falsk(**kw):
        sedda.update(kw)
        return [_sokresultat("allmant_rad", "12.7")]

    monkeypatch.setattr(bfn_adapter, "sok_korpus", falsk)
    bfn_adapter.BfnAdapter().hamta(
        Fragplan(fraga="x", extra={"sok": "x", "blocktyp": "hittepa"})
    )
    assert sedda["blocktyp_filter"] is None


# ---------------------------------------------------------------------------
# Ersatta lydelser och tunna parsningar (rättat efter första skarpa körningen)
# ---------------------------------------------------------------------------


def test_ersatt_konsolidering_utesluts_ur_sokningen(db):
    """BFN låter gamla konsolideringar ligga kvar på webbplatsen.

    Efter en skörd finns både vl12-1-k3-kons2024 och vl12-1-k3-kons20251215.
    Utan det här steget kan 2024 års lydelse citeras som gällande K3, med en
    korrekt källänk — samma fel som att indexera en EU-rättsakts
    ursprungslydelse i stället för den konsoliderade.
    """
    from quiet_oppen_data.index.bfn_ingest import markera_ersatta

    for dok_id, lydelse in (("vl12-1-k3-kons2024", "2024-12-13"),
                            ("vl12-1-k3-kons20251215", "2025-12-15")):
        skriv_dokument(db, KorpusDokument(
            korpus="bfn", dok_id=dok_id, titel="K3", kortnamn="BFNAR 2012:1",
            utgivare="Bokföringsnämnden (BFN)", lydelse=lydelse,
            lank_manniska="https://www.bfn.se/", lank_maskin="https://www.bfn.se/x.pdf",
            chunkar=[KorpusChunk(beteckning="12.7", blocktyp="allmant_rad", text="text")],
        ), generera_vektorer=False)

    ersatta = markera_ersatta(db)
    assert [e["dok_id"] for e in ersatta] == ["vl12-1-k3-kons2024"]
    assert ersatta[0]["ersatt_av"] == "vl12-1-k3-kons20251215"

    def chunkar(dok_id):
        return db.execute(
            "SELECT COUNT(*) FROM korpus_chunk WHERE dokument_id = ?", (f"bfn:{dok_id}",)
        ).fetchone()[0]

    assert chunkar("vl12-1-k3-kons2024") == 0, "den ersatta lydelsen ska bort ur sökningen"
    assert chunkar("vl12-1-k3-kons20251215") == 1, "den gällande ska vara kvar"

    # Dokumentraden ligger kvar med sin anmärkning — en medveten uteslutning
    # ska synas i /matning, inte försvinna.
    anm = db.execute(
        "SELECT anmarkning FROM korpus_dokument WHERE id = ?", ("bfn:vl12-1-k3-kons2024",)
    ).fetchone()[0]
    assert "Ersatt lydelse" in anm
    assert "vl12-1-k3-kons20251215" in anm


def test_dokument_utan_datum_rangordnas_inte(db):
    """Utan källans egen lydelseuppgift finns inget att jämföra på.

    Att rangordna på filnamn vore att gissa, och gissningen skulle avgöra
    vilken lydelse som citeras som gällande.
    """
    from quiet_oppen_data.index.bfn_ingest import markera_ersatta

    for dok_id in ("bfnar02-3-grund", "bfnar02-3-kons2"):
        skriv_dokument(db, KorpusDokument(
            korpus="bfn", dok_id=dok_id, titel="Värdering", kortnamn="BFNAR 2002:3",
            utgivare="BFN", lydelse="",
            lank_manniska="https://www.bfn.se/", lank_maskin="https://www.bfn.se/x.pdf",
            chunkar=[KorpusChunk(beteckning="1.1", blocktyp="allmant_rad", text="text")],
        ), generera_vektorer=False)

    assert markera_ersatta(db) == [], "utan datum ska ingen uteslutas"
    for dok_id in ("bfnar02-3-grund", "bfnar02-3-kons2"):
        n = db.execute("SELECT COUNT(*) FROM korpus_chunk WHERE dokument_id = ?",
                       (f"bfn:{dok_id}",)).fetchone()[0]
        assert n == 1


def test_tunn_parsning_flaggas():
    """Halvt lyckad parsning är farligare än en misslyckad.

    vl17-3-ab-kons2024 gav 4 block ur 304 sidor och rapporterades ändå som
    "ok" vid den första skarpa körningen. Dokumentet såg indexerat ut men var
    nästan tomt.
    """
    dok = bfn_parser.parsa(
        Path("tunn.pdf"), dok_id="tunn", titel="Vägledning", typ="vagledning",
        kategori="k_regelverk", niva="karna", url="", sha256="",
        sidor=["Innehåll"] * 60 + ["Kapitel 1 – Tillämpning\n1.1 Detta råd gäller.\n"],
    )
    assert dok.block, "det finns text — detta är inte ett tomt dokument"
    assert "Ovanligt få block" in dok.parsningsanmarkning
    assert "per sida" in dok.parsningsanmarkning


def test_kort_dokument_flaggas_inte():
    """Ett äkta kort BFNAR ska inte larma. Gränsen gäller bara långa dokument."""
    dok = bfn_parser.parsa(
        Path("kort.pdf"), dok_id="kort", titel="BFNAR", typ="allmant_rad",
        kategori="allmant_rad", niva="karna", url="", sha256="",
        sidor=["Bokföringsnämndens allmänna råd\nDetta råd träder i kraft den 1 januari.\n"],
    )
    assert dok.block
    assert dok.parsningsanmarkning == ""


def test_inledande_blockmarkor_delas_ut(monkeypatch):
    """I några av BFN:s mallar inleder markören texten i stället för att stå ensam.

    vl20-5-redovisning-av-fusion gav 6 block ur 113 sidor trots att texten
    innehöll 39 "Allmänt råd" och 31 "Kommentar" — parsern såg dem inte,
    eftersom de aldrig stod ensamma på en rad.
    """
    dok = bfn_parser.parsa(
        Path("fusion.pdf"), dok_id="fusion", titel="Fusion", typ="vagledning",
        kategori="vagledning_ovrig", niva="karna", url="", sha256="",
        sidor=["""Allmänt råd 3.1 Detta kapitel ska tillämpas vid nedströmsfusion.
Kommentar Värderingen av övertagna tillgångar påverkar inte fusionsdifferensen.
"""],
    )
    typer = {b.typ for b in dok.block}
    assert typer == {"allmant_rad", "kommentar"}
    rad = next(b for b in dok.block if b.typ == "allmant_rad")
    assert rad.punkt == "3.1"
    assert rad.text.startswith("Detta kapitel")
    kommentar = next(b for b in dok.block if b.typ == "kommentar")
    assert kommentar.text.startswith("Värderingen")


def test_loptext_som_borjar_med_markorord_delas_inte():
    """Kravet på versal eller siffra efter markören skiljer den från löptext.

    Utan det blev "Exempel på detta är ..." ett blockbyte, och meningen tappade
    sitt första ord — samma sorts tysta textförlust som versalen i _PUNKT gav.
    """
    dok = bfn_parser.parsa(
        Path("x.pdf"), dok_id="x", titel="X", typ="vagledning",
        kategori="vagledning_ovrig", niva="karna", url="", sha256="",
        sidor=["""Kapitel 2 – Tillämpning
Kommentar
Exempel på detta är när ett företag byter redovisningsprincip.
"""],
    )
    kommentar = next(b for b in dok.block if b.typ == "kommentar")
    assert "Exempel på detta är när ett företag" in kommentar.text
    assert not any(b.typ == "exempel" for b in dok.block), (
        "löptext som börjar med 'Exempel på' är inte ett exempelblock"
    )


def test_stycken_utan_sakinnehall_indexeras_inte(db):
    """Sidnummer och avdelarstreck blir sökträffar med ingenting i.

    En träff utan innehåll är värre än en träff mindre: modellen får en
    Faktapost att citera som inte säger något. Gränsen räknar BOKSTÄVER —
    en ren längdgräns hade tagit bort "Upphävd.", och att en punkt är upphävd
    är ett svar.
    """
    dok = KorpusDokument(
        korpus="bfn", dok_id="prov", titel="Prov", utgivare="BFN",
        lank_manniska="https://www.bfn.se/", lank_maskin="https://www.bfn.se/x.pdf",
        chunkar=[
            KorpusChunk(beteckning=None, blocktyp="brodtext", text="1"),
            KorpusChunk(beteckning=None, blocktyp="brodtext", text="1(5)"),
            KorpusChunk(beteckning=None, blocktyp="exempel", text="9."),
            KorpusChunk(beteckning=None, blocktyp="brodtext", text="_______________"),
            KorpusChunk(beteckning="4.4", blocktyp="allmant_rad",
                        text="Upphävd. (BFNAR 2013:3)."),
        ],
    )
    assert skriv_dokument(db, dok, generera_vektorer=False) == 1

    kvar = db.execute(
        "SELECT text FROM korpus_chunk WHERE dokument_id = ?", (dok.id,)
    ).fetchall()
    assert [r[0] for r in kvar] == ["Upphävd. (BFNAR 2013:3)."]
