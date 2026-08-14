"""Nattlig ingest-wrapper — steg 15.

Kör katalogingesten och loggar deltat mot föregående körning.

Körning:
    python -m quiet_oppen_data.index.nattlig_ingest
    python -m quiet_oppen_data.index.nattlig_ingest --db data/index.sqlite

Schemaläggs typiskt med cron eller systemd.timer:
    0 3 * * * cd /app && python -m quiet_oppen_data.index.nattlig_ingest >> logs/ingest.log 2>&1

Rapporten skrivs till stdout (strukturerad) och loggas via matning.logga_ingest.
Frågetexter som är äldre än 30 dagar rensas i samma körning (ARKITEKTUR.md §11).

Sedan steg 19 körs även en lagkorpus-färskhetskontroll i samma körning
(ARKITEKTUR.md §5 regel 8): dokumenthuvudena för alla författningar i
`lagar/lagregister.yaml` jämförs mot Riksdagens systemdatum, och bara de som
ändrats ingesteras om. Se `kör_nattlig_lagkontroll` nedan.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def _räkna_rader(db_sökväg: Path) -> tuple[int, int]:
    """Returnerar (antal datamängder, antal distributioner) i databasen."""
    try:
        with sqlite3.connect(str(db_sökväg), timeout=10) as kon:
            dm = kon.execute("SELECT COUNT(*) FROM datamangd").fetchone()[0]
            dist = kon.execute("SELECT COUNT(*) FROM distribution").fetchone()[0]
            return dm, dist
    except Exception:
        return 0, 0


def kör_nattlig_lagkontroll(db_sökväg: Path | None = None) -> dict:
    """Kör lagkorpus-färskhetskontrollen (steg 19) och loggar resultatet.

    Egen funktion, egen felhantering — ett fel här får aldrig hindra
    katalogingesten eller frågeradering i `kör_nattlig_ingest` (§11:
    bevarandeplikten går alltid före statistik).
    """
    from quiet_oppen_data import matning
    from quiet_oppen_data.index import lag_ingest
    from quiet_oppen_data.index.db import oppna_db

    if db_sökväg is None:
        try:
            from quiet_oppen_data.konfig import las as las_konfig
            db_sökväg = Path(las_konfig().index.db)
        except Exception:
            db_sökväg = Path("data/index.sqlite")

    conn = oppna_db(db_sökväg)
    try:
        resultat = lag_ingest.nattlig_lagkontroll(db_conn=conn)
    finally:
        conn.close()

    try:
        matning.logga_lagkontroll(
            kontrollerade=resultat["kontrollerade"],
            andrade=resultat["andrade"],
            omingesterade=resultat["omingesterade"],
            ingest_fel=resultat["ingest_fel"],
            fel_vid_kontroll=resultat["fel_vid_kontroll"],
            status=resultat["status"],
            varaktighet_sek=resultat["varaktighet_sek"],
        )
    except Exception:
        logger.warning("Kunde inte logga lagkontroll-statistik", exc_info=True)

    print(f"  Lagkorpus kontrollerad:{resultat['kontrollerade']:>7}  "
          f"ändrade {resultat['andrade']}  omingesterade {resultat['omingesterade']}  "
          f"ingest-fel {resultat['ingest_fel']}  kontroll-fel {resultat['fel_vid_kontroll']}  "
          f"status={resultat['status']}")

    return resultat


def kör_nattlig_ingest(db_sökväg: Path | None = None) -> dict:
    """Kör ingesten, beräknar deltat mot föregående körning och loggar resultatet.

    Args:
        db_sökväg: sökväg till index.sqlite. None = läs ur config.toml.

    Returns:
        Dict med deltaresultat (samma struktur som skrivs till stdout).
    """
    from quiet_oppen_data import matning
    from quiet_oppen_data.index.ingest import main as ingest_main

    # Lös upp sökvägen ur config om ingen gavs
    if db_sökväg is None:
        try:
            from quiet_oppen_data.konfig import las as las_konfig
            db_sökväg = Path(las_konfig().index.db)
        except Exception:
            db_sökväg = Path("data/index.sqlite")

    # Räkna raderna FÖRE ingesten
    dm_fore, dist_fore = _räkna_rader(db_sökväg)

    logger.info(
        "Nattlig ingest startar. DB: %s, före: %d datamängder, %d distributioner",
        db_sökväg, dm_fore, dist_fore,
    )

    start = time.monotonic()
    ingest_fel = 0

    try:
        ingest_main(db_sokväg=db_sökväg)
    except SystemExit as e:
        if e.code not in (None, 0):
            ingest_fel = 1
            logger.warning("Ingesten avslutades med exitkod %s", e.code)
    except Exception:
        ingest_fel = 1
        logger.warning("Ingesten kastade undantag", exc_info=True)

    varaktighet = time.monotonic() - start

    # Räkna EFTER
    dm_efter, dist_efter = _räkna_rader(db_sökväg)
    dm_delta = dm_efter - dm_fore
    dist_delta = dist_efter - dist_fore

    resultat = {
        "db": str(db_sökväg),
        "datamangder_totalt": dm_efter,
        "datamangder_nya": max(dm_delta, 0),
        # INSERT OR IGNORE räknar inte uppdateringar av befintliga rader
        "datamangder_uppdaterade": 0,
        "distributioner_totalt": dist_efter,
        "fel": ingest_fel,
        "varaktighet_sek": round(varaktighet, 1),
    }

    # Rensa gamla frågetexter FÖRST (§11: raderas efter 30 dagar).
    #
    # Ordningen är avsiktlig. Raderingen är en bevarandeplikt; ingest-statistiken
    # är trevlig att ha. Låg raderingen efter loggningen skulle ett undantag i
    # logga_ingest tyst hoppa över den, och frågetexter behållas i månader utan
    # att någon märkte det. Egen try/except av samma skäl.
    rensade = 0
    try:
        rensade = matning.rensa_gamla_fragor(dagar=30)
        if rensade:
            logger.info("Rensade %d gamla frågetexter (>30 dagar)", rensade)
    except Exception:
        logger.warning("Kunde inte rensa gamla frågetexter", exc_info=True)

    # Logga i mätningsdatabasen
    try:
        matning.logga_ingest(**{k: v for k, v in resultat.items() if k != "db"})
    except Exception:
        logger.warning("Kunde inte logga ingest-statistik", exc_info=True)

    # Lagkorpus-färskhetskontroll (steg 19). Eget fel-omfång: ett fel här
    # loggas och syns i resultatet, men avbryter aldrig den redan slutförda
    # katalogingesten eller frågeraderingen ovan.
    try:
        resultat["lag_kontroll"] = kör_nattlig_lagkontroll(db_sökväg=db_sökväg)
    except Exception:
        logger.error("Nattlig lagkontroll misslyckades helt", exc_info=True)
        resultat["lag_kontroll"] = {"status": "fel"}

    # Skriv deltarapport till stdout
    print("=" * 60)
    print(f"Nattlig ingest — {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    print(f"  DB:                    {db_sökväg}")
    print(f"  Datamängder totalt:    {dm_efter:>7}  (delta {dm_delta:+d})")
    print(f"  Distributioner totalt: {dist_efter:>7}  (delta {dist_delta:+d})")
    print(f"  Fel under ingest:      {ingest_fel}")
    print(f"  Varaktighet:           {varaktighet:.1f} s")
    print(f"  Frågetexter rensade:   {rensade}")
    print("=" * 60)

    logger.info("Nattlig ingest klar: %s", resultat)
    return resultat


# ---------------------------------------------------------------------------
# CLI-entry
# ---------------------------------------------------------------------------

def _cli() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )

    parser = argparse.ArgumentParser(description="Nattlig katalogingest med deltarapport.")
    parser.add_argument("--db", type=Path, default=None, help="Sökväg till index.sqlite")
    args = parser.parse_args()

    kör_nattlig_ingest(db_sökväg=args.db)


if __name__ == "__main__":
    _cli()
