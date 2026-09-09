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

-- ===========================================================================
-- Lagindex (steg 16)
-- ===========================================================================

-- Huvudpost per SFS-författning
CREATE TABLE IF NOT EXISTS lag_dokument (
    sfs           TEXT PRIMARY KEY,   -- "1999:1229"
    dok_id        TEXT NOT NULL,      -- "sfs-1999-1229"
    namn          TEXT NOT NULL,      -- "Inkomstskattelag"
    kortnamn      TEXT NOT NULL,      -- "IL"
    tom_sfs       TEXT,               -- "t.o.m. SFS 2026:1393"
    systemdatum   TEXT,               -- "2026-07-10 04:42:25"
    hamtad        TEXT NOT NULL,      -- ISO-timestamp UTC när kopian togs
    lank_manniska TEXT NOT NULL,      -- https://www.riksdagen.se/...
    lank_maskin   TEXT NOT NULL,      -- https://data.riksdagen.se/...
    ratext        TEXT                -- Råtexten sparad lokalt
);

-- Lagparagrafer / sökbara chunks
CREATE TABLE IF NOT EXISTS lag_chunk (
    id             TEXT PRIMARY KEY,  -- "1999:1229#3:9"
    sfs            TEXT NOT NULL,     -- FK → lag_dokument.sfs
    dok_id         TEXT NOT NULL,
    kapitel_nr     TEXT,              -- "3" (eller NULL om lagen saknar kapitel)
    kapitel_rubrik TEXT,              -- "Fysiska personer"
    paragraf_nr    TEXT NOT NULL,     -- "9"
    paragraf_rubrik TEXT,             -- "Undantag från skattskyldighet vid vistelse utomlands"
    paragraf_text  TEXT NOT NULL,     -- Själva lagtexten
    andringsnotis  TEXT,              -- "Lag (2019:907)"
    full_text      TEXT NOT NULL,     -- Kapitel + rubrik + text + ändring för sökning
    FOREIGN KEY (sfs) REFERENCES lag_dokument(sfs) ON DELETE CASCADE
);

-- FTS5-index för lag_chunk
CREATE VIRTUAL TABLE IF NOT EXISTS lag_chunk_fts USING fts5(
    id UNINDEXED,
    sfs UNINDEXED,
    kapitel_rubrik,
    paragraf_rubrik,
    paragraf_text,
    full_text,
    tokenize = "unicode61 remove_diacritics 1",
    prefix = '2 3'
);

-- Embedding-tabell för lag_chunk
CREATE TABLE IF NOT EXISTS lag_embedding (
    chunk_id TEXT PRIMARY KEY,
    vektor   BLOB,
    FOREIGN KEY (chunk_id) REFERENCES lag_chunk(id) ON DELETE CASCADE
);

-- ===========================================================================
-- Textkorpus (steg 23–24): BFN och EUR-Lex
-- ===========================================================================
--
-- Lagindexet ovan har egna tabeller därför att SFS har egna begrepp — kapitel,
-- paragraf, ändringsnotis, konsolideringspunkt. BFN:s allmänna råd och EU:s
-- rättsakter delar däremot form: ett dokument med källuppgifter, och under det
-- numrerade textstycken med rubrik. Två nästan identiska tabellpar hade blivit
-- två nästan identiska sökfunktioner, och en rättelse i den ena hade tyst
-- lämnat den andra kvar. Kolumnen `korpus` skiljer dem åt i stället.

CREATE TABLE IF NOT EXISTS korpus_dokument (
    id            TEXT PRIMARY KEY,   -- "bfn:vl12-1-k3-kons20251215", "eu:32006L0112"
    korpus        TEXT NOT NULL,      -- "bfn" | "eu"
    dok_id        TEXT NOT NULL,      -- dokumentets id inom sin korpus
    titel         TEXT NOT NULL,
    kortnamn      TEXT,               -- "BFNAR 2012:1" | "Momsdirektivet"
    utgivare      TEXT NOT NULL,      -- "Bokföringsnämnden (BFN)"
    -- Vilken lydelse kopian avser. BFN: titelsidans "Uppdaterad ÅÅÅÅ-MM-DD".
    -- EU: konsolideringsdatum. Tom sträng när källan inte anger någon — den
    -- gissas aldrig fram (jfr regelverk/KONTRAKT.md §4).
    lydelse       TEXT,
    -- Färskhetsnyckel för den nattliga kontrollen: BFN sha256 över pdf:en,
    -- EU rättsaktens ETag/Last-Modified om den ges, annars tom.
    version       TEXT,
    hamtad        TEXT NOT NULL,      -- när kopian togs, inte när frågan ställdes
    lank_manniska TEXT NOT NULL,
    lank_maskin   TEXT NOT NULL,
    licens        TEXT,
    attribution   TEXT,
    -- Icke-tomt när dokumentet hämtades men inte kunde läsas (t.ex. pdf utan
    -- textlager). Posten finns kvar med sin anmärkning i stället för att
    -- försvinna tyst — se ARKITEKTUR §5 regel 8 och /matning.
    anmarkning    TEXT
);

CREATE TABLE IF NOT EXISTS korpus_chunk (
    id             TEXT PRIMARY KEY,  -- "bfn:vl12-1-k3-kons20251215#12.7"
    korpus         TEXT NOT NULL,
    dokument_id    TEXT NOT NULL,     -- FK → korpus_dokument.id
    -- BFN: allmant_rad | kommentar | lagtext | exempel | brodtext
    -- EU:  artikel | skalen | bilaga
    -- Att hålla dem isär är hela poängen: ett allmänt råd är bindande,
    -- BFN:s kommentar till det är inte det.
    blocktyp       TEXT NOT NULL,
    beteckning     TEXT,              -- "12.7" | "Artikel 168"
    kapitel_nr     TEXT,
    kapitel_rubrik TEXT,
    avsnitt        TEXT,
    text           TEXT NOT NULL,
    sida           INTEGER,           -- pdf-sida (BFN), NULL för EU
    full_text      TEXT NOT NULL,     -- sammansatt sökyta
    FOREIGN KEY (dokument_id) REFERENCES korpus_dokument(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_korpus_chunk_dok ON korpus_chunk(dokument_id);
CREATE INDEX IF NOT EXISTS idx_korpus_chunk_korpus ON korpus_chunk(korpus);

CREATE VIRTUAL TABLE IF NOT EXISTS korpus_chunk_fts USING fts5(
    id UNINDEXED,
    korpus UNINDEXED,
    beteckning,
    kapitel_rubrik,
    avsnitt,
    text,
    full_text,
    tokenize = "unicode61 remove_diacritics 1",
    prefix = '2 3'
);

CREATE TABLE IF NOT EXISTS korpus_embedding (
    chunk_id TEXT PRIMARY KEY,
    vektor   BLOB,
    FOREIGN KEY (chunk_id) REFERENCES korpus_chunk(id) ON DELETE CASCADE
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


def satt_meta(conn: sqlite3.Connection, nyckel: str, varde: str) -> None:
    """Sätter ett nyckel-värde-par i _index_meta."""
    conn.execute("INSERT OR REPLACE INTO _index_meta (nyckel, varde) VALUES (?, ?)", (nyckel, varde))
    conn.commit()


def hamta_meta(conn: sqlite3.Connection, nyckel: str) -> str | None:
    """Läser ett metadatavärde ur _index_meta."""
    cur = conn.execute("SELECT varde FROM _index_meta WHERE nyckel = ?", (nyckel,))
    row = cur.fetchone()
    return row[0] if row else None


def ar_demo_index(conn: sqlite3.Connection) -> bool:
    """Returnerar True om indexet är märkt som demoindex."""
    varde = hamta_meta(conn, "demo_index")
    return varde in ("1", "true", "True", "ja")

