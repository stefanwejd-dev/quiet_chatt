"""Kvothantering — per IP och totalt per dygn. Se ARKITEKTUR.md §6 och
config.toml `[kvot]`.

Fail-closed: en fråga räknas bara mot kvoten OM den släpps igenom — annars
skulle ett avvisat anrop ändå äta av gränsen. Databasen är sanningen och
delas mellan processer (SQLite-fil), så kvoten håller även om API:et körs
med flera arbetare.

Dygnet räknas i UTC — en enda global brytpunkt, oberoende av var en
besökare eller servern råkar stå i sin lokala tid.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from quiet_oppen_data.konfig import las as las_konfig

_lock = threading.Lock()

# Nyckel som samlar den totala dygnsräkningen, skild från alla riktiga IP:n
# (ingen giltig IP-adress kan kollidera med den).
_TOTAL_NYCKEL = "__total__"


def _db_path() -> Path:
    konfig = las_konfig()
    p = Path(konfig.index.db).parent / "kvoter.sqlite"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _koppla() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS kvot_raknare (
            dag TEXT NOT NULL,
            ip TEXT NOT NULL,
            antal INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (dag, ip)
        )
        """
    )
    conn.commit()
    return conn


def _dagens_datum() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


@dataclass(frozen=True)
class KvotBeslut:
    """Resultatet av en kvotkontroll."""
    tillaten: bool
    meddelande: str | None = None


def kontrollera_och_rakna(ip: str) -> KvotBeslut:
    """Kontrollerar per-IP- och totalkvoten och räknar upp atomiskt om tillåtet.

    Kontroll och uppräkning sker under samma lås så att två samtidiga anrop
    inte båda kan smita igenom precis vid gränsen.
    """
    konfig = las_konfig().kvot
    dag = _dagens_datum()

    with _lock:
        conn = _koppla()
        try:
            ip_rad = conn.execute(
                "SELECT antal FROM kvot_raknare WHERE dag = ? AND ip = ?", (dag, ip)
            ).fetchone()
            ip_antal = ip_rad[0] if ip_rad else 0

            total_rad = conn.execute(
                "SELECT antal FROM kvot_raknare WHERE dag = ? AND ip = ?",
                (dag, _TOTAL_NYCKEL),
            ).fetchone()
            total_antal = total_rad[0] if total_rad else 0

            if ip_antal >= konfig.fragor_per_ip_per_dygn:
                return KvotBeslut(
                    tillaten=False,
                    meddelande=(
                        f"Du har nått dagens gräns på {konfig.fragor_per_ip_per_dygn} "
                        "frågor. Försök igen imorgon."
                    ),
                )
            if total_antal >= konfig.fragor_totalt_per_dygn:
                return KvotBeslut(
                    tillaten=False,
                    meddelande=(
                        "Tjänsten har nått sin dagliga gräns för antal frågor. "
                        "Försök igen imorgon."
                    ),
                )

            for nyckel in (ip, _TOTAL_NYCKEL):
                conn.execute(
                    "INSERT INTO kvot_raknare (dag, ip, antal) VALUES (?, ?, 1) "
                    "ON CONFLICT(dag, ip) DO UPDATE SET antal = antal + 1",
                    (dag, nyckel),
                )
            conn.commit()
            return KvotBeslut(tillaten=True)
        finally:
            conn.close()
