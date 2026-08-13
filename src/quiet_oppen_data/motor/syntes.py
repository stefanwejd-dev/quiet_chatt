"""Fas B — syntes med tvingad citering.

Tar Faktaregistret från fas A och användarens fråga, och producerar ett
strukturerat svar där varje stycke bär minst en källhänvisning. Se
ARKITEKTUR.md §4 ("Fas B — Syntes (isolerad)") och §1 (invarianten).

Designbeslut:
  * Nytt anrop, rent sammanhang. Kontexten är exakt: systemprompt + frågan +
    `faktaregister.serialisera_for_syntes()`. Ingen konversationshistorik,
    inget verktygsspår, ingen katalog — fas B kan bokstavligen inte citera
    något den inte fått och inte veta något den inte fått.
  * `output_config.format` tvingar JSON-schema. Schemat kräver `minItems: 1`
    på `kallor` per stycke och `additionalProperties: false` överallt —
    modellen kan inte producera ett ociterat stycke.
  * Är Faktaregistret tomt anropas modellen inte alls. Det finns inget att
    citera, så svaret är givet: "Det hittade jag inte i källorna."
  * Samma API-fällor som fas A gäller (se motor/hamtning.py): inget
    `budget_tokens`, inga beta-flaggor för thinking/caching — de är GA.
    Kontrollera `stop_reason` innan `content` läses; Opus 5 kan svara
    `refusal`.
  * Fas C (validatorn, steg 11) är den instans som slutgiltigt kontrollerar
    att varje citerat F-id finns i registret och att attribution följer med.
    Fas B:s ansvar är att producera schema-giltig utdata, inte att
    självvalidera.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

import anthropic

from quiet_oppen_data.konfig import las as las_konfig
from quiet_oppen_data.modeller import Faktaregister

logger = logging.getLogger(__name__)

# Text som ges när Faktaregistret är tomt, eller när modellen avböjer/felar
# på ett sätt som gör att inget belagt svar kan lämnas. Samma text används av
# fas C:s fail-closed-väg (ARKITEKTUR.md §4 fas C, motor/validator.py).
INGET_HITTAT = "Det hittade jag inte i källorna."

# ---------------------------------------------------------------------------
# Systemprompten — FRUSEN (ingen tidsstämpel, inget sessions-id, inget IP)
# ---------------------------------------------------------------------------

_SYSTEMPROMPT = """\
Du formulerar svenska svar på faktafrågor, byggda ENDAST av de Faktaposter du \
får i användarmeddelandet. Du har ingen annan källa till information i denna \
fas — inget verktyg, ingen katalog, inget minne av tidigare meddelanden.

Regler:
1. Använd bara uppgifter som finns i de givna Faktaposterna. Hitta aldrig på \
en siffra, ett datum eller ett namn som inte står där, även om du "vet" \
svaret från din egen förträning.
2. Varje stycke i svaret ska ha minst en källhänvisning (fältet "kallor") \
till de F-id som styckets påstående bygger på. Ett stycke utan täckning i \
Faktaposterna ska inte skrivas.
3. Om Faktaposterna inte räcker för att besvara frågan: sätt \
"kan_besvaras": false, lämna "stycken" tom, och skriv i "forbehall" varför \
(t.ex. att uppgiften inte hittades i de hämtade källorna).
4. Skriv naturlig svenska. Nämn inte F-id i löptexten — de hör hemma i \
fältet "kallor", inte i "text".
5. Räkna aldrig själv (ingen procent, differens eller summering som inte \
redan står i en Faktapost). Om en beräkning saknas, säg det i "forbehall" \
i stället för att uppskatta.
"""


def _bygg_anvandarmeddelande(fraga: str, fakta: str, felmeddelande: str | None = None) -> str:
    bas = f"Fråga: {fraga}\n\nFaktaposter:\n{fakta}"
    if felmeddelande:
        # Omförsök: fas C (validatorn) underkände föregående svar. Felet läggs
        # i användarmeddelandet, inte i systemprompten — den är frusen.
        bas = (
            f"OBS: ditt föregående svar underkändes av valideringen:\n"
            f"{felmeddelande}\n\nRätta felet och svara igen.\n\n{bas}"
        )
    return bas


# ---------------------------------------------------------------------------
# Svarsschema — se ARKITEKTUR.md §4
# ---------------------------------------------------------------------------

SVARSSCHEMA: dict = {
    "type": "object",
    "properties": {
        "kan_besvaras": {"type": "boolean"},
        "stycken": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "kallor": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                    },
                },
                "required": ["text", "kallor"],
                "additionalProperties": False,
            },
        },
        "forbehall": {"type": ["string", "null"]},
    },
    "required": ["kan_besvaras", "stycken", "forbehall"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Resultatstrukturer
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Stycke:
    """Ett stycke i syntessvaret — text plus de F-id det bygger på."""
    text: str
    kallor: tuple[str, ...] = ()


@dataclass(frozen=True)
class SyntesSvar:
    """Resultatet av en fas B-körning.

    `forbehall` bär den kortslutna "hittade inte"-texten när registret var
    tomt eller modellen avböjde — se INGET_HITTAT.

    `attribution` är tom från fas B — den fylls i deterministiskt av fas C
    (motor/validator.py) utifrån vilka Faktaposter som faktiskt citerades,
    aldrig av modellen. Se ARKITEKTUR.md §8.
    """
    kan_besvaras: bool
    stycken: tuple[Stycke, ...] = ()
    forbehall: str | None = None
    attribution: tuple[str, ...] = ()


def _fail_closed(forbehall: str = INGET_HITTAT) -> SyntesSvar:
    return SyntesSvar(kan_besvaras=False, stycken=(), forbehall=forbehall)


def _tolka_svar(rå_text: str) -> SyntesSvar:
    data = json.loads(rå_text)
    stycken = tuple(
        Stycke(text=s["text"], kallor=tuple(s["kallor"]))
        for s in data.get("stycken", [])
    )
    return SyntesSvar(
        kan_besvaras=bool(data.get("kan_besvaras", False)),
        stycken=stycken,
        forbehall=data.get("forbehall"),
    )


# ---------------------------------------------------------------------------
# Publikt API
# ---------------------------------------------------------------------------

class FasBSyntes:
    """Fas B: syntes med tvingad citering.

    Instansieras en gång (per process) och återanvänds för varje fråga —
    samma mönster som FasALopp i motor/hamtning.py.
    """

    def __init__(self) -> None:
        konfig = las_konfig()
        if not konfig.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY är inte satt i miljön.")

        self._klient = anthropic.Anthropic(api_key=konfig.anthropic_api_key)
        self._konfig = konfig

    def syntetisera(
        self, fraga: str, register: Faktaregister, felmeddelande: str | None = None
    ) -> SyntesSvar:
        """Bygger ett citerat svar ur Faktaregistret.

        Är registret tomt görs INGET API-anrop — det finns inget att citera.

        Args:
            felmeddelande: satt av fas C vid ett omförsök efter ett
                valideringsfel. Läggs i användarmeddelandet, aldrig i den
                frusna systemprompten.
        """
        if register.ar_tom():
            logger.info("Fas B: tomt Faktaregister — inget API-anrop görs.")
            return _fail_closed()

        fakta = register.serialisera_for_syntes()
        konfig = self._konfig.modell

        with self._klient.messages.stream(
            model=konfig.namn,
            max_tokens=konfig.max_tokens_syntes,
            system=[{"type": "text", "text": _SYSTEMPROMPT}],
            messages=[
                {
                    "role": "user",
                    "content": _bygg_anvandarmeddelande(fraga, fakta, felmeddelande),
                }
            ],
            thinking={"type": "adaptive"},
            output_config={
                "effort": konfig.effort_syntes,
                "format": {"type": "json_schema", "schema": SVARSSCHEMA},
            },
        ) as ström:
            svar = ström.get_final_message()

        if svar.stop_reason == "refusal":
            kategori = getattr(getattr(svar, "stop_details", None), "category", None)
            logger.warning("Fas B avböjdes av modellen (kategori=%s)", kategori)
            return _fail_closed()

        textblock = [b for b in svar.content if getattr(b, "type", None) == "text"]
        if not textblock:
            logger.warning("Fas B: inget textblock i svaret (stop_reason=%s)", svar.stop_reason)
            return _fail_closed()

        try:
            return _tolka_svar(textblock[0].text)
        except (json.JSONDecodeError, KeyError, TypeError):
            logger.warning("Fas B: kunde inte tolka modellens JSON-utdata", exc_info=True)
            return _fail_closed()
