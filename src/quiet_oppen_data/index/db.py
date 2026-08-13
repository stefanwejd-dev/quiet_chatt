"""Databasuppstartning för katalogindexet.

Schema per ARKITEKTUR.md §3.2 plus interna hjälptabeller för ingest.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- Datamängder (DCAT Dataset)
CREATE TABLE IF NOT EXISTS datamangd (
    id           TEXT PRIMARY KEY,   -- DCAT-resursens URI
    titel        TEXT,
    beskrivning  TEXT,
    utgivare     TEXT,
    licens       TEXT,
    tema         TEXT,               -- pipe-separerade tema-URI:er
    nyckelord    TEXT,               -- space-separerade nyckelord
    manniskolank TEXT                -- https://www.dataportal.se/datasets/{ctx}_{id}
);

-- Distributioner (DCAT Distribution)
CREATE TABLE IF NOT EXISTS distribution (
    id             TEXT PRIMARY KEY, -- DCAT-resursens URI
    datamangd_id   TEXT,             -- FK → datamangd.id  (NULL om ej länkad)
    format         TEXT,
    access_url     TEXT,
    access_service TEXT,
    FOREIGN KEY (datamangd_id) REFERENCES datamangd(id) ON DELETE CASCADE
);

-- Intern hjälptabell: distribution-URI → dataset-URI (byggs under pass 1)
CREATE TABLE IF NOT EXISTS _dist_dataset_link (
    dist_uri   TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL
);

-- Metadatatabell för att spara modellversion
CREATE TABLE IF NOT EXISTS _index_meta (
    nyckel TEXT PRIMARY KEY,
    varde  TEXT
);

-- Fristående FTS5-index (titel + beskrivning + nyckelord)
-- unicode61 + prefix=2,3 ger delordssökning: "moms" matchar "Momsstatistik"
CREATE VIRTUAL TABLE IF NOT EXISTS datamangd_fts USING fts5(
    id UNINDEXED,
    titel,
    beskrivning,
    nyckelord,
    tokenize = "unicode61 remove_diacritics 1",
    prefix = '2 3'
);

-- Embedding-tabell för steg 3 (vektorsökning)
CREATE TABLE IF NOT EXISTS embedding (
    datamangd_id TEXT PRIMARY KEY,
    vektor       BLOB,
    FOREIGN KEY (datamangd_id) REFERENCES datamangd(id) ON DELETE CASCADE
);
"""


def oppna_db(sokväg: Path) -> sqlite3.Connection:
    """Öppnar (eller skapar) indexdatabasen och applicerar schemat.

    Skapar föräldrakatalogen om den saknas.
    """
    sokväg.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(sokväg)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn
