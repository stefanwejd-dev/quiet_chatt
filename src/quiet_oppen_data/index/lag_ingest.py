"""Ingest-modul för lagkorpus (Steg 16).

Hämtar konsoliderad lagtext från Riksdagens öppna data via transportlagret,
extraherar konsolideringspunkt (t.o.m. SFS), systemdatum och hämtningstidpunkt,
parsar till kapitel och paragrafer, och indexerar i SQLite (FTS5 + embeddings).
"""
from __future__ import annotations

import logging
import sqlite3
import struct
import sys
import time
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

from quiet_oppen_data import lagregister
from quiet_oppen_data.adaptrar import transport
from quiet_oppen_data.index.db import oppna_db
from quiet_oppen_data.index.lag_parser import extrahera_tom_sfs, parsa_lagtext
from quiet_oppen_data.konfig import las as las_konfig
from quiet_oppen_data.lagregister import LagPost

logger = logging.getLogger(__name__)



def _generera_vektorer_for_chunks(
    conn: sqlite3.Connection,
    chunk_ids_och_texter: list[tuple[str, str]],
    modell_namn: str,
    dim: int,
    batch_size: int = 64,
) -> None:
    """Genererar embeddings för de angivna lag-chunks och sparar i lag_embedding."""
    if not chunk_ids_och_texter:
        return

    from sentence_transformers import SentenceTransformer

    logger.info("Laddar embedding-modell %s för %d chunks...", modell_namn, len(chunk_ids_och_texter))
    model = SentenceTransformer(modell_namn)

    for i in range(0, len(chunk_ids_och_texter), batch_size):
        batch = chunk_ids_och_texter[i : i + batch_size]
        ids = [c[0] for c in batch]
        texter = [c[1] for c in batch]

        embeddings = model.encode(texter, convert_to_numpy=True)
        db_batch = []
        for c_id, emb in zip(ids, embeddings, strict=True):
            vektor_bytes = struct.pack(f"{dim}f", *emb.tolist())
            db_batch.append((c_id, vektor_bytes))

        conn.executemany(
            "INSERT OR REPLACE INTO lag_embedding (chunk_id, vektor) VALUES (?, ?)",
            db_batch,
        )
        conn.commit()
        logger.info("Vektorisering: %d / %d chunks klara...", min(i + batch_size, len(chunk_ids_och_texter)), len(chunk_ids_och_texter))


def hamta_och_indexera_lag(
    lag: LagPost,
    db_conn: sqlite3.Connection | None = None,
    generera_vektorer: bool = True,
) -> tuple[int, str, str]:
    """Hämtar en lag från Riksdagen, parsar och indexerar i databasen.

    Args:
        lag: LagPost med sfs, namn, kortnamn.
        db_conn: Valfri befintlig SQLite-anslutning.
        generera_vektorer: Om embeddings ska genereras.

    Returns:
        tuple (antal_chunks, tom_sfs, systemdatum)
    """
    konfig = las_konfig()
    conn = db_conn or oppna_db(Path(konfig.index.db))
    stang_efter = db_conn is None

    try:
        # 1. Hämta metadata (systemdatum m.m.) från dokumentstatus
        meta_url = f"https://data.riksdagen.se/dokumentstatus/{lag.dok_id}.json"
        try:
            meta_json = transport.hamta_json("riksdagen", "GET", meta_url)
            systemdatum = (
                meta_json.get("dokumentstatus", {})
                .get("dokument", {})
                .get("systemdatum")
            ) or ""
        except Exception:
            logger.warning("Kunde inte hämta metadata för %s", lag.dok_id, exc_info=True)
            systemdatum = ""

        # 2. Hämta råtext
        text_url = f"https://data.riksdagen.se/dokument/{lag.dok_id}.text"
        raw_text = transport.hamta_text("riksdagen", "GET", text_url)
        if not raw_text:
            raise ValueError(f"Tom text returnerades för {lag.sfs} ({lag.dok_id})")


        # 3. Extrahera konsolideringspunkt
        tom_sfs = extrahera_tom_sfs(raw_text)
        hamtad_tid = datetime.now(UTC).isoformat()

        # 4. Parsa råtext till chunks
        chunks = parsa_lagtext(raw_text, lag.sfs, lag.namn, lag.kortnamn)
        if not chunks:
            raise ValueError(f"Parsningen gav inga chunks för {lag.sfs} ({lag.namn})")

        # 5. Spara i databas
        # Rensa gamla chunks för denna lag
        conn.execute("DELETE FROM lag_chunk WHERE sfs = ?", (lag.sfs,))
        conn.execute("DELETE FROM lag_chunk_fts WHERE sfs = ?", (lag.sfs,))

        # Spara huvudpost
        conn.execute(
            """
            INSERT OR REPLACE INTO lag_dokument
            (sfs, dok_id, namn, kortnamn, tom_sfs, systemdatum, hamtad, lank_manniska, lank_maskin, ratext)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lag.sfs,
                lag.dok_id,
                lag.namn,
                lag.kortnamn,
                tom_sfs,
                systemdatum,
                hamtad_tid,
                lag.lank_manniska,
                lag.lank_maskin,
                raw_text,
            ),
        )

        # Spara chunks
        chunk_rader = [
            (
                c.chunk_id,
                c.sfs,
                c.dok_id,
                c.kapitel_nr,
                c.kapitel_rubrik,
                c.paragraf_nr,
                c.paragraf_rubrik,
                c.paragraf_text,
                c.andringsnotis,
                c.full_text,
            )
            for c in chunks
        ]
        conn.executemany(
            """
            INSERT INTO lag_chunk
            (id, sfs, dok_id, kapitel_nr, kapitel_rubrik, paragraf_nr, paragraf_rubrik, paragraf_text, andringsnotis, full_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            chunk_rader,
        )

        # FTS5 index
        fts_rader = [
            (
                c.chunk_id,
                c.sfs,
                c.kapitel_rubrik or "",
                c.paragraf_rubrik or "",
                c.paragraf_text,
                c.full_text,
            )
            for c in chunks
        ]
        conn.executemany(
            """
            INSERT INTO lag_chunk_fts
            (id, sfs, kapitel_rubrik, paragraf_rubrik, paragraf_text, full_text)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            fts_rader,
        )
        conn.commit()

        # 6. Generera vektorer om begärt
        if generera_vektorer:
            chunk_texts = [(c.chunk_id, c.full_text) for c in chunks]
            _generera_vektorer_for_chunks(
                conn,
                chunk_texts,
                konfig.index.embedding_modell,
                konfig.index.embedding_dim,
            )

        logger.info(
            "Lag %s (%s) indexerad: %d chunks, %s, systemdatum=%s",
            lag.sfs,
            lag.kortnamn,
            len(chunks),
            tom_sfs,
            systemdatum,
        )
        return len(chunks), tom_sfs, systemdatum
    finally:
        if stang_efter:
            conn.close()


def ingest_alla(generera_vektorer: bool = True) -> list[dict[str, Any]]:
    """Hämtar och indexerar alla författningar i lagregistret."""
    lagar = lagregister.las()
    resultat = []

    for lag in lagar:
        start_t = time.perf_counter()
        try:
            antal_chunks, tom_sfs, systemdatum = hamta_och_indexera_lag(
                lag, generera_vektorer=generera_vektorer
            )
            tid_ms = (time.perf_counter() - start_t) * 1000
            resultat.append({
                "sfs": lag.sfs,
                "kortnamn": lag.kortnamn,
                "namn": lag.namn,
                "chunks": antal_chunks,
                "tom_sfs": tom_sfs,
                "systemdatum": systemdatum,
                "tid_ms": round(tid_ms, 1),
                "status": "ok",
            })
        except Exception as e:
            logger.error("Misslyckades indexera %s (%s): %s", lag.sfs, lag.namn, e, exc_info=True)
            resultat.append({
                "sfs": lag.sfs,
                "kortnamn": lag.kortnamn,
                "namn": lag.namn,
                "status": "fel",
                "fel": str(e),
            })

    return resultat


def kontrollera_andringar(
    sfs_lista: list[str] | None = None,
    db_conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """Kontrollerar om någon författning har uppdaterats hos Riksdagen.

    Jämför lokalt lagrat systemdatum i lag_dokument mot färskt systemdatum
    från Riksdagens dokumentstatus-API.

    Returns:
        Lista med rapporter per lag: sfs, kortnamn, andrad (bool), lokalt_datum, fjarr_datum.
    """
    konfig = las_konfig()
    conn = db_conn or oppna_db(Path(konfig.index.db))
    stang_efter = db_conn is None

    alla_lagar = lagregister.las()
    if sfs_lista:
        sfs_set = set(sfs_lista)
        lagar = [l for l in alla_lagar if l.sfs in sfs_set or l.kortnamn in sfs_set]
    else:
        lagar = alla_lagar

    rapport = []
    try:
        for lag in lagar:
            rad = conn.execute(
                "SELECT systemdatum, tom_sfs FROM lag_dokument WHERE sfs = ?", (lag.sfs,)
            ).fetchone()
            lokalt_systemdatum = rad[0] if rad else None

            # Hämta fjärr-systemdatum
            meta_url = f"https://data.riksdagen.se/dokumentstatus/{lag.dok_id}.json"
            try:
                meta = transport.hamta_json("riksdagen", "GET", meta_url)
                fjarr_systemdatum = (
                    meta.get("dokumentstatus", {})
                    .get("dokument", {})
                    .get("systemdatum")
                ) or None
            except Exception as e:
                logger.warning("Kunde inte hämta metadata för %s: %s", lag.sfs, e)
                fjarr_systemdatum = None

            andrad = False
            if lokalt_systemdatum is None:
                andrad = True  # Saknas lokalt
            elif fjarr_systemdatum and fjarr_systemdatum != lokalt_systemdatum:
                andrad = True  # Ändrad på servern

            rapport.append({
                "sfs": lag.sfs,
                "kortnamn": lag.kortnamn,
                "namn": lag.namn,
                "andrad": andrad,
                "lokalt_systemdatum": lokalt_systemdatum,
                "fjarr_systemdatum": fjarr_systemdatum,
            })
    finally:
        if stang_efter:
            conn.close()

    return rapport



def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    print("=== Startar lag-ingest (Steg 16) ===")
    rapporter = ingest_alla(generera_vektorer=True)
    print("\n--- Resultat ---")
    for r in rapporter:
        if r["status"] == "ok":
            print(f"  [{r['sfs']}] {r['kortnamn']} ({r['namn']}): {r['chunks']} chunks | {r['tom_sfs']} | systemdatum={r['systemdatum']} ({r['tid_ms']} ms)")
        else:
            print(f"  [{r['sfs']}] {r['kortnamn']} ({r['namn']}): FEL - {r.get('fel')}")
    print("\nLag-ingest slutförd.")


if __name__ == "__main__":
    main()
