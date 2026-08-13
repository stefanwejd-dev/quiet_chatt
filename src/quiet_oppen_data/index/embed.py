"""Genererar embeddings för alla datamängder i indexet.

Använder sentence-transformers. Bygger endast poster som saknas i embedding-tabellen.
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import struct
import sys
import tomllib
from pathlib import Path

from quiet_oppen_data.index.db import oppna_db

logger = logging.getLogger(__name__)


def init_db(db_path: Path) -> sqlite3.Connection:
    conn = oppna_db(db_path)
    return conn


def spara_meta(conn: sqlite3.Connection, modell_namn: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO _index_meta (nyckel, varde) VALUES ('embedding_modell', ?)",
        (modell_namn,)
    )
    conn.commit()


def ladda_meta(conn: sqlite3.Connection) -> str | None:
    rad = conn.execute("SELECT varde FROM _index_meta WHERE nyckel = 'embedding_modell'").fetchone()
    return rad[0] if rad else None


def generera_embeddings(db_path: Path, modell_namn: str, batch_size: int = 64) -> None:
    from sentence_transformers import SentenceTransformer

    logger.info("Laddar modell %s...", modell_namn)
    model = SentenceTransformer(modell_namn)
    
    conn = init_db(db_path)
    
    # Kolla om vi bytt modell
    gammal_modell = ladda_meta(conn)
    if gammal_modell and gammal_modell != modell_namn:
        logger.warning("Modell har bytts från %s till %s. Tömmer embedding-tabellen.", gammal_modell, modell_namn)
        conn.execute("DELETE FROM embedding")
        conn.commit()
    
    spara_meta(conn, modell_namn)

    # Hämta rader som saknar embedding
    cur = conn.execute(
        """SELECT d.id, d.titel, d.beskrivning 
           FROM datamangd d 
           LEFT JOIN embedding e ON d.id = e.datamangd_id 
           WHERE e.datamangd_id IS NULL"""
    )
    rader = cur.fetchall()
    
    if not rader:
        logger.info("Alla %d datamängder har redan embeddings.", conn.execute("SELECT COUNT(*) FROM datamangd").fetchone()[0])
        return

    logger.info("Ska generera embeddings för %d poster.", len(rader))

    for i in range(0, len(rader), batch_size):
        batch = rader[i : i + batch_size]
        texter = []
        ids = []
        for d_id, titel, beskrivning in batch:
            t = (titel or "").strip()
            b = (beskrivning or "").strip()
            text = f"{t}. {b}" if b else t
            texter.append(text)
            ids.append(d_id)
            
        embeddings = model.encode(texter, convert_to_numpy=True)
        
        db_batch = []
        dim = embeddings.shape[1]
        for d_id, emb in zip(ids, embeddings):
            vektor_bytes = struct.pack(f"{dim}f", *emb.tolist())
            db_batch.append((d_id, vektor_bytes))
            
        conn.executemany(
            "INSERT INTO embedding (datamangd_id, vektor) VALUES (?, ?)",
            db_batch
        )
        conn.commit()
        
        logger.info("Bearbetat %d / %d...", min(i + batch_size, len(rader)), len(rader))

    logger.info("Klart! Genererat %d embeddings.", len(rader))
    conn.close()


def main() -> None:
    from quiet_oppen_data.konfig import _KONFIG_FIL
    with open(_KONFIG_FIL, "rb") as f:
        data = tomllib.load(f)
    
    db_sokvag = Path(data["index"]["db"])
    modell_namn = data["index"]["embedding_modell"]
    
    generera_embeddings(db_sokvag, modell_namn)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    main()
