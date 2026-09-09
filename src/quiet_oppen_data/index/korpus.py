"""Gemensam skrivväg till textkorpusindexet (steg 23-24).

BFN och EUR-Lex delar form: ett dokument med källuppgifter, och under det
numrerade textstycken. Skrivningen, vektoriseringen och färskhetsrapporten är
därför en enda uppsättning funktioner i stället för två nästan identiska — se
kommentaren till `korpus_dokument` i db.py för skälet.

Det som INTE ligger här är hämtning och parsning. De skiljer sig helt åt
mellan en pdf hos BFN och en XHTML-rättsakt hos EUR-Lex, och att pressa in dem
bakom ett gemensamt gränssnitt hade gett en abstraktion som döljer just det
som är olika.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import struct
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from quiet_oppen_data.index.db import oppna_db
from quiet_oppen_data.konfig import las as las_konfig

logger = logging.getLogger(__name__)

# Minsta antal bokstäver ett stycke måste ha för att indexeras — se
# kommentaren i skriv_dokument.
_MINST_BOKSTAVER = 3
_BOKSTAV = re.compile(r"[^\W\d_]", re.UNICODE)


@dataclass(frozen=True)
class KorpusChunk:
    """Ett sökbart stycke."""

    beteckning: str | None       # "12.7" | "Artikel 168"
    blocktyp: str                # allmant_rad | kommentar | lagtext | exempel | artikel | ...
    text: str
    kapitel_nr: str | None = None
    kapitel_rubrik: str | None = None
    avsnitt: str | None = None
    sida: int | None = None

    def full_text(self, dokumenttitel: str, kortnamn: str | None) -> str:
        """Sökytan: styckets text med sin plats och sitt dokument omkring sig.

        Utan dokumentnamnet i sökytan hittar en fråga om "K3 materiella
        anläggningstillgångar" bara de stycken som råkar upprepa ordet K3 i
        sin egen text — vilket ett numrerat råd sällan gör.
        """
        delar = [d for d in (
            kortnamn,
            dokumenttitel,
            f"{self.kapitel_nr} {self.kapitel_rubrik}" if self.kapitel_rubrik else None,
            self.avsnitt,
            self.beteckning,
            self.text,
        ) if d]
        return "\n".join(delar)


@dataclass(frozen=True)
class KorpusDokument:
    """Ett dokument med sina källuppgifter och sina stycken."""

    korpus: str                  # "bfn" | "eu"
    dok_id: str
    titel: str
    utgivare: str
    lank_manniska: str
    lank_maskin: str
    kortnamn: str | None = None
    lydelse: str = ""
    version: str = ""
    licens: str = ""
    attribution: str | None = None
    anmarkning: str = ""
    hamtad: str = ""
    chunkar: list[KorpusChunk] = field(default_factory=list)

    @property
    def id(self) -> str:
        return f"{self.korpus}:{self.dok_id}"


# Embedding-modellen laddas EN gång per process, inte en gång per dokument.
#
# skriv_dokument anropas per dokument, och en BFN-skörd har 132 av dem. Med
# modelladdning inne i funktionen laddades KBLab-modellen (~500 MB) om för
# varje dokument — mätt till tiotals sekunder styck, alltså timmar för en
# ingest vars vektorisering tar minuter. Cachen ligger på modulnivå av samma
# skäl som sok._model gör det.
_modell_cache: dict[str, Any] = {}


def _hamta_modell(modell_namn: str) -> Any:
    if modell_namn not in _modell_cache:
        from sentence_transformers import SentenceTransformer

        logger.info("Laddar embedding-modell %s ...", modell_namn)
        _modell_cache[modell_namn] = SentenceTransformer(modell_namn)
    return _modell_cache[modell_namn]


def _generera_vektorer(
    conn: sqlite3.Connection,
    chunkar: list[tuple[str, str]],
    modell_namn: str,
    dim: int,
    batch_size: int = 64,
) -> None:
    """Genererar embeddings och sparar i korpus_embedding."""
    if not chunkar:
        return

    model = _hamta_modell(modell_namn)
    logger.info("Vektoriserar %d chunkar ...", len(chunkar))

    for i in range(0, len(chunkar), batch_size):
        batch = chunkar[i : i + batch_size]
        ids = [c[0] for c in batch]
        texter = [c[1] for c in batch]
        embeddings = model.encode(texter, convert_to_numpy=True)
        db_batch = [
            (c_id, struct.pack(f"{dim}f", *emb.tolist()))
            for c_id, emb in zip(ids, embeddings, strict=True)
        ]
        conn.executemany(
            "INSERT OR REPLACE INTO korpus_embedding (chunk_id, vektor) VALUES (?, ?)",
            db_batch,
        )
        conn.commit()
        logger.info("Vektorisering: %d / %d chunkar klara...",
                    min(i + batch_size, len(chunkar)), len(chunkar))


def skriv_dokument(
    conn: sqlite3.Connection,
    dok: KorpusDokument,
    *,
    generera_vektorer: bool = True,
) -> int:
    """Skriver ett dokument med sina chunkar. Returnerar antalet chunkar.

    Ett dokument utan chunkar skrivs ändå — med sin `anmarkning`. En inskannad
    pdf utan textlager ska synas som en känd lucka i /matning, inte försvinna
    tyst ur indexet (steg 16:s regel: tyst bortfall är inte godkänt).
    """
    konfig = las_konfig()
    dok_pk = dok.id

    conn.execute("DELETE FROM korpus_chunk WHERE dokument_id = ?", (dok_pk,))
    conn.execute("DELETE FROM korpus_chunk_fts WHERE id LIKE ?", (f"{dok_pk}#%",))

    conn.execute(
        """
        INSERT OR REPLACE INTO korpus_dokument
        (id, korpus, dok_id, titel, kortnamn, utgivare, lydelse, version, hamtad,
         lank_manniska, lank_maskin, licens, attribution, anmarkning)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            dok_pk, dok.korpus, dok.dok_id, dok.titel, dok.kortnamn, dok.utgivare,
            dok.lydelse, dok.version, dok.hamtad or datetime.now(UTC).isoformat(),
            dok.lank_manniska, dok.lank_maskin, dok.licens, dok.attribution,
            dok.anmarkning,
        ),
    )

    chunk_rader = []
    fts_rader = []
    vektor_rader = []
    # Stycken utan sakinnehåll indexeras inte. Textutvinning ur pdf lämnar
    # kvar sidnummer ("1", "1(5)"), avdelarstreck ("_______") och lösryckta
    # siffror; i EU:s XHTML blir en borttagen artikel ibland ett tomt block.
    # De blir sökträffar med ingenting i, och en sökträff med ingenting i är
    # värre än en träff mindre — modellen får en Faktapost att citera som
    # inte säger något.
    #
    # Gränsen räknar BOKSTÄVER, inte tecken. En ren längdgräns hade tagit
    # bort "Upphävd. (BFNAR 2013:3)." — att en punkt är upphävd ÄR ett svar —
    # medan bokstavsräkningen behåller den och ändå fångar "9." och "1(5)".
    # Mätt på de 8 954 styckena i korpusen 2026-09-09: 88 föll bort, varav 80
    # utan en enda bokstav.
    utan_innehall = 0
    # Beteckningar kan upprepas i en och samma pdf (samma punkt citerad i en
    # ändringsförfattning). Ett löpnummer i nyckeln gör id:t unikt utan att
    # dölja att beteckningen är densamma.
    for nr, c in enumerate(dok.chunkar, 1):
        if _MINST_BOKSTAVER > len(_BOKSTAV.findall(c.text)):
            utan_innehall += 1
            continue
        beteckning_del = (c.beteckning or f"s{c.sida or 0}").replace(" ", "")
        chunk_id = f"{dok_pk}#{beteckning_del}~{nr}"
        full = c.full_text(dok.titel, dok.kortnamn)
        chunk_rader.append((
            chunk_id, dok.korpus, dok_pk, c.blocktyp, c.beteckning,
            c.kapitel_nr, c.kapitel_rubrik, c.avsnitt, c.text, c.sida, full,
        ))
        fts_rader.append((
            chunk_id, dok.korpus, c.beteckning or "", c.kapitel_rubrik or "",
            c.avsnitt or "", c.text, full,
        ))
        vektor_rader.append((chunk_id, full))

    if utan_innehall:
        logger.info("%s: %d stycken utan sakinnehåll indexerades inte.",
                    dok.id, utan_innehall)

    if chunk_rader:
        conn.executemany(
            """
            INSERT INTO korpus_chunk
            (id, korpus, dokument_id, blocktyp, beteckning, kapitel_nr,
             kapitel_rubrik, avsnitt, text, sida, full_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            chunk_rader,
        )
        conn.executemany(
            """
            INSERT INTO korpus_chunk_fts
            (id, korpus, beteckning, kapitel_rubrik, avsnitt, text, full_text)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            fts_rader,
        )
    conn.commit()

    if generera_vektorer and vektor_rader:
        _generera_vektorer(
            conn, vektor_rader,
            konfig.index.embedding_modell, konfig.index.embedding_dim,
        )

    return len(chunk_rader)


def hamta_version(conn: sqlite3.Connection, korpus: str, dok_id: str) -> str | None:
    """Den lagrade färskhetsnyckeln, eller None om dokumentet saknas."""
    rad = conn.execute(
        "SELECT version FROM korpus_dokument WHERE id = ?", (f"{korpus}:{dok_id}",)
    ).fetchone()
    return rad[0] if rad else None


def statistik(db_conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Antal dokument och chunkar per korpus — underlag för /matning."""
    konfig = las_konfig()
    conn = db_conn or oppna_db(Path(konfig.index.db))
    stang_efter = db_conn is None
    try:
        ut: dict[str, Any] = {}
        for (korpus,) in conn.execute(
            "SELECT DISTINCT korpus FROM korpus_dokument ORDER BY korpus"
        ).fetchall():
            dok = conn.execute(
                "SELECT COUNT(*) FROM korpus_dokument WHERE korpus = ?", (korpus,)
            ).fetchone()[0]
            chunkar = conn.execute(
                "SELECT COUNT(*) FROM korpus_chunk WHERE korpus = ?", (korpus,)
            ).fetchone()[0]
            med_anmarkning = conn.execute(
                "SELECT COUNT(*) FROM korpus_dokument "
                "WHERE korpus = ? AND anmarkning IS NOT NULL AND anmarkning != ''",
                (korpus,),
            ).fetchone()[0]
            ut[korpus] = {
                "dokument": dok,
                "chunkar": chunkar,
                "dokument_med_anmarkning": med_anmarkning,
            }
        return ut
    finally:
        if stang_efter:
            conn.close()


# Antal dygn ett dokument får ligga utan lyckad omkontroll innan det räknas
# som "ligger efter" i /matning. Samma tröskel och samma skäl som för
# lagkorpuset (lag_ingest._ALDER_TROSKEL_DYGN).
_ALDER_TROSKEL_DYGN = 2


def las_alder(
    korpus: str | None = None,
    db_conn: sqlite3.Connection | None = None,
    troskel_dygn: int = _ALDER_TROSKEL_DYGN,
) -> list[dict[str, Any]]:
    """Korpusens ålder per dokument — underlag för `GET /matning`."""
    konfig = las_konfig()
    conn = db_conn or oppna_db(Path(konfig.index.db))
    stang_efter = db_conn is None
    try:
        sql = (
            "SELECT korpus, dok_id, kortnamn, titel, hamtad, anmarkning "
            "FROM korpus_dokument"
        )
        params: list[Any] = []
        if korpus:
            sql += " WHERE korpus = ?"
            params.append(korpus)
        sql += " ORDER BY korpus, dok_id"

        nu = datetime.now(UTC)
        resultat = []
        for k, dok_id, kortnamn, titel, hamtad, anmarkning in conn.execute(sql, params):
            try:
                hamtad_dt = datetime.fromisoformat(hamtad)
                if hamtad_dt.tzinfo is None:
                    hamtad_dt = hamtad_dt.replace(tzinfo=UTC)
                dygn = (nu - hamtad_dt).total_seconds() / 86400
            except (ValueError, TypeError):
                dygn = None
            resultat.append({
                "korpus": k,
                "dok_id": dok_id,
                "kortnamn": kortnamn,
                "titel": titel,
                "hamtad": hamtad,
                "dygn_sedan_hamtning": round(dygn, 2) if dygn is not None else None,
                "ligger_efter": dygn is None or dygn > troskel_dygn,
                "anmarkning": anmarkning or "",
            })
        return resultat
    finally:
        if stang_efter:
            conn.close()
