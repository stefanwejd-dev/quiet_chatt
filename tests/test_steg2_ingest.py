"""Enhetstester för Steg 2 — parsning, OGC-filter, DB-schema.

Dessa tester anropar INTE myndigheters API:er (ARKITEKTUR.md §9).
Live-ingesten körs separat som acceptanstest.
"""
from __future__ import annotations

import pytest

from quiet_oppen_data.index.db import oppna_db
from quiet_oppen_data.index.ingest import (
    DC_FORMAT,
    DC_PUBLISHER,
    DC_TITLE,
    DCAT_ACCESS_URL,
    DCAT_DISTRIBUTION,
    DCAT_IS_DIST_OF,
    ES_RESOURCE,
    ar_ogc,
    behandla_dataset,
    behandla_distribution,
    bygg_manniskolank,
    hamta_entry_och_resurs,
    hamta_text,
    hamta_utgivare,
)

# ---------------------------------------------------------------------------
# Testdata
# ---------------------------------------------------------------------------

ENTRY_URL  = "https://admin.dataportal.se/store/5/entry/1234"
RESURS_URI = "https://datasets.dataportal.se/resource/abc123"
DIST_URI   = "https://distributions.dataportal.se/resource/dist001"


def _dataset_barn(
    resurs_uri: str = RESURS_URI,
    entry_url: str = ENTRY_URL,
    titel: str = "Testdataset",
    dist_uris: list[str] | None = None,
    extra_meta: dict | None = None,
) -> dict:
    meta = {
        DC_TITLE: [{"value": titel, "lang": "sv"}],
    }
    if dist_uris:
        meta[DCAT_DISTRIBUTION] = [{"value": u} for u in dist_uris]
    if extra_meta:
        meta.update(extra_meta)
    return {
        "info": {entry_url: {ES_RESOURCE: [{"value": resurs_uri}]}},
        "metadata": {resurs_uri: meta},
    }


def _dist_barn(
    dist_uri: str = DIST_URI,
    dataset_uri: str | None = None,
    format_str: str | None = None,
    access_url: str | None = "https://example.se/data.csv",
    titel: str | None = None,
) -> dict:
    meta: dict = {}
    if format_str:
        meta[DC_FORMAT] = [{"value": format_str}]
    if access_url:
        meta[DCAT_ACCESS_URL] = [{"value": access_url}]
    if dataset_uri:
        meta[DCAT_IS_DIST_OF] = [{"value": dataset_uri}]
    if titel:
        meta[DC_TITLE] = [{"value": titel, "lang": "sv"}]
    return {
        "info": {"https://admin.dataportal.se/store/5/entry/dist001": {
            ES_RESOURCE: [{"value": dist_uri}]
        }},
        "metadata": {dist_uri: meta},
    }


# ---------------------------------------------------------------------------
# hamta_entry_och_resurs
# ---------------------------------------------------------------------------

def test_hamta_entry_och_resurs_returnerar_tuple():
    barn = _dataset_barn()
    result = hamta_entry_och_resurs(barn)
    assert result is not None
    entry_url, resurs_uri = result
    assert entry_url == ENTRY_URL
    assert resurs_uri == RESURS_URI


def test_hamta_entry_och_resurs_tomt_info_ger_none():
    assert hamta_entry_och_resurs({"info": {}, "metadata": {}}) is None


def test_hamta_entry_och_resurs_saknad_resurs_ger_none():
    barn = {"info": {ENTRY_URL: {}}, "metadata": {}}  # ES_RESOURCE saknas
    assert hamta_entry_och_resurs(barn) is None


# ---------------------------------------------------------------------------
# hamta_text — språkprioritet
# ---------------------------------------------------------------------------

def test_hamta_text_foredrager_sv():
    meta = {DC_TITLE: [
        {"value": "English", "lang": "en"},
        {"value": "Svenska", "lang": "sv"},
    ]}
    assert hamta_text(meta, DC_TITLE) == "Svenska"


def test_hamta_text_fallback_en():
    meta = {DC_TITLE: [{"value": "English only", "lang": "en"}]}
    assert hamta_text(meta, DC_TITLE) == "English only"


def test_hamta_text_tomt_ger_none():
    assert hamta_text({}, DC_TITLE) is None


# ---------------------------------------------------------------------------
# bygg_manniskolank
# ---------------------------------------------------------------------------

def test_bygg_manniskolank_standard():
    url = bygg_manniskolank("https://admin.dataportal.se/store/5/entry/1234")
    assert url == "https://www.dataportal.se/datasets/5_1234"


def test_bygg_manniskolank_utan_entry_infix():
    url = bygg_manniskolank("https://admin.dataportal.se/store/99/1234")
    assert url == "https://www.dataportal.se/datasets/99_1234"


def test_bygg_manniskolank_ogiltig_ger_none():
    assert bygg_manniskolank("https://example.com") is None


# ---------------------------------------------------------------------------
# hamta_utgivare — URI-fallback tvättar bort formulärkodning
# ---------------------------------------------------------------------------

def test_hamta_utgivare_fallback_tvattar_plustecken():
    """Upptäckt 2026-08-16: Umeå kommuns källa har "+" i stället för
    mellanslag i utgivar-URI:ns sista segment — en kvarleva av
    URL-formulärkodning i källdatan. unquote_plus tvättar bort det."""
    meta = {DC_PUBLISHER: [{
        "type": "uri",
        "value": "https://resources.stockholm.se/metadata#foaf/Stockholms+stad+-+Miljöförvaltningen/",
    }]}
    assert hamta_utgivare(meta, {}) == "Stockholms stad - Miljöförvaltningen"


# ---------------------------------------------------------------------------
# ar_ogc
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("format_str,access_url,titel,förväntat", [
    ("application/vnd.ogc.wms_xml", None, None, True),
    ("WMS", None, None, True),
    ("wfs", None, None, True),
    ("text/csv", None, None, False),
    ("application/json", None, None, False),
    (None, "https://geo.example.se/wms?SERVICE=WMS", None, True),
    (None, None, "Kommunal visningstjänst", True),
    (None, None, "Statistik och information", False),  # inte suffix överhuvudtaget
    (None, None, "Min nedladdningstjänst", True),
    ("CSV", "https://data.example.se/file.csv", "Normal data", False),
])
def test_ar_ogc(format_str, access_url, titel, förväntat):
    assert ar_ogc(format_str, access_url, titel) is förväntat


# ---------------------------------------------------------------------------
# behandla_dataset — DB-skrivning
# ---------------------------------------------------------------------------

def test_behandla_dataset_ny(tmp_path):
    conn = oppna_db(tmp_path / "test.sqlite")
    n = behandla_dataset(conn, _dataset_barn(titel="KPI-statistik"))
    assert n == 1
    rad = conn.execute("SELECT id, titel FROM datamangd").fetchone()
    assert rad[0] == RESURS_URI
    assert rad[1] == "KPI-statistik"


def test_behandla_dataset_idempotent(tmp_path):
    conn = oppna_db(tmp_path / "test.sqlite")
    barn = _dataset_barn()
    behandla_dataset(conn, barn)
    n = behandla_dataset(conn, barn)   # andra gången → skip
    assert n == 0
    count = conn.execute("SELECT COUNT(*) FROM datamangd").fetchone()[0]
    assert count == 1


def test_behandla_dataset_skriver_manniskolank(tmp_path):
    conn = oppna_db(tmp_path / "test.sqlite")
    behandla_dataset(conn, _dataset_barn())
    rad = conn.execute("SELECT manniskolank FROM datamangd").fetchone()
    assert rad[0] == "https://www.dataportal.se/datasets/5_1234"


def test_behandla_dataset_bygger_dist_link(tmp_path):
    conn = oppna_db(tmp_path / "test.sqlite")
    behandla_dataset(conn, _dataset_barn(dist_uris=["https://dist.example.se/abc"]))
    rad = conn.execute("SELECT dataset_id FROM _dist_dataset_link WHERE dist_uri=?",
                       ("https://dist.example.se/abc",)).fetchone()
    assert rad is not None
    assert rad[0] == RESURS_URI


# ---------------------------------------------------------------------------
# FTS5-sökning
# ---------------------------------------------------------------------------

def test_fts5_sokning_pa_titel(tmp_path):
    conn = oppna_db(tmp_path / "test.sqlite")
    behandla_dataset(conn, _dataset_barn(titel="Momsstatistik Sverige"))
    conn.commit()
    rad = conn.execute(
        "SELECT id FROM datamangd_fts WHERE datamangd_fts MATCH 'moms*'"
    ).fetchone()
    assert rad is not None


def test_fts5_ingen_dubblett_vid_omstart(tmp_path):
    conn = oppna_db(tmp_path / "test.sqlite")
    barn = _dataset_barn(titel="Unik titel xyz")
    behandla_dataset(conn, barn)
    conn.commit()
    behandla_dataset(conn, barn)   # omstart → INSERT OR IGNORE
    conn.commit()
    rader = conn.execute(
        "SELECT COUNT(*) FROM datamangd_fts WHERE datamangd_fts MATCH 'xyz'"
    ).fetchone()[0]
    assert rader == 1


# ---------------------------------------------------------------------------
# behandla_distribution
# ---------------------------------------------------------------------------

def test_behandla_distribution_ny(tmp_path):
    conn = oppna_db(tmp_path / "test.sqlite")
    n = behandla_distribution(conn, _dist_barn())
    assert n == 1
    rad = conn.execute("SELECT id FROM distribution").fetchone()
    assert rad[0] == DIST_URI


def test_behandla_distribution_ogc_filtreras(tmp_path):
    conn = oppna_db(tmp_path / "test.sqlite")
    # WMS-format ska filtreras
    n = behandla_distribution(conn, _dist_barn(format_str="WMS", access_url="https://geo.se/wms"))
    assert n == 0
    assert conn.execute("SELECT COUNT(*) FROM distribution").fetchone()[0] == 0


def test_behandla_distribution_ogc_via_url(tmp_path):
    conn = oppna_db(tmp_path / "test.sqlite")
    n = behandla_distribution(conn, _dist_barn(
        format_str=None,
        access_url="https://geodata.naturvardsverket.se/wfs?SERVICE=WFS",
    ))
    assert n == 0


def test_behandla_distribution_ogc_via_titel(tmp_path):
    conn = oppna_db(tmp_path / "test.sqlite")
    n = behandla_distribution(conn, _dist_barn(titel="Naturvårdsverkets visningstjänst"))
    assert n == 0


def test_behandla_distribution_lankar_dataset_via_is_dist_of(tmp_path):
    conn = oppna_db(tmp_path / "test.sqlite")
    # Lägg till parent i datamangd (FK måste existera eller vara NULL)
    conn.execute("INSERT INTO datamangd (id, titel) VALUES (?,?)", (RESURS_URI, "Parent"))
    conn.commit()
    n = behandla_distribution(conn, _dist_barn(dataset_uri=RESURS_URI))
    assert n == 1
    rad = conn.execute("SELECT datamangd_id FROM distribution WHERE id=?", (DIST_URI,)).fetchone()
    assert rad[0] == RESURS_URI


def test_behandla_distribution_lankar_dataset_via_link_tabell(tmp_path):
    conn = oppna_db(tmp_path / "test.sqlite")
    conn.execute("INSERT INTO datamangd (id, titel) VALUES (?,?)", (RESURS_URI, "Parent"))
    conn.execute("INSERT INTO _dist_dataset_link (dist_uri, dataset_id) VALUES (?,?)",
                 (DIST_URI, RESURS_URI))
    conn.commit()
    n = behandla_distribution(conn, _dist_barn())  # ingen dcat:isDistributionOf i meta
    assert n == 1
    rad = conn.execute("SELECT datamangd_id FROM distribution WHERE id=?", (DIST_URI,)).fetchone()
    assert rad[0] == RESURS_URI


def test_behandla_distribution_idempotent(tmp_path):
    conn = oppna_db(tmp_path / "test.sqlite")
    barn = _dist_barn()
    behandla_distribution(conn, barn)
    n = behandla_distribution(conn, barn)
    assert n == 0
    assert conn.execute("SELECT COUNT(*) FROM distribution").fetchone()[0] == 1


# ---------------------------------------------------------------------------
# DB-schema — grundläggande
# ---------------------------------------------------------------------------

def test_schema_skapar_alla_tabeller(tmp_path):
    conn = oppna_db(tmp_path / "schema_test.sqlite")
    tabeller = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'shadow')"
        )
    }
    for t in ("datamangd", "distribution", "_dist_dataset_link", "embedding"):
        assert t in tabeller, f"Tabell {t!r} saknas"


def test_schema_idempotent(tmp_path):
    """oppna_db ska kunna köras på en redan existerande databas utan fel."""
    db_path = tmp_path / "idempotent.sqlite"
    oppna_db(db_path)
    oppna_db(db_path)  # andra gången — CREATE IF NOT EXISTS
