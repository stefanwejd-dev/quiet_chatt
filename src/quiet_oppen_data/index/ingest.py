"""Katalogingest — hämtar dataportal.se DCAT-katalog till SQLite.

Körning:
    python -m quiet_oppen_data.index.ingest
    python -m quiet_oppen_data.index.ingest --db data/index.sqlite

Omstartbar: INSERT OR IGNORE säkerställer att inga dubbletter skapas om
körningen avbryts och startas om.

Utredda nyckeldetaljer (PLAN.md steg 2 — bygg inte om dessa):
    * Paginera med type=solr, limit max 100, offset.
    * Resurs-URI: child["info"][<entry-url>][ES_RESOURCE][0]["value"]
    * Metadata:   child["metadata"][<resurs-uri>]
    * Distributioner är EGNA poster i Solr, inte inbäddade i dataset.
    * facetFields=context fungerar; facetFields=publisher ger HTTP 400.
    * OGC-tjänster filtreras per kallregister.yaml _ogc_wms_wfs.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
import tomllib
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# API-konstanter
# ---------------------------------------------------------------------------
SEARCH_URL = "https://admin.dataportal.se/store/search"
DATASET_QUERY      = r"rdfType:http\://www.w3.org/ns/dcat#Dataset AND public:true"
DISTRIBUTION_QUERY = r"rdfType:http\://www.w3.org/ns/dcat#Distribution AND public:true"
PAGE_SIZE      = 100
FARD_INTERVAL  = 0.25   # sekunder mellan sidor → ~4 req/s mot dataportal.se

# RDF-property URIs
ES_RESOURCE         = "http://entrystore.org/terms/resource"
DC_TITLE            = "http://purl.org/dc/terms/title"
DC_DESCRIPTION      = "http://purl.org/dc/terms/description"
DC_PUBLISHER        = "http://purl.org/dc/terms/publisher"
DC_LICENSE          = "http://purl.org/dc/terms/license"
DC_FORMAT           = "http://purl.org/dc/terms/format"
DCAT_THEME          = "http://www.w3.org/ns/dcat#theme"
DCAT_KEYWORD        = "http://www.w3.org/ns/dcat#keyword"
DCAT_DISTRIBUTION   = "http://www.w3.org/ns/dcat#distribution"
DCAT_ACCESS_URL     = "http://www.w3.org/ns/dcat#accessURL"
DCAT_ACCESS_SERVICE = "http://www.w3.org/ns/dcat#accessService"
DCAT_IS_DIST_OF     = "http://www.w3.org/ns/dcat#isDistributionOf"
DC_IS_PART_OF       = "http://purl.org/dc/terms/isPartOf"
RDFS_LABEL          = "http://www.w3.org/2000/01/rdf-schema#label"
FOAF_NAME           = "http://xmlns.com/foaf/0.1/name"

# OGC-filter (kallregister.yaml → _ogc_wms_wfs)
OGC_MARKERS      = frozenset({"wms", "wfs", "wmts", "vnd.ogc", "ogcapi", "wcs"})
OGC_TITEL_SUFFIX = ("visningstjänst", "nedladdningstjänst", "view service", "download service")


# ---------------------------------------------------------------------------
# Metadatahjälpare
# ---------------------------------------------------------------------------

def _text_varden(prop_lista: list[dict]) -> list[str]:
    """Hämtar strängvärden, föredrar sv → en → övriga."""
    for lang in ("sv", "en", None):
        varden = [
            v["value"]
            for v in prop_lista
            if v.get("value") and (lang is None or v.get("lang") == lang)
            and v.get("type", "literal") != "uri"
        ]
        if varden:
            return varden
    return []


def hamta_text(metadata: dict, prop: str) -> str | None:
    varden = _text_varden(metadata.get(prop, []))
    return varden[0] if varden else None


def hamta_text_lista(metadata: dict, prop: str) -> list[str]:
    return _text_varden(metadata.get(prop, []))


def hamta_uri(metadata: dict, prop: str) -> str | None:
    for v in metadata.get(prop, []):
        val = v.get("value", "").strip()
        if val:
            return val
    return None


def hamta_uri_lista(metadata: dict, prop: str) -> list[str]:
    return [v["value"] for v in metadata.get(prop, []) if v.get("value", "").strip()]


def hamta_utgivare(metadata: dict, alla_metadata: dict) -> str | None:
    """Extraherar utgivarnamn. Hanterar literal och URI-referens."""
    values = metadata.get(DC_PUBLISHER, [])
    # 1. Literalvärde
    for v in values:
        if v.get("type", "literal") != "uri" and v.get("value"):
            return v["value"]
    # 2. URI → leta label i samma metadata
    for v in values:
        pub_uri = v.get("value", "").strip()
        if not pub_uri:
            continue
        pub_meta = alla_metadata.get(pub_uri, {})
        for label_prop in (RDFS_LABEL, FOAF_NAME, DC_TITLE):
            label = hamta_text(pub_meta, label_prop)
            if label:
                return label
        # Fallback: sista segmentet av URI
        segment = pub_uri.rstrip("/").rsplit("/", 1)[-1]
        if segment:
            return segment
    return None


def ar_ogc(format_str: str | None, access_url: str | None, titel: str | None) -> bool:
    """Returnerar True om distributionen är en OGC-tjänst som ska filtreras."""
    for kandidat in (format_str, access_url):
        if kandidat and any(m in kandidat.lower() for m in OGC_MARKERS):
            return True
    if titel and any(titel.lower().endswith(s) for s in OGC_TITEL_SUFFIX):
        return True
    return False


def bygg_manniskolank(entry_url: str) -> str | None:
    """Konstruerar dataportal.se-länk från EntryScape entry-URL.

    https://admin.dataportal.se/store/{ctx}/entry/{id}
    → https://www.dataportal.se/datasets/{ctx}_{id}
    """
    try:
        after_store = entry_url.split("/store/", 1)[1]
        # Ta bort eventuellt "entry/" infix och hitta ctx + entryid
        delar = [d for d in after_store.split("/") if d and d != "entry"]
        if len(delar) >= 2:
            ctx, entry_id = delar[0], delar[-1]
            return f"https://www.dataportal.se/datasets/{ctx}_{entry_id}"
    except (IndexError, ValueError):
        pass
    return None


def hamta_entry_och_resurs(child: dict) -> tuple[str, str] | None:
    """Returnerar (entry_url, resurs_uri) eller None.

    Följer PLAN.md:
        child["info"][<entry-url>][ES_RESOURCE][0]["value"]
    """
    for entry_url, info_dict in child.get("info", {}).items():
        try:
            resurs_uri = info_dict[ES_RESOURCE][0]["value"]
            if resurs_uri:
                return entry_url, resurs_uri
        except (KeyError, IndexError, TypeError):
            continue
    return None


# ---------------------------------------------------------------------------
# Pass 1 — Dataset
# ---------------------------------------------------------------------------

def behandla_dataset(conn, child: dict) -> int:
    """Parsar ett dataset-barn och skriver till DB.

    Returns:
        1 om posten var ny, 0 om den redan fanns (INSERT OR IGNORE).
    """
    parsed = hamta_entry_och_resurs(child)
    if not parsed:
        return 0
    entry_url, resurs_uri = parsed

    alla_metadata: dict = child.get("metadata", {})
    metadata: dict = alla_metadata.get(resurs_uri, {})
    if not metadata:
        return 0

    titel        = hamta_text(metadata, DC_TITLE) or ""
    beskrivning  = hamta_text(metadata, DC_DESCRIPTION) or ""
    utgivare     = hamta_utgivare(metadata, alla_metadata) or ""
    licens       = hamta_uri(metadata, DC_LICENSE) or ""
    tema         = " | ".join(hamta_uri_lista(metadata, DCAT_THEME))
    nyckelord    = " ".join(hamta_text_lista(metadata, DCAT_KEYWORD))
    manniskolank = bygg_manniskolank(entry_url) or ""
    dist_uris    = hamta_uri_lista(metadata, DCAT_DISTRIBUTION)

    cur = conn.execute(
        """INSERT OR IGNORE INTO datamangd
               (id, titel, beskrivning, utgivare, licens, tema, nyckelord, manniskolank)
           VALUES (?,?,?,?,?,?,?,?)""",
        (resurs_uri, titel, beskrivning, utgivare, licens, tema, nyckelord, manniskolank),
    )

    if cur.rowcount > 0:
        # FTS5 — bara när raden faktiskt är ny (undviker dubbletter vid omstart)
        conn.execute(
            "INSERT INTO datamangd_fts (id, titel, beskrivning, nyckelord) VALUES (?,?,?,?)",
            (resurs_uri, titel, beskrivning, nyckelord),
        )
        # Bygg distribution→dataset-länktabell för pass 2
        for dist_uri in dist_uris:
            conn.execute(
                "INSERT OR IGNORE INTO _dist_dataset_link (dist_uri, dataset_id) VALUES (?,?)",
                (dist_uri, resurs_uri),
            )
        return 1
    return 0


def ingest_datasets(conn, client: httpx.Client) -> int:
    """Paginerar Solr-sökningen för Dataset och fyller datamangd."""
    offset, n_ny, n_total = 0, 0, None

    while True:
        resp = client.get(
            SEARCH_URL,
            params={
                "type": "solr",
                "query": DATASET_QUERY,
                "limit": PAGE_SIZE,
                "offset": offset,
            },
        )
        resp.raise_for_status()
        data = resp.json()

        resurs   = data.get("resource", {})
        children = resurs.get("children", [])

        if n_total is None:
            n_total = resurs.get("size") or 0
            logger.info("Dataset: API rapporterar %d poster (paginerar till slut)", n_total)

        if not children:
            break

        for child in children:
            n_ny += behandla_dataset(conn, child)

        conn.commit()
        offset += len(children)

        if offset % 1000 < PAGE_SIZE:
            logger.info("Dataset: %d sidan (%d nya hittills)", offset, n_ny)

        # Avsluta när API:et ger tom lista — inte på n_total (kan vara 0 eller felaktig)
        time.sleep(FARD_INTERVAL)

    logger.info("Dataset klart: %d / %d  (%d nya)", offset, n_total, n_ny)
    return n_ny


# ---------------------------------------------------------------------------
# Pass 2 — Distributioner
# ---------------------------------------------------------------------------

def behandla_distribution(conn, child: dict) -> int:
    """Parsar en distribution och skriver till DB.

    Filtrerar bort OGC-tjänster (ARKITEKTUR.md + kallregister.yaml _ogc_wms_wfs).
    Länkar till parent-dataset via:
        1) dcat:isDistributionOf i metadata
        2) dc:isPartOf i metadata
        3) _dist_dataset_link (byggd under pass 1)
    """
    parsed = hamta_entry_och_resurs(child)
    if not parsed:
        return 0
    _, resurs_uri = parsed

    alla_metadata: dict = child.get("metadata", {})
    metadata: dict = alla_metadata.get(resurs_uri, {})
    if not metadata:
        return 0

    format_str  = hamta_text(metadata, DC_FORMAT) or hamta_uri(metadata, DC_FORMAT) or ""
    titel       = hamta_text(metadata, DC_TITLE)
    access_url  = hamta_uri(metadata, DCAT_ACCESS_URL) or ""
    access_svc  = hamta_uri(metadata, DCAT_ACCESS_SERVICE) or ""

    # OGC-filter
    if ar_ogc(format_str, access_url, titel):
        return 0

    # Hitta parent dataset
    dataset_id = (
        hamta_uri(metadata, DCAT_IS_DIST_OF)
        or hamta_uri(metadata, DC_IS_PART_OF)
    )
    if not dataset_id:
        row = conn.execute(
            "SELECT dataset_id FROM _dist_dataset_link WHERE dist_uri = ?",
            (resurs_uri,),
        ).fetchone()
        if row:
            dataset_id = row[0]

    cur = conn.execute(
        """INSERT OR IGNORE INTO distribution (id, datamangd_id, format, access_url, access_service)
           VALUES (?,?,?,?,?)""",
        (resurs_uri, dataset_id, format_str, access_url, access_svc),
    )
    return cur.rowcount


def ingest_distributions(conn, client: httpx.Client) -> int:
    """Paginerar Solr-sökningen för Distribution och fyller distribution-tabellen."""
    offset, n_ny, n_total = 0, 0, None

    while True:
        resp = client.get(
            SEARCH_URL,
            params={
                "type": "solr",
                "query": DISTRIBUTION_QUERY,
                "limit": PAGE_SIZE,
                "offset": offset,
            },
        )
        resp.raise_for_status()
        data = resp.json()

        resurs   = data.get("resource", {})
        children = resurs.get("children", [])

        if n_total is None:
            n_total = resurs.get("size") or 0
            logger.info("Distributioner: API rapporterar %d poster (paginerar till slut)", n_total)

        if not children:
            break

        for child in children:
            n_ny += behandla_distribution(conn, child)

        conn.commit()
        offset += len(children)

        if offset % 1000 < PAGE_SIZE:
            logger.info("Distribution: %d sidan (%d nya hittills)", offset, n_ny)

        time.sleep(FARD_INTERVAL)

    logger.info("Distribution klart: %d / %d  (%d nya)", offset, n_total, n_ny)
    return n_ny


# ---------------------------------------------------------------------------
# Huvudfunktion
# ---------------------------------------------------------------------------

def _las_db_sokväg() -> Path:
    """Läser db-sökvägen ur config.toml utan att kräva ANTHROPIC_API_KEY."""
    from quiet_oppen_data.konfig import _KONFIG_FIL
    with open(_KONFIG_FIL, "rb") as f:
        data = tomllib.load(f)
    return Path(data["index"]["db"])


def main(db_sokväg: Path | None = None) -> None:
    """Kör fullständig katalogingest i två pass."""
    if db_sokväg is None:
        db_sokväg = _las_db_sokväg()

    from quiet_oppen_data.index.db import oppna_db
    logger.info("Databas: %s", db_sokväg)
    conn = oppna_db(db_sokväg)

    headers = {
        "Accept":     "application/json",
        "User-Agent": "quiet-oppen-data/0.1 (quiet.nu)",
    }

    with httpx.Client(headers=headers, timeout=30.0) as client:
        logger.info("=== Pass 1: Dataset ===")
        n_dataset = ingest_datasets(conn, client)

        logger.info("=== Pass 2: Distributioner ===")
        n_dist = ingest_distributions(conn, client)

    # Rapportera slutresultat
    totalt_dm   = conn.execute("SELECT COUNT(*) FROM datamangd").fetchone()[0]
    totalt_dist = conn.execute("SELECT COUNT(*) FROM distribution").fetchone()[0]
    conn.close()

    logger.info(
        "Ingest klar: datamängder=%d (+%d nya)  distributioner=%d (+%d nya)",
        totalt_dm, n_dataset, totalt_dist, n_dist,
    )
    print(f"datamängder={totalt_dm}  distributioner={totalt_dist}")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )
    parser = argparse.ArgumentParser(description="Dataportal-katalogingest")
    parser.add_argument("--db", type=Path, default=None, help="Sökväg till SQLite-databasen")
    args = parser.parse_args()
    main(args.db)
