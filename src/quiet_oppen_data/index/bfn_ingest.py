"""Ingest av BFN-korpuset (steg 23).

Läser de pdf:er skörden laddat ner, parsar dem till typade block och indexerar
dem i korpustabellerna (FTS5 + embeddings). Bygger inte om skörden — kör
`python -m quiet_oppen_data.index.bfn_skord` först.

Två regler styr vad som hamnar i indexet, och båda står i kallor/bfnregister.yaml
snarare än här:

  * `indexera_nivaer` avgör vilka dokument som får indexeras. BFN publicerar
    ~900 dokument varav de flesta är remisser och remissvar — alltså vad andra
    TYCKER om ett regelförslag, inte vad som gäller. Ett remissvar i indexet
    blir förr eller senare citerat som om det vore god redovisningssed, med en
    korrekt källänk till bfn.se, vilket gör felet svårare att upptäcka.
  * Blocktypen följer med hela vägen ut i svaret, så att ett bindande allmänt
    råd inte kan förväxlas med BFN:s kommentar till det.

Ett dokument som hämtats men inte kunnat läsas (inskannad pdf utan textlager)
skrivs ändå, med sin anmärkning, och rapporteras i `GET /matning`. Tyst
bortfall är inte godkänt.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from quiet_oppen_data import bfnregister
from quiet_oppen_data.index import bfn_parser, bfn_skord
from quiet_oppen_data.index.db import oppna_db
from quiet_oppen_data.index.korpus import KorpusChunk, KorpusDokument, skriv_dokument
from quiet_oppen_data.konfig import las as las_konfig
from quiet_oppen_data.register import Kalla, hamta

logger = logging.getLogger(__name__)

KORPUS = "bfn"
KALLA = bfn_skord.KALLA

# Demourvalet: de dokument som besvarar de vanligaste frågorna om löpande
# bokföring och årsredovisning. Motsvarar lag_ingest.DEMO_SFS.
DEMO_MONSTER = ("vl13-2-bokforing", "bfnar13-2-grund", "vl12-1-k3-kons",
                "vl16-10-k2ar-kons", "bfnar16-10-grund")


def _las_skorderegister() -> dict[str, Any]:
    fil = bfn_skord.registerfil()
    if not fil.exists():
        raise FileNotFoundError(
            f"{fil} saknas. Kör 'python -m quiet_oppen_data.index.bfn_skord' först — "
            "ingesten behöver kategorin och nivån, och de bor på webbplatsen, "
            "inte i pdf:en."
        )
    return json.loads(fil.read_text(encoding="utf-8"))


def _manniskolank(kalla: Kalla, bilaga: dict[str, Any]) -> str:
    """Klickbar sida hos BFN.

    Bilagans egen url ÄR pdf:en hos myndigheten och duger som människolänk —
    men om skörden vet vilken sida som länkade till den är den sidan en bättre
    ingång för en människa, eftersom den bär BFN:s sammanhang.
    """
    sidor = bilaga.get("sidor") or []
    if sidor:
        return f"{bfn_skord.BAS}/{sidor[0]}/"
    return bilaga.get("url") or (kalla.manniskolank_mall or bfn_skord.BAS)


def indexera_bilaga(
    bilaga: dict[str, Any],
    kalla: Kalla,
    conn: sqlite3.Connection,
    *,
    generera_vektorer: bool = True,
) -> dict[str, Any]:
    """Parsar och indexerar en nedladdad pdf. Returnerar en rapportrad."""
    pdf_fil = bfn_skord.bfn_rot() / (bilaga.get("lokal_fil") or "")
    dok_id = Path(bilaga["filnamn"]).stem

    dokument = bfn_parser.parsa(
        pdf_fil,
        dok_id=dok_id,
        titel=bilaga.get("titel") or dok_id,
        typ=bilaga.get("typ") or "okant",
        kategori=bilaga.get("kategori") or "ovrigt",
        niva=bilaga.get("niva") or "bakgrund",
        url=bilaga.get("url") or "",
        sha256=bilaga.get("sha256") or "",
    )

    chunkar = [
        KorpusChunk(
            beteckning=b.punkt,
            blocktyp=b.typ,
            text=b.text,
            kapitel_nr=b.kapitel_nr,
            kapitel_rubrik=b.kapitel_rubrik,
            avsnitt=b.avsnitt,
            sida=b.sida,
        )
        for b in dokument.block
    ]

    # Attributionen bär BFN:s villkor: "Ange alltid källa, BFN och datum."
    # Datumet är dokumentets egen uppdatering när den står på titelsidan,
    # annars dagen kopian togs — och vilket av de två det är får inte suddas
    # ut, därför skrivs det ut i klartext.
    if dokument.uppdaterad:
        datum = f"uppdaterad {dokument.uppdaterad}"
    else:
        datum = f"hämtad {dokument.hamtad[:10]}"
    attribution = (kalla.attribution or "Källa: Bokföringsnämnden (BFN), {datum}").replace(
        "{datum}", datum
    )

    kd = KorpusDokument(
        korpus=KORPUS,
        dok_id=dok_id,
        titel=dokument.titel,
        kortnamn=dokument.bfnar or None,
        utgivare=kalla.myndighet or "Bokföringsnämnden (BFN)",
        lydelse=dokument.uppdaterad,
        version=dokument.sha256,
        licens=kalla.licens,
        attribution=attribution,
        anmarkning=dokument.parsningsanmarkning,
        hamtad=dokument.hamtad,
        lank_manniska=_manniskolank(kalla, bilaga),
        lank_maskin=bilaga.get("url") or "",
        chunkar=chunkar,
    )
    antal = skriv_dokument(conn, kd, generera_vektorer=generera_vektorer)

    return {
        "dok_id": dok_id,
        "bfnar": dokument.bfnar,
        "titel": dokument.titel,
        "niva": dokument.niva,
        "kategori": dokument.kategori,
        "sidor": dokument.sidor,
        "kapitel": len(dokument.kapitel),
        "chunkar": antal,
        "uppdaterad": dokument.uppdaterad,
        "anmarkning": dokument.parsningsanmarkning,
        "status": "anmarkning" if dokument.parsningsanmarkning else "ok",
    }


def _familj(dok_id: str) -> str:
    """Konsolideringsfamiljen ett dokument tillhör.

    BFN namnger konsoliderade vägledningar med årtalet sist:
    `vl12-1-k3-kons2024` och `vl12-1-k3-kons20251215` är två lydelser av samma
    vägledning. Familjenyckeln är namnet utan den avslutande sifferföljden.
    """
    return re.sub(r"\d+$", "", dok_id)


def markera_ersatta(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Tar bort ersatta konsolideringar ur sökindexet.

    BFN publicerar den konsoliderade vägledningen på nytt vid varje ändring
    och låter de gamla ligga kvar. Efter en skörd finns därför både
    `vl12-1-k3-kons2024` och `vl12-1-k3-kons20251215` — samma vägledning i två
    lydelser, varav den ena är ersatt.

    Utan det här steget kan en fråga om K3 få 2024 års lydelse citerad som om
    den vore gällande, med en korrekt källänk till bfn.se. Det är exakt samma
    fel som att indexera en EU-rättsakts ursprungslydelse i stället för den
    konsoliderade (eu_ingest), och det ska hanteras likadant.

    Valet görs på **källans egen uppgift** — datumet "Uppdaterad ÅÅÅÅ-MM-DD"
    på titelsidan — inte på filnamnet. Ett dokument utan sådant datum deltar
    inte i jämförelsen: att rangordna på filnamn vore att gissa.

    Den ersatta lydelsen tas bort ur SÖKNINGEN men dokumentraden ligger kvar
    med sin anmärkning, av samma skäl som en inskannad pdf gör det: en
    medveten uteslutning ska synas i /matning, inte försvinna.
    """
    rader = conn.execute(
        "SELECT id, dok_id, kortnamn, lydelse FROM korpus_dokument WHERE korpus = ?",
        (KORPUS,),
    ).fetchall()

    familjer: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
    for pk, dok_id, kortnamn, lydelse in rader:
        if not lydelse:
            continue
        familjer.setdefault((_familj(dok_id), kortnamn or ""), []).append((pk, dok_id, lydelse))

    ersatta: list[dict[str, Any]] = []
    for (_familjenyckel, _kortnamn), medlemmar in familjer.items():
        if len(medlemmar) < 2:
            continue
        medlemmar.sort(key=lambda m: m[2])
        gallande = medlemmar[-1]
        for pk, dok_id, lydelse in medlemmar[:-1]:
            conn.execute("DELETE FROM korpus_chunk WHERE dokument_id = ?", (pk,))
            conn.execute("DELETE FROM korpus_chunk_fts WHERE id LIKE ?", (f"{pk}#%",))
            anm = (
                f"Ersatt lydelse. Gällande version är {gallande[1]} "
                f"(uppdaterad {gallande[2]}). Dokumentet är hämtat och kvar i "
                f"registret men ingår inte i sökningen."
            )
            conn.execute(
                "UPDATE korpus_dokument SET anmarkning = ? WHERE id = ?", (anm, pk)
            )
            ersatta.append({
                "dok_id": dok_id, "lydelse": lydelse,
                "ersatt_av": gallande[1], "ersatt_av_lydelse": gallande[2],
            })
            logger.info("%s (%s) är ersatt av %s (%s) — utesluten ur sökningen.",
                        dok_id, lydelse, gallande[1], gallande[2])
    conn.commit()
    return ersatta


def ingest_alla(
    generera_vektorer: bool = True,
    demo: bool = False,
    endast: list[str] | None = None,
    db_conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """Parsar och indexerar de nedladdade pdf:erna på tillåten nivå."""
    konfig = las_konfig()
    reg = bfnregister.las()
    kalla = hamta(KALLA)
    if not isinstance(kalla, Kalla):
        raise RuntimeError(f"Källan {KALLA} saknas eller är blockerad i källregistret.")

    register = _las_skorderegister()
    conn = db_conn or oppna_db(Path(konfig.index.db))
    stang_efter = db_conn is None

    try:
        resultat: list[dict[str, Any]] = []
        for b in register.get("bilagor") or []:
            if not b.get("lokal_fil"):
                continue
            if not reg.far_indexeras(b.get("niva") or "bakgrund"):
                continue
            if endast and Path(b["filnamn"]).stem not in endast:
                continue
            if demo and not any(m in b["filnamn"] for m in DEMO_MONSTER):
                continue
            pdf_fil = bfn_skord.bfn_rot() / b["lokal_fil"]
            if not pdf_fil.exists():
                logger.warning("%s saknas på disk trots registerpost.", pdf_fil)
                continue

            start_t = time.perf_counter()
            try:
                rad = indexera_bilaga(
                    b, kalla, conn, generera_vektorer=generera_vektorer
                )
                rad["tid_ms"] = round((time.perf_counter() - start_t) * 1000, 1)
                resultat.append(rad)
            except Exception as e:
                logger.error("Misslyckades indexera %s: %s", b["filnamn"], e, exc_info=True)
                resultat.append({
                    "dok_id": Path(b["filnamn"]).stem,
                    "titel": b.get("titel", ""),
                    "status": "fel",
                    "fel": str(e),
                })

        # Ersatta konsolideringar ut ur sökningen. Görs sist, när alla
        # lydelser är kända — valet kräver att man vet vilka som finns.
        for e in markera_ersatta(conn):
            resultat.append({
                "dok_id": e["dok_id"],
                "bfnar": "",
                "titel": "",
                "chunkar": 0,
                "status": "ersatt",
                "ersatt_av": e["ersatt_av"],
                "lydelse": e["lydelse"],
            })

        if demo:
            from quiet_oppen_data.index.db import satt_meta
            satt_meta(conn, "bfn_demo", "1")

        return resultat
    finally:
        if stang_efter:
            conn.close()


def nattlig_bfnkontroll(
    generera_vektorer: bool = True,
    db_conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Nattlig färskhetskontroll av BFN-korpuset (motsvarar steg 19 för SFS).

    Räknar upp mediabiblioteket — tio lätta anrop, ingen omparsning i onödan —
    och jämför varje indexerat dokuments `modified` mot det som stod i förra
    skördens register. Bara de ändrade laddas ner på nytt, och bara de vars
    sha256 faktiskt skiljer sig indexeras om.

    Två steg i stället för ett, av samma skäl som lagkontrollen läser
    systemdatum före texten: BFN byter ofta `modified` utan att filen ändras
    (omsparad i mediabiblioteket), och en omparsning av K3 kostar en minut.

    Misslyckas uppräkningen helt avslutas körningen med `status="fel"` i
    stället för att tyst rapportera att ingenting ändrats.
    """
    konfig = las_konfig()
    reg = bfnregister.las()
    kalla = hamta(KALLA)
    if not isinstance(kalla, Kalla):
        raise RuntimeError(f"Källan {KALLA} saknas eller är blockerad i källregistret.")

    conn = db_conn or oppna_db(Path(konfig.index.db))
    stang_efter = db_conn is None
    start = time.perf_counter()

    try:
        register = _las_skorderegister()
        indexerbara = {
            b["filnamn"]: b
            for b in (register.get("bilagor") or [])
            if b.get("lokal_fil") and reg.far_indexeras(b.get("niva") or "bakgrund")
        }

        try:
            media = bfn_skord.hamta_mediabibliotek()
        except Exception as e:
            logger.error("Nattlig BFN-kontroll: uppräkningen misslyckades: %s", e, exc_info=True)
            return {
                "status": "fel",
                "kontrollerade": 0,
                "andrade": 0,
                "omindexerade": 0,
                "fel": str(e),
                "varaktighet_sek": round(time.perf_counter() - start, 1),
            }

        andrad_per_fil = {
            (m.get("source_url") or "").split("?")[0].rsplit("/", 1)[-1]: m.get("modified")
            for m in media
            if m.get("source_url")
        }

        misstankta = []
        for filnamn, b in indexerbara.items():
            fjarr = andrad_per_fil.get(filnamn)
            if fjarr is None:
                # Filen finns i vårt register men inte i mediabiblioteket.
                # Det är inte samma sak som "oförändrad" och ska inte tystas.
                misstankta.append((filnamn, b, None, "saknas_i_mediabiblioteket"))
            elif fjarr != (b.get("andrad") or ""):
                misstankta.append((filnamn, b, fjarr, "andrad"))

        omindexerade: list[str] = []
        oforandrat_innehall: list[str] = []
        fel: list[dict[str, str]] = []
        saknade: list[str] = []

        for filnamn, b, fjarr, skal in misstankta:
            if skal == "saknas_i_mediabiblioteket":
                saknade.append(filnamn)
                continue
            bilaga = bfn_skord.Bilaga(
                filnamn=filnamn,
                url=b.get("url") or "",
                titel=b.get("titel") or filnamn,
                kategori=b.get("kategori") or "ovrigt",
                typ=b.get("typ") or "okant",
                niva=b.get("niva") or "bakgrund",
                sidor=list(b.get("sidor") or []),
                url_alternativ=list(b.get("url_alternativ") or []),
            )
            try:
                bfn_skord.ladda_ner({filnamn: bilaga}, reg, tvinga=True)
            except Exception as e:
                fel.append({"filnamn": filnamn, "fel": str(e)})
                continue
            if bilaga.status != "hamtad":
                fel.append({"filnamn": filnamn, "fel": bilaga.fel or bilaga.status})
                continue

            b["sha256"] = bilaga.sha256
            b["andrad"] = fjarr
            b["storlek_byte"] = bilaga.storlek_byte
            b["hamtad"] = bilaga.hamtad

            from quiet_oppen_data.index.korpus import hamta_version
            if hamta_version(conn, KORPUS, Path(filnamn).stem) == bilaga.sha256:
                # `modified` ändrades men innehållet inte. Vanligt när BFN
                # sparar om en fil i mediabiblioteket.
                oforandrat_innehall.append(filnamn)
                continue

            try:
                indexera_bilaga(b, kalla, conn,
                                generera_vektorer=generera_vektorer)
                omindexerade.append(filnamn)
            except Exception as e:
                logger.error("Omindexering av %s misslyckades: %s", filnamn, e, exc_info=True)
                fel.append({"filnamn": filnamn, "fel": str(e)})

        # Skörderegistret uppdateras så att nästa körning jämför mot det som
        # faktiskt ligger på disk.
        register["kontrollerad"] = datetime.now(UTC).isoformat(timespec="seconds")
        bfn_skord.registerfil().write_text(
            json.dumps(register, ensure_ascii=False, indent=1), encoding="utf-8"
        )

        if fel:
            status = "fel"
        elif saknade:
            status = "delvis"
        else:
            status = "ok"

        resultat = {
            "status": status,
            "kontrollerade": len(indexerbara),
            "andrade": len([m for m in misstankta if m[3] == "andrad"]),
            "omindexerade": len(omindexerade),
            "oforandrat_innehall": len(oforandrat_innehall),
            "saknas_i_mediabiblioteket": len(saknade),
            "fel": len(fel),
            "filer_omindexerade": omindexerade,
            "filer_saknade": saknade,
            "filer_fel": fel,
            "varaktighet_sek": round(time.perf_counter() - start, 1),
        }
        logger.info(
            "Nattlig BFN-kontroll klar: %d kontrollerade, %d ändrade, %d omindexerade, "
            "%d oförändrat innehåll, %d saknade, %d fel, status=%s",
            len(indexerbara), resultat["andrade"], len(omindexerade),
            len(oforandrat_innehall), len(saknade), len(fel), status,
        )
        return resultat
    finally:
        if stang_efter:
            conn.close()


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(description="BFN-ingest (steg 23)")
    p.add_argument("--demo", action="store_true",
                   help="Indexera bara K2, K3 och vägledningen Bokföring")
    p.add_argument("--db", type=Path, default=None, help="Sökväg till SQLite-databasen")
    p.add_argument("--inga-vektorer", action="store_true", help="Hoppa över embeddings")
    p.add_argument("--endast", nargs="*", default=None, help="Bara dessa dokument-id")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        stream=sys.stdout)
    print(f"=== Startar BFN-ingest (demo={args.demo}) ===")

    db_conn = oppna_db(args.db) if args.db else None
    rapporter = ingest_alla(
        generera_vektorer=not args.inga_vektorer,
        demo=args.demo,
        endast=args.endast,
        db_conn=db_conn,
    )

    print("\n--- Resultat ---")
    chunkar = 0
    for r in rapporter:
        if r["status"] == "fel":
            print(f"  [{r['dok_id']}] FEL - {r.get('fel')}")
            continue
        if r["status"] == "ersatt":
            print(f"  [{r['dok_id']}] ERSATT av {r['ersatt_av']} "
                  f"(lydelse {r['lydelse']}) — utesluten ur sökningen")
            continue
        chunkar += r.get("chunkar", 0)
        markering = "  (ANMÄRKNING)" if r["status"] == "anmarkning" else ""
        print(f"  [{r['dok_id']}] {r.get('bfnar') or '-'} | {r['chunkar']} chunkar | "
              f"{r['kapitel']} kapitel | {r['sidor']} sidor{markering}")
        if r.get("anmarkning"):
            print(f"      {r['anmarkning']}")
    print(f"\nBFN-ingest slutförd: {len(rapporter)} dokument, {chunkar} chunkar.")


if __name__ == "__main__":
    main()
