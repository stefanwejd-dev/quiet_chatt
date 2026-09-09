"""Ingest av EU-korpuset (steg 24).

Hämtar EU-rättsakter från publications.europa.eu i **gällande konsoliderad
lydelse på svenska**, parsar dem till artiklar och indexerar dem i
korpustabellerna.

## Den viktigaste regeln i modulen

CELEX-numret i registret (t.ex. `32013L0034`) pekar på rättsaktens
URSPRUNGLIGA lydelse — den som publicerades i Officiella tidningen 2013. Den
konsoliderade lydelsen har ett annat nummer: `02013L0034-20260318`, där
datumet är konsolideringspunkten. Kontrollerat 2026-09-09: `02013L0034` utan
datum ger 404, så datumet måste tas reda på, inte gissas.

Att indexera ursprungslydelsen som om den vore gällande vore samma fel som att
tillämpa en upphävd paragraf — och svårare att upptäcka, eftersom källänken
skulle vara korrekt. Modulen frågar därför källan vilka konsolideringar som
finns (`Accept: application/xml;notice=object`, ~1,6 MB mot 20 MB för hela
RDF-grafen) och väljer den senaste vars datum har INFALLIT.

Framtida konsolideringar förekommer: 32013L0034 hade 2026-09-09 en
konsolidering daterad 2027-01-30. Den får inte väljas — det är samma regel som
`status: framtida` i systerprojektets regelverkskontrakt, och samma skäl: en
regel som ännu inte trätt i kraft får inte tillämpas på dagens fråga.

Saknar en rättsakt konsoliderade lydelser helt hämtas ursprungslydelsen, och
`lydelse` sätts uttryckligen till "ursprunglig lydelse" i stället för att
lämnas tom och se ut som en okänd konsolidering.
"""
from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from quiet_oppen_data import euregister
from quiet_oppen_data.adaptrar import transport
from quiet_oppen_data.euregister import Rattsakt
from quiet_oppen_data.index.db import oppna_db
from quiet_oppen_data.index.korpus import (
    KorpusChunk,
    KorpusDokument,
    hamta_version,
    skriv_dokument,
)
from quiet_oppen_data.index.eu_parser import parsa
from quiet_oppen_data.konfig import las as las_konfig
from quiet_oppen_data.register import Kalla, hamta

logger = logging.getLogger(__name__)

KORPUS = "eu"
KALLA = "eurlex"

# Innehållsförhandlingen. Accept-Language MÅSTE sättas: utan huvudet, och med
# en okänd språkkod, svarar tjänsten 400. Det är ett bra felläge — den kan
# alltså inte tyst lämna ut engelska när svenska begärts (kontrollerat
# 2026-09-09 med swe/sv/fra/xx/tomt).
_XHTML_HUVUDEN = {"Accept": "application/xhtml+xml", "Accept-Language": "swe"}
# Objektnotisen listar rättsaktens konsolideringar. notice=tree ger samma
# uppgifter men dubbelt så mycket data; notice=branch svarar 400.
_NOTIS_HUVUDEN = {"Accept": "application/xml;notice=object"}

DEMO_CELEX = ["32013L0034", "32006L0112"]


def _idag() -> date:
    return datetime.now(UTC).date()


def _konsoliderad_bas(celex: str) -> str:
    """`32013L0034` -> `02013L0034`. Konsolideringsfamiljen bär sektor 0."""
    return "0" + celex[1:]


def hitta_konsolideringar(celex: str) -> list[str]:
    """Konsolideringsdatum (ÅÅÅÅMMDD) som källan känner till, i stigande ordning."""
    url = f"{_bas_url()}/{celex}"
    svar = transport.hamta_ocachat(KALLA, "GET", url, headers=_NOTIS_HUVUDEN)
    bas = _konsoliderad_bas(celex)
    return sorted(set(re.findall(rf"{re.escape(bas)}-(\d{{8}})", svar.text())))


def _bas_url() -> str:
    kalla = hamta(KALLA)
    if not isinstance(kalla, Kalla):
        raise RuntimeError(f"Källan {KALLA} saknas eller är blockerad i källregistret.")
    return (kalla.bas_url or "").rstrip("/")


def valj_lydelse(celex: str, idag: date | None = None) -> tuple[str, str, list[str]]:
    """Väljer vilken lydelse som ska hämtas.

    Returns:
        (resurs_id, lydelsebeskrivning, framtida_konsolideringar)

        `resurs_id` är det CELEX som ska hämtas — antingen
        `0xxxx-ÅÅÅÅMMDD` eller det ursprungliga `3xxxx`.
    """
    idag = idag or _idag()
    try:
        datum = hitta_konsolideringar(celex)
    except Exception:
        logger.warning(
            "%s: kunde inte läsa konsolideringsförteckningen — hämtar INTE "
            "ursprungslydelsen i stället, eftersom den kan vara ersatt.",
            celex, exc_info=True,
        )
        raise

    galland = [d for d in datum if date(int(d[:4]), int(d[4:6]), int(d[6:8])) <= idag]
    framtida = [d for d in datum if d not in galland]

    if not datum:
        # Rättsakten har aldrig konsoliderats — ursprungslydelsen ÄR den
        # gällande. Det sägs ut i klartext i stället för att lämnas tomt.
        return celex, "ursprunglig lydelse", []
    if not galland:
        # Alla kända konsolideringar ligger i framtiden. Ursprungslydelsen
        # gäller alltså fortfarande i dag.
        logger.info("%s: samtliga konsolideringar är framtida (%s).", celex, framtida)
        return celex, "ursprunglig lydelse", framtida

    senaste = galland[-1]
    bas = _konsoliderad_bas(celex)
    lydelse = f"{senaste[:4]}-{senaste[4:6]}-{senaste[6:8]}"
    return f"{bas}-{senaste}", lydelse, framtida


def _hamta_lydelse(celex: str) -> tuple[str, str, list[str], Any, str]:
    """Hämtar den lydelse som ska indexeras, med reserv om en resurs saknas.

    Konsolideringsförteckningen kan lista en punkt som ALDRIG publicerats som
    eget dokument. Kontrollerat 2026-09-09: e-fakturadirektivet 32014L0055
    listar konsolideringen 20140526 — direktivets ikraftträdande — men
    `02014L0055-20140526` svarar 404. Rättsakten har i själva verket aldrig
    konsoliderats, och ursprungslydelsen är den gällande.

    Kandidaterna prövas därför nyast först, och ursprungslydelsen sist. Det är
    INTE samma sak som att gissa: varje kandidat är antingen en punkt källan
    själv listat eller rättsaktens egen publikation, och den som svarar 200 är
    den som finns. Att en nyare konsolidering föll bort ska däremot synas, så
    reservvägen lämnar en anmärkning efter sig.

    Returns:
        (resurs_id, lydelse, framtida, svar, anmärkningstillägg)
    """
    resurs, lydelse, framtida = valj_lydelse(celex)

    kandidater: list[tuple[str, str]] = [(resurs, lydelse)]
    if resurs != celex:
        # Äldre gällande konsolideringar, nyast först, och sist originalet.
        datum = hitta_konsolideringar(celex)
        idag = _idag()
        galland = [d for d in datum if date(int(d[:4]), int(d[4:6]), int(d[6:8])) <= idag]
        bas = _konsoliderad_bas(celex)
        for d in reversed(galland):
            post = (f"{bas}-{d}", f"{d[:4]}-{d[4:6]}-{d[6:8]}")
            if post not in kandidater:
                kandidater.append(post)
        kandidater.append((celex, "ursprunglig lydelse"))

    sista_fel: Exception | None = None
    for i, (kandidat, kandidatlydelse) in enumerate(kandidater):
        url = f"{_bas_url()}/{kandidat}"
        try:
            svar = transport.hamta_ocachat(KALLA, "GET", url, headers=_XHTML_HUVUDEN)
        except Exception as e:
            sista_fel = e
            logger.info("%s: %s svarade inte (%s), prövar nästa lydelse.", celex, kandidat, e)
            continue
        not_ = ""
        if i > 0:
            not_ = (
                f"Konsolideringen {resurs} listas av källan men kunde inte hämtas; "
                f"lydelsen {kandidatlydelse} användes i stället."
            )
            logger.warning("%s: %s", celex, not_)
        return kandidat, kandidatlydelse, framtida, svar, not_

    assert sista_fel is not None
    raise sista_fel


def hamta_och_indexera(
    akt: Rattsakt,
    conn: sqlite3.Connection,
    *,
    generera_vektorer: bool = True,
    hoppa_over_oforandrade: bool = False,
) -> dict[str, Any]:
    """Hämtar, parsar och indexerar en rättsakt. Returnerar en rapportrad."""
    kalla = hamta(KALLA)
    if not isinstance(kalla, Kalla):
        raise RuntimeError(f"Källan {KALLA} saknas eller är blockerad i källregistret.")

    resurs, lydelse, framtida, svar, reserv_not = _hamta_lydelse(akt.celex)
    url = f"{_bas_url()}/{resurs}"
    sha = hashlib.sha256(svar.innehall).hexdigest()

    if hoppa_over_oforandrade and hamta_version(conn, KORPUS, akt.celex) == sha:
        return {
            "celex": akt.celex, "kortnamn": akt.kortnamn, "resurs": resurs,
            "lydelse": lydelse, "chunkar": 0, "status": "oforandrad",
            "framtida_konsolideringar": framtida,
        }

    parsad = parsa(svar.text(), akt.celex)

    # Konsolideringsdatumet i dokumentets eget huvud går före det vi räknade
    # fram: källans egen uppgift om sin lydelse väger tyngre än vår slutledning
    # ur en förteckning. Skiljer de sig är det värt en varning — då har vi
    # missförstått något.
    if parsad.konsolideringsdatum and parsad.konsolideringsdatum != lydelse:
        logger.warning(
            "%s: valde konsolidering %s men dokumentet uppger %s. "
            "Dokumentets egen uppgift används.",
            akt.celex, lydelse, parsad.konsolideringsdatum,
        )
        lydelse = parsad.konsolideringsdatum

    chunkar = [
        KorpusChunk(
            beteckning=c.beteckning,
            blocktyp=c.blocktyp,
            text=c.text,
            kapitel_nr=c.kapitel_nr,
            kapitel_rubrik=c.kapitel_rubrik,
            avsnitt=c.avsnitt,
        )
        for c in parsad.chunkar
    ]

    anmarkning = parsad.parsningsanmarkning
    if reserv_not:
        anmarkning = f"{anmarkning} {reserv_not}".strip()
    if framtida:
        # Inte ett fel — men läsaren ska kunna få veta att en senare lydelse
        # är beslutad men ännu inte gäller.
        datum_txt = ", ".join(f"{d[:4]}-{d[4:6]}-{d[6:8]}" for d in framtida)
        tillagg = f"Senare konsolidering beslutad men ej i kraft: {datum_txt}."
        anmarkning = f"{anmarkning} {tillagg}".strip()

    dok = KorpusDokument(
        korpus=KORPUS,
        dok_id=akt.celex,
        titel=akt.namn,
        kortnamn=akt.kortnamn or None,
        utgivare=kalla.myndighet or "Europeiska unionens publikationsbyrå (EUR-Lex)",
        lydelse=lydelse,
        version=sha,
        licens=kalla.licens,
        attribution=kalla.attribution,
        anmarkning=anmarkning,
        hamtad=datetime.now(UTC).isoformat(),
        lank_manniska=akt.lank_manniska,
        # Maskinlänken är den resurs som FAKTISKT hämtades, inte registrets
        # bas-CELEX. Den är beviset, och ett bevis som pekar på en annan
        # lydelse än den citerade vore värdelöst.
        lank_maskin=url,
        chunkar=chunkar,
    )
    antal = skriv_dokument(conn, dok, generera_vektorer=generera_vektorer)

    return {
        "celex": akt.celex,
        "kortnamn": akt.kortnamn,
        "resurs": resurs,
        "lydelse": lydelse,
        "chunkar": antal,
        "artiklar": sum(1 for c in chunkar if c.blocktyp == "artikel"),
        "framtida_konsolideringar": framtida,
        "anmarkning": anmarkning,
        "status": "anmarkning" if parsad.parsningsanmarkning else "ok",
    }


def ingest_alla(
    generera_vektorer: bool = True,
    demo: bool = False,
    endast: list[str] | None = None,
    hoppa_over_oforandrade: bool = False,
    db_conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """Hämtar och indexerar rättsakterna i eu/euregister.yaml."""
    konfig = las_konfig()
    conn = db_conn or oppna_db(Path(konfig.index.db))
    stang_efter = db_conn is None

    try:
        akter = euregister.las()
        if demo:
            akter = [a for a in akter if a.celex in DEMO_CELEX]
        if endast:
            valda = {e.lower() for e in endast}
            akter = [a for a in akter if a.celex.lower() in valda or a.kortnamn.lower() in valda]

        resultat: list[dict[str, Any]] = []
        for akt in akter:
            start_t = time.perf_counter()
            try:
                rad = hamta_och_indexera(
                    akt, conn,
                    generera_vektorer=generera_vektorer,
                    hoppa_over_oforandrade=hoppa_over_oforandrade,
                )
                rad["tid_ms"] = round((time.perf_counter() - start_t) * 1000, 1)
                resultat.append(rad)
            except Exception as e:
                logger.error("Misslyckades indexera %s (%s): %s",
                             akt.celex, akt.kortnamn, e, exc_info=True)
                resultat.append({
                    "celex": akt.celex, "kortnamn": akt.kortnamn,
                    "status": "fel", "fel": str(e),
                })

        if demo:
            from quiet_oppen_data.index.db import satt_meta
            satt_meta(conn, "eu_demo", "1")

        return resultat
    finally:
        if stang_efter:
            conn.close()


def nattlig_eukontroll(
    generera_vektorer: bool = True,
    db_conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Nattlig färskhetskontroll av EU-korpuset.

    EUR-Lex har inget systemdatum motsvarande Riksdagens, så kontrollen jämför
    innehållets sha256. Det innebär att hela dokumentet måste hämtas — men
    inte att det måste parsas och vektoriseras om, vilket är det dyra.

    Konsolideringsvalet görs om vid varje körning. Det är hela poängen: den
    dag en ny konsolidering träder i kraft ska korpuset följa med utan att
    någon rör en konfigurationsfil.
    """
    konfig = las_konfig()
    conn = db_conn or oppna_db(Path(konfig.index.db))
    stang_efter = db_conn is None
    start = time.perf_counter()

    try:
        rapporter = ingest_alla(
            generera_vektorer=generera_vektorer,
            hoppa_over_oforandrade=True,
            db_conn=conn,
        )
        fel = [r for r in rapporter if r["status"] == "fel"]
        oforandrade = [r for r in rapporter if r["status"] == "oforandrad"]
        omindexerade = [r for r in rapporter if r["status"] in ("ok", "anmarkning")]

        if fel and not omindexerade:
            status = "fel"
        elif fel:
            status = "delvis"
        else:
            status = "ok"

        resultat = {
            "status": status,
            "kontrollerade": len(rapporter),
            "oforandrade": len(oforandrade),
            "omindexerade": len(omindexerade),
            "fel": len(fel),
            "celex_omindexerade": [r["celex"] for r in omindexerade],
            "celex_fel": [r["celex"] for r in fel],
            "varaktighet_sek": round(time.perf_counter() - start, 1),
        }
        logger.info(
            "Nattlig EU-kontroll klar: %d kontrollerade, %d oförändrade, "
            "%d omindexerade, %d fel, status=%s",
            len(rapporter), len(oforandrade), len(omindexerade), len(fel), status,
        )
        return resultat
    finally:
        if stang_efter:
            conn.close()


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(description="EU-ingest (steg 24)")
    p.add_argument("--demo", action="store_true",
                   help="Bara redovisningsdirektivet och momsdirektivet")
    p.add_argument("--db", type=Path, default=None, help="Sökväg till SQLite-databasen")
    p.add_argument("--inga-vektorer", action="store_true", help="Hoppa över embeddings")
    p.add_argument("--endast", nargs="*", default=None, help="Bara dessa CELEX eller kortnamn")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        stream=sys.stdout)
    print(f"=== Startar EU-ingest (demo={args.demo}) ===")

    db_conn = oppna_db(args.db) if args.db else None
    rapporter = ingest_alla(
        generera_vektorer=not args.inga_vektorer,
        demo=args.demo,
        endast=args.endast,
        db_conn=db_conn,
    )

    print("\n--- Resultat ---")
    for r in rapporter:
        if r["status"] == "fel":
            print(f"  [{r['celex']}] {r.get('kortnamn')}: FEL - {r.get('fel')}")
            continue
        print(f"  [{r['celex']}] {r['kortnamn']}: {r['chunkar']} chunkar "
              f"({r.get('artiklar', 0)} artiklar) | lydelse {r['lydelse']} | "
              f"resurs {r['resurs']}")
        if r.get("framtida_konsolideringar"):
            print(f"      framtida (ej tillämpad): {r['framtida_konsolideringar']}")
        if r.get("anmarkning"):
            print(f"      {r['anmarkning']}")

    for lucka in euregister.luckor():
        print(f"  LUCKA: {lucka.identitet} — {lucka.skal[:100]}")
    print("\nEU-ingest slutförd.")


if __name__ == "__main__":
    main()
