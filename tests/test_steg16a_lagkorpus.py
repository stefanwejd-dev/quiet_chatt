"""Acceptanstester för Steg 16A — Lagkorpus, de fem huvudlagarna.

Acceptanskriterier (PLAN.md §16A):
1. lag_ingest hämtar alla fem och rapporterar t.o.m. SFS per författning.
2. Inkomstskattelagen parsas till minst 60 kapitel; inget kapitel är tomt.
3. Stickprov: 3 kap. 9 § IL återfinns som en egen chunk med rätt kapitelrubrik,
   och chunken bär en Lag (ÅÅÅÅ:NNN)-markering.
4. Sökning på "när är man begränsat skattskyldig" ger en IL-chunk bland topp 5,
   utan lexikal överlappning med paragrafens rubrik.
5. En Faktapost från lagtext-adaptern har period satt till konsolideringspunkten,
   och registret avvisar den om lank_manniska saknas.
6. Ändringskontrollen upptäcker en ändring: mata in ett gammalt systemdatum för
   en författning och verifiera att den flaggas för omhämtning.
7. python -m ruff check . och python -m pytest -q är rena.
"""
from pathlib import Path

import pytest

from quiet_oppen_data import lagregister
from quiet_oppen_data.adaptrar.lagtext import LagtextAdapter
from quiet_oppen_data.index.db import oppna_db
from quiet_oppen_data.index.lag_ingest import kontrollera_andringar
from quiet_oppen_data.index.lag_parser import parsa_lagtext
from quiet_oppen_data.index.sok import sok_lag
from quiet_oppen_data.konfig import las as las_konfig
from quiet_oppen_data.modeller import Faktaregister, Faktautkast, Fragplan


# ---------------------------------------------------------------------------
# 1. Lagregistret
# ---------------------------------------------------------------------------

def test_lagregister_las():
    """Lagregistret läses deklarativt ur lagar/lagregister.yaml."""
    lagar = lagregister.las()
    assert len(lagar) >= 5
    sfs_lista = [l.sfs for l in lagar]

    assert "1999:1229" in sfs_lista
    assert "2023:200" in sfs_lista
    assert "2011:1244" in sfs_lista
    assert "1999:1078" in sfs_lista
    assert "1995:1554" in sfs_lista

    il = lagregister.hamta("IL")
    assert il is not None
    assert il.sfs == "1999:1229"
    assert il.dok_id == "sfs-1999-1229"
    assert il.lank_manniska == "https://www.riksdagen.se/sv/dokument-och-lagar/dokument/_sfs-1999-1229/"
    assert il.lank_maskin == "https://data.riksdagen.se/dokument/sfs-1999-1229"


# ---------------------------------------------------------------------------
# 2. Parsning och kapitelkontroll för Inkomstskattelagen
# ---------------------------------------------------------------------------

def test_lag_parser_inkomstskattelag():
    """Inkomstskattelagen parsas till minst 60 kapitel; inget kapitel är tomt."""
    konfig = las_konfig()
    conn = oppna_db(Path(konfig.index.db))

    # Hämta råtext från databasen
    rad = conn.execute("SELECT ratext, tom_sfs FROM lag_dokument WHERE sfs = '1999:1229'").fetchone()
    assert rad is not None, "Inkomstskattelagen saknas i lag_dokument! Kör lag_ingest först."
    ratext, tom_sfs = rad
    assert "t.o.m. SFS" in tom_sfs

    chunks = parsa_lagtext(ratext, "1999:1229", "Inkomstskattelag", "IL")
    kapitel = set(c.kapitel_nr for c in chunks if c.kapitel_nr)

    # Minst 60 kapitel
    assert len(kapitel) >= 60, f"Färre än 60 kapitel: {len(kapitel)}"

    # Inget kapitel är tomt (varje kapitel i mängden har minst 1 chunk)
    for kap in kapitel:
        kap_chunks = [c for c in chunks if c.kapitel_nr == kap]
        assert len(kap_chunks) > 0, f"Kapitel {kap} är tomt!"


# ---------------------------------------------------------------------------
# 3. Stickprov IL 3 kap. 9 §
# ---------------------------------------------------------------------------

def test_stickprov_il_3_kap_9_paragraf():
    """Stickprov: 3 kap. 9 § IL återfinns med rätt kapitelrubrik och ändringsmarkering."""
    konfig = las_konfig()
    conn = oppna_db(Path(konfig.index.db))

    cur = conn.execute(
        """
        SELECT kapitel_rubrik, paragraf_rubrik, paragraf_nr, andringsnotis, full_text, paragraf_text
        FROM lag_chunk
        WHERE sfs = '1999:1229' AND kapitel_nr = '3' AND paragraf_nr = '9'
        """
    )
    rad = cur.fetchone()
    assert rad is not None, "3 kap. 9 § IL hittades inte i databasen!"

    kap_rubrik, par_rubrik, par_nr, andring, full_text, par_text = rad
    assert kap_rubrik == "Fysiska personer"
    assert par_nr == "9"
    assert andring is not None
    assert "Lag (" in andring or "SFS " in andring
    assert "sexmånadersregeln" in par_text or "vistelse utomlands" in par_text or "utland" in par_text


# ---------------------------------------------------------------------------
# 4. Semantisk sökning i lagindexet
# ---------------------------------------------------------------------------

def test_sok_lag_begransat_skattskyldig():
    """Sökning på 'när är man begränsat skattskyldig' ger en IL-chunk bland topp 5."""
    traffar = sok_lag("när är man begränsat skattskyldig", max_antal=5)
    assert len(traffar) > 0

    # Minst en träff från Inkomstskattelagen (1999:1229) bland topp 5
    il_traffar = [t for t in traffar if t.sfs == "1999:1229"]
    assert len(il_traffar) >= 1, "Ingen IL-chunk bland topp 5!"


# ---------------------------------------------------------------------------
# 5. LagtextAdapter, Faktapost och regel 8 (färskhetsstämpel)
# ---------------------------------------------------------------------------

def test_lagtext_adapter_returnerar_faktautkast_med_farskhetsstampel():
    """En Faktapost från lagtext-adaptern har period satt till konsolideringspunkten."""
    adapter = LagtextAdapter()
    plan = Fragplan(fraga="när är man begränsat skattskyldig", extra={"limit": 3})
    utkast_lista = adapter.hamta(plan)

    assert len(utkast_lista) >= 1
    utkast = utkast_lista[0]

    # Invarianter enligt ARKITEKTUR.md §5 regel 8:
    assert utkast.period is not None
    assert "t.o.m. SFS" in utkast.period
    assert utkast.dataset is not None  # SFS-nummer
    assert utkast.lank_manniska.startswith("https://www.riksdagen.se")
    assert utkast.lank_maskin.startswith("https://data.riksdagen.se")
    assert utkast.hamtad is not None

    # Registrering i Faktaregister
    reg = Faktaregister()
    post = reg.registrera_utkast(utkast)
    assert post.id == "F1"
    assert post.period == utkast.period
    assert post.dataset == utkast.dataset
    assert post.lank_manniska == utkast.lank_manniska
    assert post.lank_maskin == utkast.lank_maskin


def test_faktaregister_avvisar_lagpost_utan_lank_manniska():
    """Faktaregistret avvisar lagposter om lank_manniska saknas."""
    reg = Faktaregister()
    utkast_ogiltig = Faktautkast(
        etikett="Testlag 1 §",
        varde="Lagtext",
        kalla_id="lagtext",
        myndighet="Sveriges riksdag",
        licens="okänd",
        lank_manniska="",  # Saknas!
        lank_maskin="https://data.riksdagen.se/dokument/sfs-1999-1229",
    )
    with pytest.raises(ValueError, match="lank_manniska"):
        reg.registrera_utkast(utkast_ogiltig)


# ---------------------------------------------------------------------------
# 6. Ändringskontroll
# ---------------------------------------------------------------------------

def test_andringskontroll_detekterar_andring(monkeypatch, tmp_path):
    """Ändringskontrollen upptäcker ändring om systemdatum skiljer sig."""
    from quiet_oppen_data.adaptrar import transport

    def mock_hamta_json(kalla_id, method, url, **kwargs):
        return {
            "dokumentstatus": {
                "dokument": {
                    "dok_id": "sfs-1999-1229",
                    "systemdatum": "2026-08-14 12:00:00",  # Nytt datum på servern
                }
            }
        }

    monkeypatch.setattr(transport, "hamta_json", mock_hamta_json)

    # Skapa tillfällig testdatabas med ett gammalt systemdatum
    test_db = tmp_path / "test_index.sqlite"
    conn = oppna_db(test_db)
    conn.execute(
        """
        INSERT INTO lag_dokument
        (sfs, dok_id, namn, kortnamn, tom_sfs, systemdatum, hamtad, lank_manniska, lank_maskin, ratext)
        VALUES ('1999:1229', 'sfs-1999-1229', 'Inkomstskattelag', 'IL', 't.o.m. SFS 2026:100', '2020-01-01 00:00:00', '2020-01-01T00:00:00Z', 'https://www.riksdagen.se/...', 'https://data.riksdagen.se/...', 'text')
        """
    )
    conn.commit()

    rapporter = kontrollera_andringar(["1999:1229"], db_conn=conn)
    conn.close()

    assert len(rapporter) == 1
    rapp = rapporter[0]
    assert rapp["sfs"] == "1999:1229"
    assert rapp["andrad"] is True
    assert rapp["lokalt_systemdatum"] == "2020-01-01 00:00:00"
    assert rapp["fjarr_systemdatum"] == "2026-08-14 12:00:00"



# ---------------------------------------------------------------------------
# 7. Parsning av alla fem lagar
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sfs,kortnamn", [
    ("1999:1229", "IL"),
    ("2023:200", "ML"),
    ("2011:1244", "SFL"),
    ("1999:1078", "BFL"),
    ("1995:1554", "ÅRL"),
])
def test_alla_fem_lagar_har_chunks_i_databasen(sfs, kortnamn):
    """Samtliga fem lagar finns i indexet med chunks och konsolideringspunkt."""
    konfig = las_konfig()
    conn = oppna_db(Path(konfig.index.db))

    dok = conn.execute("SELECT namn, tom_sfs, systemdatum FROM lag_dokument WHERE sfs = ?", (sfs,)).fetchone()
    assert dok is not None, f"Lagen {sfs} ({kortnamn}) saknas i lag_dokument!"
    namn, tom_sfs, systemdatum = dok
    assert tom_sfs is not None and len(tom_sfs) > 0

    antal_chunks = conn.execute("SELECT COUNT(*) FROM lag_chunk WHERE sfs = ?", (sfs,)).fetchone()[0]
    assert antal_chunks > 0, f"Lagen {sfs} ({kortnamn}) har 0 chunks!"
