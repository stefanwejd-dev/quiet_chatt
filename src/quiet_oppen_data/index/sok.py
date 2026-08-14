"""Semantisk och hybrid sökning (BM25 + Vektor).

Implementerar Steg 3:
- Laddar in inbäddningar från DB till minnet (varm databas).
- Räknar kosinuslikhet med query-vektor.
- FTS5 BM25-sökning.
- Sammanvägning med Reciprocal Rank Fusion (k=60).
"""
from __future__ import annotations

import logging
import sqlite3
import struct
import time
from typing import Any

import numpy as np
from sentence_transformers import SentenceTransformer

from quiet_oppen_data.index.db import oppna_db
from quiet_oppen_data.konfig import Konfig, las

from quiet_oppen_data.modeller import LagSokresultat, Sokresultat

logger = logging.getLogger(__name__)

# Global cache för "varm" databas
_model: SentenceTransformer | None = None
_vec_ids: list[str] = []
_vec_matrix: np.ndarray | None = None

# Global cache för lagindex
_lag_vec_ids: list[str] = []
_lag_vec_matrix: np.ndarray | None = None



def _initiera(konfig: Konfig) -> tuple[SentenceTransformer, sqlite3.Connection]:
    global _model, _vec_ids, _vec_matrix

    from pathlib import Path
    db_path = Path(konfig.index.db)
    conn = oppna_db(db_path)

    # Kolla meta
    rad = conn.execute("SELECT varde FROM _index_meta WHERE nyckel = 'embedding_modell'").fetchone()
    db_modell = rad[0] if rad else None
    konfig_modell = konfig.index.embedding_modell

    if db_modell != konfig_modell:
        raise ValueError(f"Index byggdes med '{db_modell}' men config kräver '{konfig_modell}'. Kör ingest/embed om.")

    if _model is None:
        logger.info("Laddar embedding-modell %s...", konfig_modell)
        _model = SentenceTransformer(konfig_modell)

    if _vec_matrix is None:
        logger.info("Laddar %d inbäddningar från databas till minnet...", konfig.index.embedding_dim)
        rader = conn.execute("SELECT datamangd_id, vektor FROM embedding").fetchall()
        dim = konfig.index.embedding_dim
        
        matrix = []
        _vec_ids.clear()
        
        for d_id, blob in rader:
            if blob:
                vektor = struct.unpack(f"{dim}f", blob)
                matrix.append(vektor)
                _vec_ids.append(d_id)
        
        # Normalisera vektorerna så cosinuslikhet blir en enkel punktprodukt (vektorlängden är alltid 1)
        arr = np.array(matrix, dtype=np.float32)
        norm = np.linalg.norm(arr, axis=1, keepdims=True)
        # Undvik division med noll
        norm[norm == 0] = 1
        _vec_matrix = arr / norm
        logger.info("Laddade %d inbäddningar till minnet.", len(_vec_ids))

    return _model, conn


def _gissa_adapter(titel: str, format_str: str, access_url: str) -> str:
    """Simpel heuristik för att tipsa planeraren.

    Bara URL och format vägs in. `titel` tas emot för anropssidans skull men
    används inte — titeln säger sällan något om protokollet, och OGC-tjänster
    (där titeln vore en signal) filtreras redan bort vid ingest.
    """
    url = (access_url or "").lower()
    fmt = (format_str or "").lower()


    if "pxweb" in url or "scb" in url:
        return "pxweb"
    if "rowstore" in url or "entryscape" in url:
        return "rowstore"
    if "json" in fmt or "api" in url:
        return "json_rest"
    
    return "dataportal (katalogsvar)"


def fts5_escape(fraga: str) -> str:
    # Rensar så inte specialtecken kraschar fts5
    import re
    rensad = re.sub(r'[^a-zA-ZåäöÅÄÖ0-9\s]', '', fraga).strip()
    # Stjärna på slutet av varje ord
    if not rensad:
        return ""
    return " OR ".join(f"{o}*" for o in rensad.split())


def sok(fraga: str, max_antal: int = 10) -> list[Sokresultat]:
    start_t = time.perf_counter()
    konfig = las()
    modell, conn = _initiera(konfig)

    # 1. Tät (vektor) sökning
    q_vec = modell.encode([fraga], convert_to_numpy=True)[0]
    q_norm = np.linalg.norm(q_vec)
    if q_norm > 0:
        q_vec = q_vec / q_norm

    # Punktprodukt är cosinuslikhet eftersom allt är normaliserat
    scores = np.dot(_vec_matrix, q_vec)
    
    # Hämta top 100
    top_k_vec = 100
    if len(scores) > top_k_vec:
        # argpartition är O(N), sedan sorteras bara topp 100
        topp_idx = np.argpartition(scores, -top_k_vec)[-top_k_vec:]
        # Sortera dessa (från högst till lägst)
        topp_idx = topp_idx[np.argsort(scores[topp_idx])[::-1]]
    else:
        topp_idx = np.argsort(scores)[::-1]
        
    vec_rank = {}
    for rank, idx in enumerate(topp_idx, start=1):
        vec_rank[_vec_ids[idx]] = rank

    # 2. Gles (FTS5 / BM25) sökning
    fts_fraga = fts5_escape(fraga)
    bm25_rank = {}
    if fts_fraga:
        # ORDER BY rank asc (rank är negativt score i sqlite fts5)
        cur = conn.execute(
            "SELECT id FROM datamangd_fts WHERE datamangd_fts MATCH ? ORDER BY rank LIMIT 100",
            (fts_fraga,)
        )
        for rank, (d_id,) in enumerate(cur.fetchall(), start=1):
            bm25_rank[d_id] = rank

    # 3. Reciprocal Rank Fusion (RRF)
    k = 60
    rrf_scores = {}
    alla_id = set(vec_rank.keys()) | set(bm25_rank.keys())
    
    for d_id in alla_id:
        v_r = vec_rank.get(d_id, 1000)
        b_r = bm25_rank.get(d_id, 1000)
        
        score = 0.0
        if v_r < 1000:
            score += 1.0 / (k + v_r)
        if b_r < 1000:
            score += 1.0 / (k + b_r)
            
        rrf_scores[d_id] = score

    # Sortera och plocka ut topp N
    topp_slutlig = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)[:max_antal]

    if not topp_slutlig:
        return []

    # 4. Bygg resultat-objekt från DB
    # Hämta datamängd och eventuell förstadistribution
    placeholders = ",".join("?" for _ in topp_slutlig)
    sql = f"""
        SELECT 
            dm.id, dm.titel, dm.beskrivning, dm.utgivare, dm.licens,
            dist.format, dist.access_url
        FROM datamangd dm
        LEFT JOIN distribution dist ON dist.datamangd_id = dm.id
        WHERE dm.id IN ({placeholders})
    """
    
    rader = conn.execute(sql, topp_slutlig).fetchall()
    
    # Gruppera distributioner, en datamängd kan ha flera, plocka den första (föredra API/JSON)
    db_poster = {}
    for rad in rader:
        d_id, titel, besk, utg, licens, fmt, a_url = rad
        if d_id not in db_poster:
            db_poster[d_id] = {
                "id": d_id, "titel": titel, "beskrivning": besk,
                "utgivare": utg, "licens": licens,
                "format": fmt, "access_url": a_url
            }
        else:
            # Uppdatera om vi hittar json
            if fmt and "json" in fmt.lower() and db_poster[d_id]["format"] and "json" not in db_poster[d_id]["format"].lower():
                db_poster[d_id]["format"] = fmt
                db_poster[d_id]["access_url"] = a_url
                
    resultat = []
    for d_id in topp_slutlig:
        data = db_poster.get(d_id)
        if not data:
            continue
            
        adapter_hint = _gissa_adapter(data["titel"], data["format"], data["access_url"])
        
        sr = Sokresultat(
            datamangd_id=data["id"],
            titel=data["titel"] or "",
            beskrivning=(data["beskrivning"] or "")[:200] + "...", # Korta ner i listan
            utgivare=data["utgivare"] or "Okänd",
            relevans=rrf_scores[d_id],
            licens=data["licens"],
            format=data["format"],
            access_url=data["access_url"],
            adapter_hint=adapter_hint
        )
        resultat.append(sr)
        
    tid = (time.perf_counter() - start_t) * 1000
    logger.info("Sökning '%s' klar på %.1f ms", fraga, tid)
    return resultat


def _initiera_lag(konfig: Konfig, db_conn: sqlite3.Connection | None = None) -> tuple[SentenceTransformer, sqlite3.Connection]:
    """Initierar embedding-modell och laddar lag-inbäddningar till minnet."""
    global _model, _lag_vec_ids, _lag_vec_matrix

    from pathlib import Path
    db_path = Path(konfig.index.db)
    conn = db_conn or oppna_db(db_path)

    konfig_modell = konfig.index.embedding_modell
    if _model is None:
        logger.info("Laddar embedding-modell %s...", konfig_modell)
        _model = SentenceTransformer(konfig_modell)

    # Kontrollera om inbäddningar behöver laddas
    antal_db = conn.execute("SELECT COUNT(*) FROM lag_embedding").fetchone()[0]
    if _lag_vec_matrix is None or len(_lag_vec_ids) != antal_db:
        logger.info("Laddar %d lag-inbäddningar från databas till minnet...", antal_db)
        rader = conn.execute("SELECT chunk_id, vektor FROM lag_embedding").fetchall()
        dim = konfig.index.embedding_dim

        matrix = []
        _lag_vec_ids.clear()

        for c_id, blob in rader:
            if blob:
                vektor = struct.unpack(f"{dim}f", blob)
                matrix.append(vektor)
                _lag_vec_ids.append(c_id)

        if matrix:
            arr = np.array(matrix, dtype=np.float32)
            norm = np.linalg.norm(arr, axis=1, keepdims=True)
            norm[norm == 0] = 1
            _lag_vec_matrix = arr / norm
        else:
            _lag_vec_matrix = np.empty((0, dim), dtype=np.float32)

        logger.info("Laddade %d lag-inbäddningar till minnet.", len(_lag_vec_ids))

    return _model, conn


def sok_lag(
    fraga: str,
    max_antal: int = 5,
    sfs_filter: str | None = None,
    kapitel_filter: str | None = None,
    paragraf_filter: str | None = None,
    db_conn: sqlite3.Connection | None = None,
) -> list[LagSokresultat]:
    """Hybridsökning i lagindexet (BM25 + Vektorsökning + RRF).

    Args:
        fraga: Sökfråga (fritext).
        max_antal: Max antal resultat att returnera.
        sfs_filter: Valfritt SFS-nummer eller kortnamn för filtrering (t.ex. '1999:1229' el. 'IL').
        kapitel_filter: Valfritt kapitelnummer (t.ex. '3').
        paragraf_filter: Valfritt paragrafnummer (t.ex. '9').
        db_conn: Valfri befintlig SQLite-anslutning.

    Returns:
        Lista med LagSokresultat-objekt sorterade efter relevans.
    """
    start_t = time.perf_counter()
    konfig = las()
    modell, conn = _initiera_lag(konfig, db_conn)

    # Om exakt sökning efter specifik paragraf anges utan fritextfråga
    if paragraf_filter and not fraga.strip():
        sql = """
            SELECT
                c.id, c.sfs, c.dok_id, d.namn, d.kortnamn,
                c.kapitel_nr, c.kapitel_rubrik, c.paragraf_nr, c.paragraf_rubrik,
                c.paragraf_text, c.andringsnotis, d.tom_sfs, d.hamtad,
                d.lank_manniska, d.lank_maskin, c.full_text
            FROM lag_chunk c
            JOIN lag_dokument d ON d.sfs = c.sfs
            WHERE c.paragraf_nr = ?
        """
        params: list[Any] = [paragraf_filter]
        if sfs_filter:
            sql += " AND (c.sfs = ? OR d.kortnamn = ?)"
            params.extend([sfs_filter, sfs_filter])
        if kapitel_filter:
            sql += " AND c.kapitel_nr = ?"
            params.append(kapitel_filter)
        sql += f" LIMIT {max_antal}"

        rader = conn.execute(sql, params).fetchall()
        return [
            LagSokresultat(
                chunk_id=r[0], sfs=r[1], dok_id=r[2], lag_namn=r[3], kortnamn=r[4],
                kapitel_nr=r[5], kapitel_rubrik=r[6], paragraf_nr=r[7], paragraf_rubrik=r[8],
                paragraf_text=r[9], andringsnotis=r[10], tom_sfs=r[11], hamtad=r[12],
                lank_manniska=r[13], lank_maskin=r[14], relevans=1.0, full_text=r[15]
            )
            for r in rader
        ]

    # 1. Vektorsökning
    vec_rank: dict[str, int] = {}
    if _lag_vec_matrix is not None and len(_lag_vec_matrix) > 0:
        q_vec = modell.encode([fraga], convert_to_numpy=True)[0]
        q_norm = np.linalg.norm(q_vec)
        if q_norm > 0:
            q_vec = q_vec / q_norm

        scores = np.dot(_lag_vec_matrix, q_vec)
        top_k_vec = min(100, len(scores))
        if len(scores) > top_k_vec:
            topp_idx = np.argpartition(scores, -top_k_vec)[-top_k_vec:]
            topp_idx = topp_idx[np.argsort(scores[topp_idx])[::-1]]
        else:
            topp_idx = np.argsort(scores)[::-1]

        for rank, idx in enumerate(topp_idx, start=1):
            vec_rank[_lag_vec_ids[idx]] = rank

    # 2. FTS5 / BM25 sökning
    fts_fraga = fts5_escape(fraga)
    bm25_rank: dict[str, int] = {}
    if fts_fraga:
        sql = "SELECT id FROM lag_chunk_fts WHERE lag_chunk_fts MATCH ? ORDER BY rank LIMIT 100"
        cur = conn.execute(sql, (fts_fraga,))
        for rank, (c_id,) in enumerate(cur.fetchall(), start=1):
            bm25_rank[c_id] = rank

    # 3. RRF
    k = 60
    rrf_scores: dict[str, float] = {}
    alla_id = set(vec_rank.keys()) | set(bm25_rank.keys())

    for c_id in alla_id:
        v_r = vec_rank.get(c_id, 1000)
        b_r = bm25_rank.get(c_id, 1000)
        score = 0.0
        if v_r < 1000:
            score += 1.0 / (k + v_r)
        if b_r < 1000:
            score += 1.0 / (k + b_r)
        rrf_scores[c_id] = score

    # Sortera och hämta kandidater
    topp_kandidater = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)[:max_antal * 3]
    if not topp_kandidater:
        return []

    # 4. Hämta chunk- och dokumentdata från databasen med eventuella filter
    placeholders = ",".join("?" for _ in topp_kandidater)
    sql = f"""
        SELECT
            c.id, c.sfs, c.dok_id, d.namn, d.kortnamn,
            c.kapitel_nr, c.kapitel_rubrik, c.paragraf_nr, c.paragraf_rubrik,
            c.paragraf_text, c.andringsnotis, d.tom_sfs, d.hamtad,
            d.lank_manniska, d.lank_maskin, c.full_text
        FROM lag_chunk c
        JOIN lag_dokument d ON d.sfs = c.sfs
        WHERE c.id IN ({placeholders})
    """
    params = list(topp_kandidater)
    if sfs_filter:
        sql += " AND (c.sfs = ? OR d.kortnamn = ?)"
        params.extend([sfs_filter, sfs_filter])
    if kapitel_filter:
        sql += " AND c.kapitel_nr = ?"
        params.append(kapitel_filter)
    if paragraf_filter:
        sql += " AND c.paragraf_nr = ?"
        params.append(paragraf_filter)

    rader = conn.execute(sql, params).fetchall()
    rad_dict = {r[0]: r for r in rader}

    resultat: list[LagSokresultat] = []
    for c_id in topp_kandidater:
        if c_id not in rad_dict:
            continue
        r = rad_dict[c_id]
        sr = LagSokresultat(
            chunk_id=r[0],
            sfs=r[1],
            dok_id=r[2],
            lag_namn=r[3],
            kortnamn=r[4],
            kapitel_nr=r[5],
            kapitel_rubrik=r[6],
            paragraf_nr=r[7],
            paragraf_rubrik=r[8],
            paragraf_text=r[9],
            andringsnotis=r[10],
            tom_sfs=r[11],
            hamtad=r[12],
            lank_manniska=r[13],
            lank_maskin=r[14],
            relevans=rrf_scores[c_id],
            full_text=r[15],
        )
        resultat.append(sr)
        if len(resultat) >= max_antal:
            break

    tid = (time.perf_counter() - start_t) * 1000
    logger.info("Lag-sökning '%s' klar på %.1f ms (%d träffar)", fraga, tid, len(resultat))
    return resultat

