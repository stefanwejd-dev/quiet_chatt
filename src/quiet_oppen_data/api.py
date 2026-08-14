"""HTTP-API — FastAPI-applikationen (steg 13).

Tre endpoints:
  * POST /fraga  — kör fas A→B→C och strömmar svaret som SSE.
  * GET  /kallor — källregistret, publikt, utan spärrade poster.
  * GET  /halsa  — hälsokontroll (används av Coolify) + per-källa-statistik.

Designbeslut:
  * `/halsa` svarar alltid HTTP 200 med "status": "ok" så länge processen
    lever — det är kontraktet drift förlitar sig på (se PLAN.md, frågor
    till beställaren #5). Per-källa-statistiken är ett extra fält i samma
    svar, inte ett separat kontrakt.
  * Fas A och fas C instansieras lat och en gång per process (samma
    motivering som i motor/hamtning.py och motor/syntes.py: prompt-cachen
    kräver återanvändning). De skapas INTE vid import — /kallor och /halsa
    ska fungera även utan ANTHROPIC_API_KEY i miljön.
  * Kvoten kontrolleras och räknas upp INNAN någon modell anropas —
    fail-closed. Ett avvisat anrop kostar ingenting.
  * CORS-allowlisten byggs ur `config.toml → site.domain` (ARKITEKTUR.md §0).
    Utöver webbläsarens CORS-huridering (som bara skyddar mot att JS i en
    webbläsare LÄSER svaret) avvisar /fraga uttryckligen alla anrop som
    bär en Origin-header utanför allowlistan — annars är CORS bara kosmetik
    mot ett direkt curl/skript-anrop, som inte bryr sig om
    Access-Control-Allow-Origin.
  * API-nyckeln läses ur miljön via konfig.py — den skickas aldrig till
    klienten och loggas aldrig (samma disciplin som motor/hamtning.py och
    motor/syntes.py håller redan).
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from typing import Any
from collections.abc import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from quiet_oppen_data import kvot, matning
from quiet_oppen_data.adaptrar.transport import halsostatistik
from quiet_oppen_data.konfig import las as las_konfig
from quiet_oppen_data.modeller import Faktapost, Faktaregister
from quiet_oppen_data.register import Kalla, Sparrad, las as las_register

logger = logging.getLogger(__name__)

app = FastAPI(title="Quiet Öppen Data", version="0.1.0")


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

def _tillatna_ursprung() -> list[str]:
    domain = las_konfig().site.domain
    return [f"https://{domain}", f"https://www.{domain}"]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_tillatna_ursprung(),
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


def _kontrollera_ursprung(request: Request) -> None:
    """Avvisar explicit anrop från en Origin utanför allowlistan.

    CORSMiddleware ovan styr bara vilka headers webbläsaren får se — den
    stoppar inget på servern. Den här kontrollen gör det, för /fraga
    specifikt (den enda endpointen som kostar pengar per anrop). Saknas
    Origin-headern helt (curl, server-till-server) släpps anropet igenom;
    det är kvoten som är det egentliga skyddet mot missbruk där.
    """
    ursprung = request.headers.get("origin")
    if ursprung is not None and ursprung not in _tillatna_ursprung():
        logger.warning("Avvisat anrop från otillåtet ursprung: %s", ursprung)
        raise HTTPException(status_code=403, detail="Ursprunget är inte tillåtet.")


def _kontrollera_matningsnyckel(request: Request) -> None:
    """Skyddar /matning med en delad nyckel ur miljön.

    Mätvyn läcker ingen frågetext — bara aggregat — men den avslöjar
    trafikvolym, vilka källor som fallerar och hur ofta fas C faller stängt.
    Det är driftdata, inte publikt innehåll, till skillnad från /kallor och
    /halsa som är avsiktligt öppna.

    Saknas MATNING_NYCKEL i miljön är endpointen helt stängd (503) i stället för
    öppen. Fail-closed: en glömd variabel ska inte tyst göra driftdata publik.
    """
    forvantad = os.environ.get("MATNING_NYCKEL")
    if not forvantad:
        logger.warning("MATNING_NYCKEL är inte satt — /matning är stängd.")
        raise HTTPException(
            status_code=503,
            detail="Mätvyn är inte konfigurerad. Sätt MATNING_NYCKEL i miljön.",
        )
    if not secrets.compare_digest(request.headers.get("x-matning-nyckel", ""), forvantad):
        raise HTTPException(status_code=401, detail="Ogiltig eller saknad nyckel.")


# ---------------------------------------------------------------------------
# Fas A/C — lat, per-process singleton (se moduldocstringen)
# ---------------------------------------------------------------------------

_fas_a: Any = None
_fas_c: Any = None


def _motorer():
    """Bygger (eller återanvänder) FasALopp och FasCValidator.

    Import av motor.hamtning/.syntes/.validator sker här, inte i toppen av
    filen — de kräver ANTHROPIC_API_KEY vid instansiering, och den
    importen ska inte krascha /kallor eller /halsa när nyckeln saknas.
    """
    global _fas_a, _fas_c
    if _fas_a is None or _fas_c is None:
        from quiet_oppen_data.motor.hamtning import FasALopp
        from quiet_oppen_data.motor.syntes import FasBSyntes
        from quiet_oppen_data.motor.validator import FasCValidator

        _fas_a = FasALopp()
        _fas_c = FasCValidator(syntes=FasBSyntes())
    return _fas_a, _fas_c


# ---------------------------------------------------------------------------
# GET /halsa
# ---------------------------------------------------------------------------

@app.get("/halsa")
async def halsa() -> dict[str, Any]:
    """Hälsokontroll. Svarar alltid 200 om processen lever — se moduldocstringen.

    Per källa: senaste lyckade (icke-cachade) anrop och cache-träffkvot.
    """
    try:
        statistik = await run_in_threadpool(halsostatistik)
    except Exception:
        logger.warning("Kunde inte läsa hälsostatistik", exc_info=True)
        statistik = {}

    kallor: dict[str, Any] = {}
    try:
        for post in las_register():
            if isinstance(post, Kalla):
                kallor[post.id] = statistik.get(
                    post.id,
                    {
                        "senaste_lyckade_anrop": None,
                        "cache_traffar": 0,
                        "cache_missar": 0,
                        "cache_traffkvot": None,
                    },
                )
    except Exception:
        logger.warning("Kunde inte läsa källregistret för /halsa", exc_info=True)

    return {"status": "ok", "kallor": kallor}


# ---------------------------------------------------------------------------
# GET /kallor
# ---------------------------------------------------------------------------

@app.get("/matning")
async def matning_endpoint(request: Request) -> dict[str, Any]:
    """Aggregerade mätpunkter för de senaste 30 dagarna.

    Den viktigaste siffran är `niva3_andel` — andelen frågor som besvarades
    med katalogsvar (dataportal) i stället för ett exekverat adapter-svar.
    Det är den siffran som styr vilken adapter som ska byggas härnäst
    (ARKITEKTUR.md §11).

    `lagkorpus_alder` visar, per författning, dygn sedan senaste lyckade
    ingest och om den ligger efter (steg 19, §5 regel 8).
    """
    _kontrollera_matningsnyckel(request)

    try:
        punkter = await run_in_threadpool(matning.las_matpunkter, 30)
        senaste_ingest = await run_in_threadpool(matning.las_senaste_ingest)
    except Exception:
        logger.warning("Kunde inte läsa mätpunkter", exc_info=True)
        punkter = {"fel": "Kunde inte läsa mätpunkter."}
        senaste_ingest = None

    # Lagkorpusets ålder (steg 19, ARKITEKTUR.md §5 regel 8). Läses live ur
    # indexet, inte ur mätningsdatabasen — åldern ska alltid spegla
    # indexets faktiska tillstånd, inte en tidigare körnings ögonblicksbild.
    try:
        from quiet_oppen_data.index.lag_ingest import las_lagkorpus_alder
        lagkorpus_alder = await run_in_threadpool(las_lagkorpus_alder)
        senaste_lagkontroll = await run_in_threadpool(matning.las_senaste_lagkontroll)
    except Exception:
        logger.warning("Kunde inte läsa lagkorpusets ålder", exc_info=True)
        lagkorpus_alder = {"fel": "Kunde inte läsa lagkorpusets ålder."}
        senaste_lagkontroll = None

    return {
        "matpunkter": punkter,
        "senaste_ingest": senaste_ingest,
        "lagkorpus_alder": lagkorpus_alder,
        "senaste_lagkontroll": senaste_lagkontroll,
    }


@app.get("/kallor")
async def kallor_endpoint() -> dict[str, Any]:
    """Källregistret, publikt. Spärrade källor (Sparrad) tas bort helt —
    de ska inte ens synas som existerande."""
    poster = await run_in_threadpool(las_register)

    resultat = []
    for post in poster:
        if isinstance(post, Sparrad):
            continue
        if isinstance(post, Kalla):
            resultat.append({
                "id": post.id,
                "myndighet": post.myndighet,
                "adapter": post.adapter,
                "verifierad": post.verifierad,
                "aktiverad": post.aktiverad,
                "licens": post.licens,
                "attribution": post.attribution,
            })
        else:  # EjAnvandbar
            resultat.append({"id": post.id, "verifierad": False, "aktiverad": False})

    return {"kallor": resultat}


# ---------------------------------------------------------------------------
# POST /fraga
# ---------------------------------------------------------------------------

class FragaBegaran(BaseModel):
    fraga: str


def _klient_ip(request: Request) -> str:
    """Klientens IP, för per-IP-kvoten.

    X-Forwarded-For sätts av vem som helst som kan nå porten. Litar vi alltid
    på den blir per-IP-kvoten verkningslös: en angripare skickar en ny slumpad
    adress per anrop och får obegränsat antal frågor, bara begränsat av
    dygnstotalen. Headern läses därför bara när `site.betrodd_proxy` är satt,
    vilket den ska vara bakom Coolify/Traefik och inte vara om appen exponeras
    direkt.
    """
    if las_konfig().site.betrodd_proxy:
        vidarebefordrad = request.headers.get("x-forwarded-for")
        if vidarebefordrad:
            return vidarebefordrad.split(",")[0].strip()
    return request.client.host if request.client else "okänd"


def _sse(handelse: str, data: dict[str, Any]) -> str:
    return f"event: {handelse}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _kallpanel(register: Faktaregister, citerade_id: set[str]) -> list[dict[str, Any]]:
    panel = []
    for fid in sorted(citerade_id, key=lambda x: int(x[1:]) if x[1:].isdigit() else 0):
        post: Faktapost | None = register.hamta(fid)
        if post is None:
            continue
        panel.append({
            "id": post.id,
            "etikett": post.etikett,
            "myndighet": post.myndighet,
            "dataset": post.dataset,
            "period": post.period,
            "dimensioner": post.dimensioner,
            "hamtad": post.hamtad.isoformat(),
            "lank_manniska": post.lank_manniska,
            "lank_maskin": post.lank_maskin,
            "licens": post.licens,
            "attribution": post.attribution,
            "harledd": post.harledd,
            "harledd_av": list(post.harledd_av),
        })
    return panel


async def _strom_svar(fraga: str) -> AsyncIterator[str]:
    fas_a, fas_c = _motorer()

    try:
        hamtningsresultat = await run_in_threadpool(fas_a.hamta, fraga)
        svar = await run_in_threadpool(fas_c.kor, fraga, hamtningsresultat.register)
    except Exception:
        logger.warning("Fas A/B/C misslyckades för en fråga", exc_info=True)
        yield _sse("fel", {"meddelande": "Ett tekniskt fel inträffade. Försök igen."})
        return

    # --- Mätning (ARKITEKTUR.md §11) ---
    # fas_c_forsok: 1=godkänt direkt, 0=fail-closed. Validator-koden loggar
    # varningsmeddelanden vid omförsök, men returnerar alltid ett SyntesSvar.
    # Vi approximerar försöksantalet: om kan_besvaras=False och stycken är
    # tomma har fail-closed inträffat (0), annars godkänt (1).
    # En exakt räknare kräver ändring i FasCValidator — se nedan.
    fas_c_forsok = 0 if (not svar.kan_besvaras and not svar.stycken) else 1
    anvanda_kallor = list({
        p.kalla_id for p in hamtningsresultat.register.alla()
    })
    try:
        await run_in_threadpool(
            matning.logga_fraga,
            fraga_text=fraga,
            kan_besvaras=svar.kan_besvaras,
            fas_c_forsok=fas_c_forsok,
            antal_faktaposter=len(hamtningsresultat.register),
            anvanda_kallor=anvanda_kallor,
            token_fas_a_in=hamtningsresultat.input_tokens,
            token_fas_a_ut=hamtningsresultat.output_tokens,
            token_fas_a_cache_read=hamtningsresultat.cache_read_tokens,
            token_fas_a_cache_write=hamtningsresultat.cache_write_tokens,
        )
    except Exception:
        logger.warning("Mätning: logga_fraga misslyckades", exc_info=True)
        # Loggningsfel är icke-fatala — svaret blockeras aldrig
    # --- Slut mätning ---

    if not svar.kan_besvaras:
        yield _sse("svar", {
            "kan_besvaras": False,
            "forbehall": svar.forbehall or "Det hittade jag inte i källorna.",
        })
        yield _sse("klart", {})
        return

    citerade: set[str] = set()
    for stycke in svar.stycken:
        citerade.update(stycke.kallor)
        yield _sse("stycke", {"text": stycke.text, "kallor": list(stycke.kallor)})

    yield _sse("kallor", {"kallor": _kallpanel(hamtningsresultat.register, citerade)})

    if svar.attribution:
        yield _sse("attribution", {"attribution": list(svar.attribution)})
    if svar.forbehall:
        yield _sse("forbehall", {"forbehall": svar.forbehall})

    yield _sse("klart", {})


@app.post("/fraga")
async def fraga(begaran: FragaBegaran, request: Request):
    _kontrollera_ursprung(request)

    ip = _klient_ip(request)
    try:
        beslut = await run_in_threadpool(kvot.kontrollera_och_rakna, ip)
    except Exception:
        # Fail-closed: går kvotkontrollen inte att lita på, avvisas anropet
        # hellre än att kvoten kringgås tyst.
        logger.warning("Kvotkontroll misslyckades — avvisar fail-closed", exc_info=True)
        # from None: internfelet är redan loggat med exc_info och ska inte
        # läcka till klienten.
        raise HTTPException(
            status_code=503, detail="Tjänsten är tillfälligt otillgänglig."
        ) from None

    if not beslut.tillaten:
        raise HTTPException(status_code=429, detail=beslut.meddelande)

    return StreamingResponse(_strom_svar(begaran.fraga), media_type="text/event-stream")
