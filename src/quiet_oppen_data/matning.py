"""Mätning och loggning — steg 15.

Loggar per fråga enligt ARKITEKTUR.md §11:
  * vilka källor som anropades och om de svarade
  * om fas C validerade i första försöket, andra, eller föll igenom
  * antal Faktaposter per svar
  * cache-träffkvot per källa  (läses ur transport.halsostatistik, inte loggat per fråga)
  * token in/ut per fas

Frågetexter lagras i kolumnen `fraga_text`, men raderas automatiskt (sätts till NULL)
efter 30 dagar. Övriga fält finns kvar för statistikändamål.

Den viktigaste aggregerade siffran är andelen frågor som besvaras på nivå 3
(dataportal-katalogsvar, alltså `kalla_id = 'dataportal'` i källlistan). Det är
den siffran som styr vilken adapter som ska byggas härnäst.

Databas: `data/matning.sqlite` (separat från cache och kvoter).

Trådsäkerhet: varje operation öppnar och stänger en anslutning explicit —
WAL-läge gör läs-/skrivsamtidighet möjlig utan global lås.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Databassökväg och initialisering
# ---------------------------------------------------------------------------

_DB_RELATIV = Path("data/matning.sqlite")


def _db_sökväg() -> Path:
    """Returnerar absolut sökväg till mätningsdatabasen.

    Söker uppåt från denna fil tills repots rot hittas (pyproject.toml).
    Fallback: relativ från CWD.
    """
    här = Path(__file__).resolve()
    for förälder in här.parents:
        if (förälder / "pyproject.toml").exists():
            return förälder / _DB_RELATIV
    return _DB_RELATIV


def _anslut() -> sqlite3.Connection:
    db = _db_sökväg()
    db.parent.mkdir(parents=True, exist_ok=True)
    kon = sqlite3.connect(str(db), timeout=10)
    kon.execute("PRAGMA journal_mode=WAL")
    kon.execute("PRAGMA foreign_keys=ON")
    return kon


def _initiera(kon: sqlite3.Connection) -> None:
    """Skapar tabeller om de inte finns. Idempotent."""
    kon.executescript("""
        CREATE TABLE IF NOT EXISTS fraga_logg (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            tidpunkt        TEXT    NOT NULL,   -- ISO-8601 UTC
            fraga_text      TEXT,               -- raderas efter 30 dagar (§11)
            kan_besvaras    INTEGER NOT NULL,   -- 0/1
            fas_c_forsok    INTEGER NOT NULL,   -- 1=första ok, 2=omförsök ok, 0=fail-closed
            antal_faktaposter INTEGER NOT NULL,
            niva3_svar      INTEGER NOT NULL,   -- 1 om minst en källa är 'dataportal'
            token_fas_a_in  INTEGER NOT NULL DEFAULT 0,
            token_fas_a_ut  INTEGER NOT NULL DEFAULT 0,
            token_fas_a_cache_read  INTEGER NOT NULL DEFAULT 0,
            token_fas_a_cache_write INTEGER NOT NULL DEFAULT 0,
            token_fas_b_in  INTEGER NOT NULL DEFAULT 0,
            token_fas_b_ut  INTEGER NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_fraga_logg_tidpunkt
            ON fraga_logg(tidpunkt);

        CREATE TABLE IF NOT EXISTS kalla_logg (
            fraga_id    INTEGER NOT NULL REFERENCES fraga_logg(id) ON DELETE CASCADE,
            kalla_id    TEXT    NOT NULL,
            svarade     INTEGER NOT NULL  -- 1=hämtade minst en Faktapost, 0=inga
        );

        CREATE INDEX IF NOT EXISTS idx_kalla_logg_fraga
            ON kalla_logg(fraga_id);
        CREATE INDEX IF NOT EXISTS idx_kalla_logg_kalla
            ON kalla_logg(kalla_id);
    """)
    kon.commit()


def _säkerställ_schema() -> None:
    """Öppnar, initierar och stänger — anropas en gång vid modulimport."""
    try:
        with _anslut() as kon:
            _initiera(kon)
    except Exception:
        logger.warning("Mätning: kunde inte initialisera databasen", exc_info=True)


_säkerställ_schema()


# ---------------------------------------------------------------------------
# Loggning av en enskild fråga
# ---------------------------------------------------------------------------

def logga_fraga(
    *,
    fraga_text: str,
    kan_besvaras: bool,
    fas_c_forsok: int,                  # 1, 2, eller 0 (fail-closed)
    antal_faktaposter: int,
    anvanda_kallor: list[str],          # kalla_id:n för poster som registrerades
    token_fas_a_in: int = 0,
    token_fas_a_ut: int = 0,
    token_fas_a_cache_read: int = 0,
    token_fas_a_cache_write: int = 0,
    token_fas_b_in: int = 0,
    token_fas_b_ut: int = 0,
) -> None:
    """Loggar en slutförd fråga.

    Anropas av api.py efter att hela A→B→C-kedjan körts klart.
    Trådsäker: anslutningen öppnas och stängs i funktionen.
    """
    niva3 = 1 if any(k == "dataportal" for k in anvanda_kallor) else 0
    tidpunkt = datetime.now(UTC).isoformat()

    try:
        with _anslut() as kon:
            _initiera(kon)
            cur = kon.execute(
                """INSERT INTO fraga_logg (
                    tidpunkt, fraga_text, kan_besvaras, fas_c_forsok,
                    antal_faktaposter, niva3_svar,
                    token_fas_a_in, token_fas_a_ut,
                    token_fas_a_cache_read, token_fas_a_cache_write,
                    token_fas_b_in, token_fas_b_ut
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    tidpunkt,
                    fraga_text[:1000],   # Begränsa lagring; raderas ändå efter 30 d
                    int(kan_besvaras),
                    fas_c_forsok,
                    antal_faktaposter,
                    niva3,
                    token_fas_a_in,
                    token_fas_a_ut,
                    token_fas_a_cache_read,
                    token_fas_a_cache_write,
                    token_fas_b_in,
                    token_fas_b_ut,
                ),
            )
            fraga_id = cur.lastrowid

            # Logga vilka källor som svarade
            unika_kallor = set(anvanda_kallor)
            if unika_kallor:
                kon.executemany(
                    "INSERT INTO kalla_logg (fraga_id, kalla_id, svarade) VALUES (?,?,?)",
                    [(fraga_id, k, 1) for k in unika_kallor],
                )
            kon.commit()

    except Exception:
        logger.warning("Mätning: kunde inte logga fråga", exc_info=True)
        # Loggningsfel är icke-fatala — svaret ska inte blockeras


# ---------------------------------------------------------------------------
# Radering av gamla frågetexter (§11: frågetexter raderas efter 30 dagar)
# ---------------------------------------------------------------------------

def rensa_gamla_fragor(dagar: int = 30) -> int:
    """Sätter fraga_text till NULL för rader äldre än `dagar` dagar.

    Returnerar antal rensade rader. Körs av nattlig_ingest vid varje körning.
    """
    gräns = (datetime.now(UTC) - timedelta(days=dagar)).isoformat()
    try:
        with _anslut() as kon:
            _initiera(kon)
            cur = kon.execute(
                "UPDATE fraga_logg SET fraga_text = NULL WHERE tidpunkt < ? AND fraga_text IS NOT NULL",
                (gräns,),
            )
            kon.commit()
            return cur.rowcount
    except Exception:
        logger.warning("Mätning: kunde inte rensa gamla frågetexter", exc_info=True)
        return 0


# ---------------------------------------------------------------------------
# Aggregerade mätpunkter — underlag för GET /matning
# ---------------------------------------------------------------------------

def las_matpunkter(dygn: int = 30) -> dict:
    """Returnerar aggregerade mätpunkter för de senaste `dygn` dagarna.

    Fält som returneras:
      * period_dagar: dygn som ingår i statistiken
      * totalt_fragor: antal loggade frågor
      * besvarade: antal frågor med kan_besvaras=1
      * inte_besvarade: antal frågor med kan_besvaras=0
      * niva3_andel: andel (0–1) besvarade med katalogsvar (dataportal)
      * fas_c_fordelning: {1: N, 2: N, 0: N} (försök 1 / omförsök / fail-closed)
      * snitt_faktaposter: genomsnittligt antal Faktaposter per besvarad fråga
      * tokens: medelvärden per fas
      * kallor_topp: de 10 vanligaste källorna (kalla_id, antal_fragor)
    """
    gräns = (datetime.now(UTC) - timedelta(days=dygn)).isoformat()

    try:
        with _anslut() as kon:
            _initiera(kon)

            # Grundaggregat
            rad = kon.execute(
                """SELECT
                    COUNT(*)                                AS totalt,
                    COALESCE(SUM(kan_besvaras), 0)          AS besvarade,
                    COALESCE(SUM(niva3_svar), 0)            AS niva3,
                    COALESCE(AVG(CASE WHEN kan_besvaras=1 THEN antal_faktaposter END), 0) AS snitt_fp,
                    COALESCE(AVG(token_fas_a_in), 0)        AS avg_a_in,
                    COALESCE(AVG(token_fas_a_ut), 0)        AS avg_a_ut,
                    COALESCE(AVG(token_fas_a_cache_read),0) AS avg_a_cr,
                    COALESCE(AVG(token_fas_b_in), 0)        AS avg_b_in,
                    COALESCE(AVG(token_fas_b_ut), 0)        AS avg_b_ut
                FROM fraga_logg WHERE tidpunkt >= ?""",
                (gräns,),
            ).fetchone()

            totalt, besvarade, niva3, snitt_fp, avg_a_in, avg_a_ut, avg_a_cr, avg_b_in, avg_b_ut = rad

            inte_besvarade = totalt - besvarade

            # fas C-fördelning
            fas_c_rader = kon.execute(
                """SELECT fas_c_forsok, COUNT(*) FROM fraga_logg
                   WHERE tidpunkt >= ? GROUP BY fas_c_forsok""",
                (gräns,),
            ).fetchall()
            fas_c_fördeln = {0: 0, 1: 0, 2: 0}
            for forsok, antal in fas_c_rader:
                fas_c_fördeln[forsok] = antal

            # Topp-10 källor
            kallor_topp = kon.execute(
                """SELECT k.kalla_id, COUNT(DISTINCT k.fraga_id) AS n
                   FROM kalla_logg k
                   JOIN fraga_logg f ON f.id = k.fraga_id
                   WHERE f.tidpunkt >= ? AND k.svarade = 1
                   GROUP BY k.kalla_id
                   ORDER BY n DESC LIMIT 10""",
                (gräns,),
            ).fetchall()

            niva3_andel = round(niva3 / besvarade, 4) if besvarade > 0 else None

            return {
                "period_dagar": dygn,
                "totalt_fragor": totalt,
                "besvarade": besvarade,
                "inte_besvarade": inte_besvarade,
                "niva3_andel": niva3_andel,
                "fas_c_fordelning": {
                    "forsok_1_ok": fas_c_fördeln[1],
                    "forsok_2_ok": fas_c_fördeln[2],
                    "fail_closed": fas_c_fördeln[0],
                },
                "snitt_faktaposter_per_svar": round(snitt_fp, 2),
                "tokens": {
                    "fas_a_in_snitt": round(avg_a_in),
                    "fas_a_ut_snitt": round(avg_a_ut),
                    "fas_a_cache_read_snitt": round(avg_a_cr),
                    "fas_b_in_snitt": round(avg_b_in),
                    "fas_b_ut_snitt": round(avg_b_ut),
                },
                "kallor_topp10": [
                    {"kalla_id": k, "antal_fragor": n} for k, n in kallor_topp
                ],
            }

    except Exception:
        logger.warning("Mätning: kunde inte läsa mätpunkter", exc_info=True)
        return {"fel": "Kunde inte läsa mätpunkter."}


# ---------------------------------------------------------------------------
# Ingest-logg — för nattlig_ingest.py:s deltarapport
# ---------------------------------------------------------------------------

def logga_ingest(
    *,
    datamangder_totalt: int,
    datamangder_nya: int,
    datamangder_uppdaterade: int,
    distributioner_totalt: int,
    fel: int,
    varaktighet_sek: float,
    extra: dict | None = None,
) -> None:
    """Loggar en ingest-körning. Skapar tabellen vid behov."""
    try:
        with _anslut() as kon:
            kon.execute("""
                CREATE TABLE IF NOT EXISTS ingest_logg (
                    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                    tidpunkt                TEXT NOT NULL,
                    datamangder_totalt      INTEGER NOT NULL,
                    datamangder_nya         INTEGER NOT NULL,
                    datamangder_uppdaterade INTEGER NOT NULL,
                    distributioner_totalt   INTEGER NOT NULL,
                    fel                     INTEGER NOT NULL,
                    varaktighet_sek         REAL    NOT NULL,
                    extra_json              TEXT
                )
            """)
            kon.execute(
                """INSERT INTO ingest_logg (
                    tidpunkt, datamangder_totalt, datamangder_nya,
                    datamangder_uppdaterade, distributioner_totalt,
                    fel, varaktighet_sek, extra_json
                ) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    datetime.now(UTC).isoformat(),
                    datamangder_totalt,
                    datamangder_nya,
                    datamangder_uppdaterade,
                    distributioner_totalt,
                    fel,
                    round(varaktighet_sek, 1),
                    json.dumps(extra, ensure_ascii=False) if extra else None,
                ),
            )
            kon.commit()
    except Exception:
        logger.warning("Mätning: kunde inte logga ingest", exc_info=True)


def las_senaste_ingest() -> dict | None:
    """Returnerar den senaste ingest-loggraden, eller None om tabellen är tom."""
    try:
        with _anslut() as kon:
            kon.execute("""
                CREATE TABLE IF NOT EXISTS ingest_logg (
                    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                    tidpunkt                TEXT NOT NULL,
                    datamangder_totalt      INTEGER NOT NULL,
                    datamangder_nya         INTEGER NOT NULL,
                    datamangder_uppdaterade INTEGER NOT NULL,
                    distributioner_totalt   INTEGER NOT NULL,
                    fel                     INTEGER NOT NULL,
                    varaktighet_sek         REAL    NOT NULL,
                    extra_json              TEXT
                )
            """)
            rad = kon.execute(
                """SELECT tidpunkt, datamangder_totalt, datamangder_nya,
                          datamangder_uppdaterade, distributioner_totalt,
                          fel, varaktighet_sek
                   FROM ingest_logg ORDER BY id DESC LIMIT 1"""
            ).fetchone()
            if rad is None:
                return None
            return {
                "tidpunkt": rad[0],
                "datamangder_totalt": rad[1],
                "datamangder_nya": rad[2],
                "datamangder_uppdaterade": rad[3],
                "distributioner_totalt": rad[4],
                "fel": rad[5],
                "varaktighet_sek": rad[6],
            }
    except Exception:
        logger.warning("Mätning: kunde inte läsa senaste ingest", exc_info=True)
        return None
