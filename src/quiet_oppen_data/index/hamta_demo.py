"""Hämtar förbyggt demoindex från GitHub Release.

Valfritt och synligt hjälpverktyg (Steg 10).
"""
from __future__ import annotations

import hashlib
import logging
import sys
import tomllib
from pathlib import Path
import httpx

logger = logging.getLogger(__name__)

DEMO_INDEX_VERSION = "v0.1.0"
DEMO_INDEX_FILNAMN = f"quiet_demo_index_{DEMO_INDEX_VERSION}.sqlite"
DEMO_INDEX_URL = f"https://github.com/stefanwejd-dev/quiet_chatt/releases/download/{DEMO_INDEX_VERSION}/{DEMO_INDEX_FILNAMN}"
FORVANTAD_SHA256 = "2fd55b668f43d8dd4d7d7e75b8cdbfe919d8462937123df84db33730da7c4aec"


def hamta_demo_index(mal_sokvag: Path | None = None) -> bool:
    """Laddar ner och verifierar det förbyggda demoindexet."""
    from quiet_oppen_data.konfig import _KONFIG_FIL
    if mal_sokvag is None:
        with open(_KONFIG_FIL, "rb") as f:
            konfig_data = tomllib.load(f)
        mal_sokvag = Path(konfig_data["index"]["db"])

    mal_sokvag.parent.mkdir(parents=True, exist_ok=True)
    temp_fil = mal_sokvag.with_suffix(".download.tmp")

    print(f"Hämtar förbyggt demoindex från:\n  {DEMO_INDEX_URL}\nTill:\n  {mal_sokvag}")
    try:
        with httpx.stream("GET", DEMO_INDEX_URL, follow_redirects=True, timeout=60.0) as resp:
            if resp.status_code != 200:
                print(f"\nFEL: Kunde inte hämta releasefilen (HTTP {resp.status_code}).")
                print("Du kan bygga demoindexet lokalt med:")
                print("  python -m quiet_oppen_data.index.ingest --demo")
                print("  python -m quiet_oppen_data.index.lag_ingest --demo")
                return False

            hasher = hashlib.sha256()
            with open(temp_fil, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=65536):
                    f.write(chunk)
                    hasher.update(chunk)

        beraknad_sha = hasher.hexdigest()
        if FORVANTAD_SHA256 and beraknad_sha != FORVANTAD_SHA256:
            print(f"\nFEL: Kontrollsumma matchar inte!\nVäntad:  {FORVANTAD_SHA256}\nBeräknad: {beraknad_sha}")
            temp_fil.unlink(missing_ok=True)
            return False

        temp_fil.replace(mal_sokvag)
        print(f"\nKlart! Demoindex installerat ({mal_sokvag.stat().st_size / (1024*1024):.1f} MB, SHA-256 verifierad).")
        return True
    except Exception as e:
        print(f"\nFEL vid nedladdning: {e}")
        print("Du kan bygga demoindexet manuellt:")
        print("  python -m quiet_oppen_data.index.ingest --demo")
        print("  python -m quiet_oppen_data.index.lag_ingest --demo")
        temp_fil.unlink(missing_ok=True)
        return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    lyckades = hamta_demo_index()
    if not lyckades:
        sys.exit(1)
