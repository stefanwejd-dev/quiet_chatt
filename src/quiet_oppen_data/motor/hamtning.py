"""Fas A — Planerare och hämtningsloop.

Tar en frihandsfråga, kör Claude med adaptrarnas verktyg i en agent-loop, och
returnerar ett Faktaregister. Fas A:s textutgång kastas — det enda som förs
vidare till syntesfasen är registret.

Designbeslut (se ARKITEKTUR.md §4 och PLAN.md §9):
  * Verktygsdefinitioner byggs i deterministisk ordning (sorterade på id) —
    annars slås prompt-cachen sönder vid varje ny adapter.
  * Systemprompten är frusen: ingen tidsstämpel, inget sessions-id, inget IP.
    Dynamiskt innehåll läggs i användarmeddelandena.
  * Cache-brytpunkt (cache_control: {"type": "ephemeral"}) sätts på sista
    verktyget och på sista systemblocket. Renderingsordningen är
    tools → system → messages, så verktygen cachas i den första brytpunkten
    och systemprompten i den andra.
  * Thinking: {"type": "adaptive"} + output_config: {"effort": ...} ur config.
    INGET budget_tokens — det är borttaget på Opus 5 och ger HTTP 400.
    INGA beta-flaggor — caching och thinking är GA; de gamla flaggorna ger 400.
  * Loopen är handskriven för att kunna samla usage per varv.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import anthropic

from quiet_oppen_data.adaptrar import (
    DataportalAdapter,
    JsonRestAdapter,
    KoladaAdapter,
    LagtextAdapter,
    PxWebAdapter,
    RiksdagenAdapter,
    RiksbankenAdapter,
    RowStoreAdapter,
    SkatteverketVagledningAdapter,
    TedAdapter,
    ViesAdapter,
)
from quiet_oppen_data.konfig import las as las_konfig
from quiet_oppen_data.modeller import Faktapost, Faktaregister, Faktautkast, Fragplan
from quiet_oppen_data.motor import berakningar

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Systemprompten — FRUSEN (ingen tidsstämpel, inget sessions-id)
# ---------------------------------------------------------------------------

_SYSTEMPROMPT = """\
Du är en faktaassistent som svarar på frågor om Sverige med hjälp av officiella \
öppna datakällor. Du har tillgång till verktyg som hämtar data i realtid från \
myndigheter och EU-institutioner.

Regler:
1. Använd ALLTID minst ett datahämtningsverktyg innan du svarar.
2. Hämta bara det du behöver — starta med den mest specifika källan.
3. Om ett verktyg returnerar valalternativ (dimensioner saknas), välj rätt \
alternativ och hämta igen.
4. Avsluta loopen när du har tillräckliga Faktaposter för att besvara frågan, \
eller när det är uppenbart att ingen källa kan svara.
5. Räkna ALDRIG själv — inte ens en enkel differens eller procentsats. Om \
frågan kräver en beräkning på hämtade Faktaposter, använd något av \
verktygen berakna_differens, berakna_procentuell_forandring, berakna_kvot \
eller berakna_indexupprakning. De returnerar en ny Faktapost med \
beräkningen redan gjord och spårbar.
6. Din text i denna fas kastas — skriv ingenting till användaren här. \
Kommunicera enbart via verktygskall.
"""

# ---------------------------------------------------------------------------
# Adapterns instansering
# ---------------------------------------------------------------------------

def _bygg_adaptrar() -> dict[str, Any]:
    """Instansierar alla aktiva adaptrar. Returnerar {kalla_id: adapter}."""
    adaptrar: dict[str, Any] = {}

    kandidater = [
        ("riksbanken", lambda: RiksbankenAdapter()),
        ("vies", lambda: ViesAdapter()),
        ("scb_pxweb", lambda: PxWebAdapter("scb_pxweb")),
        ("ted", lambda: TedAdapter()),
        ("riksdagen", lambda: RiksdagenAdapter()),
        ("lagtext", lambda: LagtextAdapter()),
        ("kolada", lambda: KoladaAdapter()),
        ("dataportal", lambda: DataportalAdapter()),
        ("skatteverket_rowstore", lambda: RowStoreAdapter("skatteverket_rowstore")),
        ("skatteverket_vagledning", lambda: SkatteverketVagledningAdapter()),
        ("kronofogden_rowstore", lambda: RowStoreAdapter("kronofogden_rowstore")),
        ("smhi", lambda: JsonRestAdapter("smhi")),
        ("skolverket", lambda: JsonRestAdapter("skolverket")),
        ("trafa", lambda: JsonRestAdapter("trafa")),
        ("polisen_handelser", lambda: JsonRestAdapter("polisen_handelser")),
        ("jobtech", lambda: JsonRestAdapter("jobtech")),
    ]

    for kalla_id, fabrik in kandidater:
        try:
            adaptrar[kalla_id] = fabrik()
        except Exception:
            logger.warning("Kunde inte instansiera adapter '%s' — hoppas över", kalla_id, exc_info=True)

    return adaptrar


# ---------------------------------------------------------------------------
# Verktygsdefinitioner och -dispatcher
# ---------------------------------------------------------------------------

def _bygg_verktygsspecar(adaptrar: dict[str, Any]) -> list[dict[str, Any]]:
    """Bygg deterministisk lista med verktygsspecar från alla adaptrar,
    plus beräkningsverktygen (motor/berakningar.py — ARKITEKTUR.md §5 regel 2).

    Sorteras på verktygetsnamn ('name') för stabil prompt-cacheträff.
    """
    specs: list[dict[str, Any]] = list(berakningar.VERKTYGSSPECAR)
    for kalla_id in sorted(adaptrar.keys()):
        adapter = adaptrar[kalla_id]
        try:
            specs.extend(adapter.beskriv())
        except Exception:
            logger.warning("beskriv() misslyckades för '%s'", kalla_id, exc_info=True)

    # Sortera slutligen på verktygetsnamn (adaptrar med flera verktyg)
    return sorted(specs, key=lambda s: s.get("name", ""))


def _bygg_dispatcher(adaptrar: dict[str, Any]) -> dict[str, Any]:
    """Bygg ett {verktygsnamn → adapter}-index för verktygsdispacthcing."""
    dispatcher: dict[str, Any] = {}
    for kalla_id, adapter in adaptrar.items():
        try:
            for spec in adapter.beskriv():
                namn = spec.get("name", "")
                if namn:
                    dispatcher[namn] = adapter
        except Exception:
            # Tyst svälj är förbjudet enligt kontraktet i PLAN.md — en adapter
            # som tappas här försvinner annars spårlöst ur verktygslistan.
            logger.warning("beskriv() misslyckades för '%s'", kalla_id, exc_info=True)
    return dispatcher


# ---------------------------------------------------------------------------
# Verktygskörning — anropas av SDK:n via BetaRunnableTool
# ---------------------------------------------------------------------------

@dataclass
class _VerktygKontext:
    """Kontextobjekt som delas mellan verktygskallarna och loopen."""
    dispatcher: dict[str, Any]
    register: Faktaregister


def _formatera_poster(poster: list[Faktapost]) -> list[dict[str, Any]]:
    """Kompakt representation av Faktaposter för Claude — inte råa API-svar."""
    resultat = []
    for post in poster:
        item: dict[str, Any] = {
            "id": post.id,
            "etikett": post.etikett,
            "varde": post.varde,
        }
        if post.enhet:
            item["enhet"] = post.enhet
        if post.period:
            item["period"] = post.period
        if post.dimensioner:
            item["dimensioner"] = post.dimensioner
        if post.harledd:
            item["harledd_av"] = list(post.harledd_av)
        resultat.append(item)
    return resultat


def _kör_verktyg(kontext: _VerktygKontext, verktygsnamn: str, indata: dict[str, Any]) -> str:
    """Kör ett verktyg och registrerar svaret i Faktaregistret.

    Returnerar en JSON-sträng som Claude ser som verktygsresultat.
    """
    if verktygsnamn in berakningar.VERKTYGSNAMN:
        # Beräkningsverktygen (ARKITEKTUR.md §5 regel 2) registrerar redan
        # sitt resultat själva — de arbetar direkt mot Faktaregistret, inte
        # via Faktautkast + registrera_alla som adaptrarna.
        try:
            post = berakningar.kor_verktyg(verktygsnamn, kontext.register, indata)
        except (ValueError, ZeroDivisionError, KeyError) as exc:
            logger.info("Beräkningsverktyg '%s' avvisades: %s", verktygsnamn, exc)
            return json.dumps({"fel": str(exc)}, ensure_ascii=False)
        except Exception:
            logger.warning("Beräkningsverktyg '%s' kastade undantag", verktygsnamn, exc_info=True)
            return json.dumps({"fel": f"Verktyget {verktygsnamn} returnerade ett fel."})

        return json.dumps({"resultat": _formatera_poster([post]), "antal": 1}, ensure_ascii=False)

    adapter = kontext.dispatcher.get(verktygsnamn)
    if adapter is None:
        logger.warning("Okänt verktyg begärt: '%s'", verktygsnamn)
        return json.dumps({"fel": f"Okänt verktyg: {verktygsnamn}"})

    # Sätt verktygsnamnet i extra så adaptern kan dispatcha internt
    indata_med_namn = {"verktyg": verktygsnamn, "name": verktygsnamn, **indata}
    plan = Fragplan(fraga="", extra=indata_med_namn)

    try:
        utkast: list[Faktautkast] = adapter.hamta(plan)
    except Exception:
        logger.warning("Verktyg '%s' kastade undantag", verktygsnamn, exc_info=True)
        return json.dumps({"fel": f"Verktyget {verktygsnamn} returnerade ett fel."})

    if not utkast:
        return json.dumps({"resultat": [], "meddelande": "Inga data hittades för dessa parametrar."})

    poster: list[Faktapost] = kontext.register.registrera_alla(utkast)

    return json.dumps({"resultat": _formatera_poster(poster), "antal": len(poster)}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Publik API
# ---------------------------------------------------------------------------

@dataclass
class HamtningsResultat:
    """Resultatet av en Fas A-körning."""
    register: Faktaregister
    cache_read_tokens: int   # Bevis på att prompt-cachen träffar
    cache_write_tokens: int
    input_tokens: int
    output_tokens: int
    iterationer: int


class FasALopp:
    """Fas A: planerare och hämtningsloop.

    Instansieras en gång (per process) och återanvänds för varje fråga.
    Adaptrar byggs vid instansiering och hålls i minnet.
    """

    def __init__(self) -> None:
        konfig = las_konfig()
        if not konfig.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY är inte satt i miljön.")

        self._klient = anthropic.Anthropic(api_key=konfig.anthropic_api_key)
        self._konfig = konfig
        self._adaptrar = _bygg_adaptrar()
        self._dispatcher = _bygg_dispatcher(self._adaptrar)
        self._verktygsspecar = _bygg_verktygsspecar(self._adaptrar)

        logger.info(
            "FasALopp initierad: %d adaptrar, %d verktyg",
            len(self._adaptrar),
            len(self._verktygsspecar),
        )

    def hamta(self, fraga: str) -> HamtningsResultat:
        """Kör planeringsloopen för en fråga och returnerar Faktaregistret.

        Args:
            fraga: Användarens frihandsfråga på svenska.

        Returns:
            HamtningsResultat med Faktaregister och tokenräkning.
        """
        register = Faktaregister()
        kontext = _VerktygKontext(
            dispatcher=self._dispatcher,
            register=register,
        )

        # Bygg systemblocket med cache-brytpunkt på sista blocket
        systemblock = [
            {
                "type": "text",
                "text": _SYSTEMPROMPT,
                "cache_control": {"type": "ephemeral"},  # Cache-brytpunkt
            }
        ]

        # Bygg verktygslistan med cache_control på sista verktyget
        verktyg_med_cache: list[dict[str, Any]] = []
        for i, spec in enumerate(self._verktygsspecar):
            t: dict[str, Any] = {
                "name": spec["name"],
                "description": spec.get("description", ""),
                "input_schema": spec.get("input_schema", {"type": "object", "properties": {}}),
            }
            if i == len(self._verktygsspecar) - 1:
                # Sista verktyget bär cache-brytpunkten
                t["cache_control"] = {"type": "ephemeral"}
            verktyg_med_cache.append(t)

        konfig = self._konfig.modell
        iterationer = 0
        cache_read = 0
        cache_write = 0
        tot_in = 0
        tot_ut = 0

        # Kör agent-loop manuellt för att ha full kontroll och kunna
        # fånga usage-statistik per varv
        meddelanden: list[dict[str, Any]] = [
            {"role": "user", "content": fraga}
        ]

        for _ in range(konfig.max_verktygsvarv):
            iterationer += 1

            # Anropsformen är verifierad mot API:t 2026-08-13. Två fällor:
            #   * budget_tokens är BORTTAGET på Opus 5 och ger HTTP 400.
            #     Djupet styrs med output_config.effort, inte med ett tokentak.
            #   * Prompt-caching och thinking är GA. Beta-flaggorna
            #     "extended-thinking-*" och "prompt-caching-*" finns inte längre
            #     och ger också 400 — därför vanliga messages.stream, inte beta.
            with self._klient.messages.stream(
                model=konfig.namn,
                max_tokens=konfig.max_tokens_hamtning,
                system=systemblock,
                messages=meddelanden,
                tools=verktyg_med_cache,
                thinking={"type": "adaptive"},
                output_config={"effort": konfig.effort_hamtning},
            ) as ström:
                svar = ström.get_final_message()

            # Samla tokenstatistik
            if hasattr(svar, "usage") and svar.usage:
                u = svar.usage
                cache_read += getattr(u, "cache_read_input_tokens", 0) or 0
                cache_write += getattr(u, "cache_creation_input_tokens", 0) or 0
                tot_in += getattr(u, "input_tokens", 0) or 0
                tot_ut += getattr(u, "output_tokens", 0) or 0

            # Opus 5 kan avböja med stop_reason "refusal". Då är content tomt
            # eller partiellt — kontrollera stop_reason innan det läses.
            if svar.stop_reason == "refusal":
                kategori = getattr(getattr(svar, "stop_details", None), "category", None)
                logger.warning(
                    "Fas A avbröts: modellen avböjde frågan (kategori=%s)", kategori
                )
                break

            # Kolla om modellen vill avsluta
            if svar.stop_reason == "end_turn":
                logger.info("Fas A avslutad (end_turn) efter %d iterationer", iterationer)
                break

            # Samla verktygskall
            verktygsblock = [b for b in svar.content if b.type == "tool_use"]
            if not verktygsblock:
                logger.info("Fas A avslutad (inga verktygsanrop) efter %d iterationer", iterationer)
                break

            # Lägg assistentsvaret i historiken
            meddelanden.append({"role": "assistant", "content": svar.content})

            # Kör verktygen och samla resultaten
            verktygssvar: list[dict[str, Any]] = []
            for vb in verktygsblock:
                logger.debug("Kör verktyg '%s' med indata: %s", vb.name, vb.input)
                resultat_json = _kör_verktyg(kontext, vb.name, dict(vb.input or {}))
                verktygssvar.append({
                    "type": "tool_result",
                    "tool_use_id": vb.id,
                    "content": resultat_json,
                })

            meddelanden.append({"role": "user", "content": verktygssvar})

        else:
            logger.warning(
                "Fas A nådde max_verktygsvarv=%d utan end_turn", konfig.max_verktygsvarv
            )

        logger.info(
            "Fas A klar: %d Faktaposter, tokens in=%d ut=%d cache_read=%d cache_write=%d",
            len(register),
            tot_in,
            tot_ut,
            cache_read,
            cache_write,
        )

        return HamtningsResultat(
            register=register,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            input_tokens=tot_in,
            output_tokens=tot_ut,
            iterationer=iterationer,
        )
