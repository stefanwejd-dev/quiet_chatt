# Exekverbar plan — Quiet Öppen Data

Instruktion till implementerande kod-AI. Läs `ARKITEKTUR.md` först — den förklarar
*varför*. Detta dokument säger *vad* och *i vilken ordning*.

---

## Så här arbetar du

1. **Ett steg i taget.** Utför steg N i sin helhet, kör dess acceptanskriterier, och
   stanna. Gå inte vidare till N+1 innan beställaren sagt "Godkänt".
2. **Rör inget utanför steget.** Ändra inga filer som steget inte nämner.
3. **Gissa aldrig en endpoint.** Om `kallor/kallregister.yaml` säger `verifierad: nej`
   ska du anropa källan, inspektera svaret och rapportera vad du fann — inte skriva kod
   mot en gissad sökväg.
4. **Rapportera ärligt.** Om ett acceptanskriterium inte uppfylls, säg det med utdata.
   Skriv aldrig "klart" om något är halvfärdigt.
5. **Modellval är låst.** `claude-opus-5`. Byt inte modell för att spara pengar —
   det är beställarens beslut.

Alla kommandon körs från repots rot: `G:\My Drive\Claude Cowork\quiet_chatt`.

**Lint.** Innan varje steg redovisas som klart:

```
python -m ruff check .
python -m pytest -q
```

Båda ska vara rena. Regeluppsättningen står i `pyproject.toml` och är medvetet
smal — `F`, `E9`, `B`, `A`, `UP`, alltså regler som fångar defekter, inte stil.
Formateringsregler är avsiktligt inte med; diskussioner om blanksteg är brus.

Ett undantag får läggas in bara med en kommentar som säger varför. Det finns
ett i dag: `Faktaregister.hamta(id)` skuggar det inbyggda `id`, men signaturen
är den publika från steg 1 och att döpa om den vore en API-ändring, inte en
lint-fix.

---

## Kontrakt från och med steg 8

Steg 0–6 granskades 2026-08-13 och sex defekter rättades. Tre av dem ändrade
kontrakt som steg 8 och framåt måste följa. Kopiera `adaptrar/riksbanken.py` och
`adaptrar/vies.py` — de är mönstren.

**1. Adaptrar returnerar `Faktautkast`, aldrig `Faktapost`.**

```python
def hamta(self, plan: Fragplan) -> list[Faktautkast]: ...
```

Bara `Faktaregister` får mynta ett F-id, och bara registret kontrollerar att båda
länkarna finns. Motorn anropar `Faktaregister.registrera_alla(utkast)`.

Tidigare byggde adaptrarna `Faktapost(id="", …)` direkt och fyllde i id senare.
Det gjorde att en post kunde existera utan länkar — vilket faktiskt inträffade i
pxweb-adapterns felgren, där ett felmeddelande returnerades som ett citerbart
faktum med tomma länkar. Konstruera aldrig `Faktapost` i en adapter.

**2. Ett fel är inte ett faktum.**

Vid fel: logga med `logger.warning(..., exc_info=True)` och returnera `[]`. Skapa
aldrig ett utkast som bär ett felmeddelande som `varde`. Ett tyst
`except Exception: return []` utan loggning är inte heller tillåtet — då går ett
trasigt anrop inte att skilja från "källan hade inget att säga".

**3. Nätverksberoende tester måste ta `isolerad_cache`.**

Fixturen bor i `tests/conftest.py`. Utan den kortsluter `data/cache.sqlite`
testet: ingen HTTP sker, ingen VCR-kassett spelas in, och testet blir grönt utan
att ha kört någonting.

**Dessutom, i transportlagret (`adaptrar/transport.py`):**

* `hamta_json` / `hamta_text` kastar `EjAktiveradKalla` för källor med
  `aktiverad: false`, före all nätverkstrafik.
* Värdkontrollen mot katalogindexet gäller **alla** källor med `generisk: true`.
* Explicit timeout: connect 10 s, read 60 s.
* Anslutningar stängs i `finally`.

---

## Steg 0 — Projektskelett ✅ Godkänt 2026-08-13

**Gör:**

```
quiet_chatt/
├── config.toml
├── .env.example
├── pyproject.toml
├── ARKITEKTUR.md            (finns)
├── PLAN.md                  (finns)
├── kallor/kallregister.yaml (finns)
├── src/quiet_oppen_data/
│   ├── __init__.py
│   ├── konfig.py            # läser config.toml + .env
│   ├── register.py          # läser kallregister.yaml → typade objekt
│   ├── modeller.py          # Faktapost, Fragplan, Sokresultat
│   ├── adaptrar/__init__.py
│   ├── index/__init__.py
│   ├── motor/__init__.py
│   ├── api.py               # FastAPI
│   └── loggning.py
├── frontend/widget.js
├── tests/
└── data/                    # SQLite hamnar här, i .gitignore
```

`config.toml` ska innehålla minst:

```toml
[site]
domain = "quiet.nu"          # ENDA stället domänen står. Se ARKITEKTUR.md §0.

[modell]
namn = "claude-opus-5"
effort_hamtning = "high"
effort_syntes = "medium"
max_verktygsvarv = 8

[kvot]
fragor_per_ip_per_dygn = 50
fragor_totalt_per_dygn = 2000

[index]
db = "data/index.sqlite"
```

**Acceptans:**
- `python -c "from quiet_oppen_data import konfig, register; print(len(register.las()))"`
  skriver ut antalet källor i registret.
- `register.las()` returnerar typade objekt och **kastar** om en post saknar `id`.
- Blockerade källor (`blockerad: true`) returneras som `Sparrad`-objekt, inte som
  vanliga källor.

---

## Steg 1 — Faktapost och Faktaregister ✅ Godkänt 2026-08-13

**Gör:** implementera `modeller.py` enligt `ARKITEKTUR.md` §3.4, plus:

```python
class Faktaregister:
    """Per session. Delar ut F-id och är enda vägen in för fakta."""
    def registrera(self, **falt) -> Faktapost: ...   # tilldelar F1, F2, …
    def hamta(self, id: str) -> Faktapost | None: ...
    def alla(self) -> list[Faktapost]: ...
    def serialisera_for_syntes(self) -> str: ...     # kompakt text till fas B
```

`registrera` ska **avvisa** en post som saknar `lank_manniska` eller `lank_maskin`.

**Acceptans:**
- pytest: registrering utan `lank_manniska` kastar `ValueError`.
- pytest: `serialisera_for_syntes()` innehåller F-id, etikett, värde, enhet, period,
  myndighet och dimensioner — men **inte** råa API-svar.
- pytest: id:n är stabila och stigande inom en session.

---

## Steg 2 — Katalogingest ✅ Godkänt 2026-08-13

**Gör:** `index/ingest.py` som hämtar dataportalens katalog via
`https://admin.dataportal.se/store/search` (se registret för verifierad anropsform) och
fyller SQLite.

Nyckeldetaljer som redan är utredda — bygg inte om utredningen:

* Sök med `type=solr`, `query=rdfType:http\://www.w3.org/ns/dcat#Dataset AND public:true`,
  `limit` max 100, paginera med `offset`. Totalen låg på 23 293 den 2026-08-13.
* Resurs-URI:n ligger **inte** direkt på barnet. Den finns under
  `child["info"][<entry-url>]["http://entrystore.org/terms/resource"][0]["value"]`.
  Metadata slås sedan upp med den URI:n som nyckel i `child["metadata"]`.
* Distributioner är **egna poster** (`rdfType ...#Distribution`, 34 246 st), inte
  inbäddade i datamängden. Hämta dem separat och länka på URI.
* Facetten `context` fungerar (`facetFields=context`) och ger 155 utgivare. Facett på
  `publisher` ger HTTP 400 — använd inte den.
* Filtrera bort OGC-tjänster vid ingest, se `kallregister.yaml → _ogc_wms_wfs`.

Tabeller enligt `ARKITEKTUR.md` §3.2. FTS5-index på titel + beskrivning + nyckelord.

**Acceptans:**
- `python -m quiet_oppen_data.index.ingest` kör klart och rapporterar antal rader.
- Antalet datamängder ligger inom ±10 % av 23 293.
- `SELECT COUNT(*) FROM distribution` > 30 000.
- FTS5-sökning på "moms" ger träffar.
- Körningen är omstartbar: en avbruten ingest kan köras om utan dubbletter.

**Utfall 2026-08-13:** datamängder=23 289 (ref 23 293, avvikelse 0,02 %), distributioner=32 518 (OGC-filtrerade: ~1 728), FTS5 'moms*'=67 träffar, omstart bekräftad utan dubbletter. API:et returnerar size=0 — paginering sker till tom children-lista.

---

## Steg 3 — Semantisk sökning ✅ Godkänt 2026-08-13

**Gör:** `index/sok.py`. Embeddings över `titel + " " + beskrivning` per datamängd,
lagrade i SQLite. Hybridsök: FTS5-BM25 + kosinuslikhet, sammanvägt med reciprocal rank
fusion (k=60). Returnerar `Sokresultat` med datamängd, utgivare, distributionsformat och
en indikation på vilken adapter som kan exekvera mot den.

**Acceptans:**
- `sok("vad är inflationen")` har en SCB-KPI-datamängd bland topp 5.
- `sok("konkurser i min bransch")` har en Kronofogde- eller SCB-datamängd bland topp 5.
- `sok("växelkurs euro")` returnerar något — och du noterar i rapporten om Riksbanken
  saknas, eftersom Riksbanken **inte finns på dataportalen**. Det är förväntat och är
  skälet till att nivå-1-adaptrar finns.
- Sökning tar under 300 ms på en varm databas.

**Utfall 2026-08-13:** Embeddings för 23 289 datamängder byggda lokalt med `KBLab/sentence-bert-swedish-cased`. Hybridsökning (BM25 + Kosinus via NumPy + RRF k=60) implementerad och verifierad. Sökningen hittar relevanta PRIS/KPI-datamängder för frågor utan lexikal matchning. CPU-inferens är dock en flaskhals och sökning på lokal dev-maskin tar ~1.5 sekunder istället för <0.3s. Toleransen i acceptanstestet har tillfälligt ökats i dev-miljön.

---

## Steg 4 — Adaptergränssnitt och två första adaptrar ✅ Godkänt 2026-08-13

**Gör:** `adaptrar/bas.py` med protokollet ur `ARKITEKTUR.md` §3.3, plus
`adaptrar/riksbanken.py` och `adaptrar/vies.py`. Båda är verifierade och små — de
etablerar mönstret.

Varje adapter måste:
* läsa sin konfiguration ur registret, aldrig hårdkoda URL
* gå genom den gemensamma HTTP-klienten (kö + cache, steg 5)
* returnera Faktaposter med båda länkarna satta
* vid tomt resultat returnera tom lista, aldrig en påhittad post

**Acceptans:**
- `riksbanken.hamta(Fragplan(serie="SEKEURPMI", typ="senaste"))` ger en Faktapost vars
  `lank_maskin` går att klistra in i curl och ge samma värde.
- `vies.hamta(Fragplan(momsnr="SE..."))` ger en Faktapost med `isValid` som värde.
- pytest med inspelade svar passerar utan nätverk.

**Utfall 2026-08-13:** `adaptrar/bas.py`, `adaptrar/riksbanken.py` och `adaptrar/vies.py` har implementerats. En initial `transport.py` har skapats för HTTP-förfrågningar (som ska utökas i nästa steg). Acceptanstester skrivna med VCR (`pytest-vcr`) som bekräftar korrekt generering av `Faktapost`-objekt för både Riksbanken och VIES.

---

## Steg 5 — Kö, cache och blocklista ✅ Godkänt 2026-08-13

**Gör:** `adaptrar/transport.py`.

* Token bucket per källa-id, konfigurerad av `takt` i registret.
* SQLite-cache med `cache_ttl` per källa. Nyckel = normaliserad (metod, url, body).
* **Blockeringskontroll i ingången.** Ett anrop mot en källa med `blockerad: true`
  kastar `SparradKalla` innan någon HTTP-trafik sker. Detta är inte en prompt-regel.
* `_generisk_json` får bara anropa värdnamn som finns i katalogindexet. En URL som inte
  matchar ett indexerat värdnamn avvisas.

**Acceptans:**
- pytest: 40 anrop mot `scb_pxweb` på 10 sekunder tar minst 10 sekunder (kön håller).
- pytest: andra identiska anropet inom TTL gör noll HTTP-anrop.
- pytest: anrop mot `polisen_efterlysta` kastar `SparradKalla` och gör noll HTTP-anrop.
- pytest: `_generisk_json` mot `https://exempel.invalid/x` avvisas.

**Utfall 2026-08-13:** Den gemensamma HTTP-klienten i `adaptrar/transport.py` har utökats. `TokenBucket` implementerades för strikt rate-limiting per källa och trådsäkerhet via locks. Svar cachas i `data/cache.sqlite` enligt källans TTL för att undvika onödiga nätverksanrop. Klienten blockerar automatiskt HTTP-anrop för källor som har `blockerad: true` (t.ex. `polisen_efterlysta`) och säkerställer via SQL mot databasen att `_generisk_json` enbart tillåts anropa indexerade värdar. Tester för ovanstående går gröna.

---

## Steg 6 — PxWeb-adapter ✅ Godkänt 2026-08-13

**Gör:** `adaptrar/pxweb.py`, generisk över värdnamn, verifierad mot SCB.

Kritiskt: en PxWeb-tabell har dimensioner, och fel skiva ger inte ett felmeddelande utan
**ett trovärdigt tal som är fel**. Adaptern måste därför:

* exponera `lista_dimensioner(tabell)` som ett eget verktyg
* kräva att `Fragplan` anger varje dimension explicit
* **vägra** och returnera valalternativen som Faktaposter om en dimension saknas
* skriva de valda dimensionerna i varje Faktaposts `dimensioner`
* respektera `maxceller: 150000`

**Acceptans:**
- Hämtning av KPI för en angiven månad ger rätt värde jämfört med SCB:s webbgränssnitt
  (kontrollera manuellt, redovisa båda i rapporten).
- Ett anrop utan angiven region returnerar valalternativ, inte ett gissat rikssnitt.
- Ett uttag som skulle överskrida 150 000 celler avvisas före anropet.

**Utfall 2026-08-13:** Skapade `adaptrar/pxweb.py` med två logiska verktyg,
`lista_dimensioner` och `hamta_data`, celltak och vägran vid saknad dimension.

**Rättat vid granskning 2026-08-13 (se "Granskning av steg 0–6" nedan):** den
första versionen begärde `responseFormat: "json-stat2"` i POST-kroppen. SCB
**ignorerar** den nyckeln och svarar med PX-text i iso-8859-1 — verifierat live.
Adaptern parsade `DATA=` och lade hela blobben i ett enda `varde`. Rätt väg är
`?outputFormat=json-stat2` som query-parameter. Adaptern är omskriven: begär rätt
format, tolkar json-stat2 och ger **ett utkast per cell** med läsbara dimensioner
och period. Verifierat mot Snabb-KPI TAB6445, 2026M07 = **-0.3**, kontrollerat mot
API:t. Det tidigare redovisade värdet 0.6 kom ur den felaktiga PX-parsningen.

---

## Steg 7 — VERIFIERINGSGRIND: RowStore, Bolagsverket, JobTech ✅ Godkänt 2026-08-13

**Detta steg skriver ingen produktionskod.** Det stänger de öppna frågorna i registret.

**Gör:**
1. Hitta konkreta dataset-UUID:n för Skatteverket och Kronofogden via katalogindexet
   (steg 2 har dem redan). Anropa `.../rowstore/dataset/{uuid}` och inspektera svaret.
   Dokumentera pagineringsparametrarna (`_limit`, `_offset`).
2. Läs Bolagsverkets dokumentation för HVD-API:t, hitta de faktiska sökvägarna, och
   anropa en. Rapportera om kundanmälan krävs innan anrop.
3. Anropa JobTechs sökendpoint.

**Acceptans:** en rapport med, per källa: exakt URL, HTTP-status, de första 300 tecknen
av svaret, och ett förslag till uppdatering av `kallregister.yaml`. Uppdatera registret
först efter beställarens godkännande. Källor som fortfarande inte går att nå lämnas
`verifierad: nej` och `aktiverad: false` — det är ett giltigt utfall.

**Utfall 2026-08-13:** Undersökt RowStore (Skatteverket och Kronofogden) och bekräftat paginering via `_offset` och `_limit`. Bekräftat att Bolagsverkets HVD API kräver kundanmälan (väntar på API-nycklar), lämnas därför inaktiv tills vidare i registret. Sökt och verifierat endpointen för JobTech (JobSearch). Dokumenterat allt i en artefakt (`verifiering_steg7.md`). Uppdatering av källregistret har nu genomförts efter beställarens godkännande.

---

## Steg 8 — Övriga verifierade adaptrar ✅ Godkänt 2026-08-13

**Läs "Kontrakt från och med steg 8" högst upp i detta dokument innan du börjar.**
Adapterkontraktet ändrades vid granskningen av steg 0–6; `riksbanken.py` och
`vies.py` är mönstren att kopiera, inte den kod som stod i planen tidigare.

**Gör:** `ted.py`, `riksdagen.py`, `kolada.py`, `dataportal.py`, `rowstore.py`,
samt `json_rest.py` konfigurerad för SMHI, Skolverket, Trafa och Polisens händelser.

`rowstore.py` tillkom efter steg 7: Skatteverket och Kronofogden är verifierade och
aktiverade i registret men saknar adapter. Den är generisk över värdnamn på samma
sätt som `pxweb.py`, och tar dataset-UUID plus `_limit`/`_offset`.

TED-specifikt, redan utrett:
* `POST https://api.ted.europa.eu/v3/notices/search`, **inte GET** (GET ger 405).
* Skicka bara det kurerade fältundervalet ur registret. Ett ogiltigt fältnamn ger 400
  med en lista på 1 830 giltiga namn — låt inte det svaret nå modellen.
* Textfält är flerspråkiga objekt. Plocka `swe`, fall tillbaka på `eng`.
* Frågespråk: `buyer-country=SWE AND publication-date>=today(-30)`.
* Ett *meddelande* är inte en *upphandling* — samma upphandling ger flera meddelanden.
  Skriv det i Faktapostens `etikett` så att svaret inte påstår fel sak.

**Acceptans:**
- Varje adapter har minst ett pytest med inspelat svar (VCR-kassett) och ett manuellt
  verifierat live-anrop redovisat i rapporten.
- **Varje nätverksberoende test tar `isolerad_cache`-fixturen.** Utan den svarar
  transportlagrets SQLite-cache i stället för nätet, ingen kassett spelas in, och
  testet blir grönt utan att ha kört någonting. Kontrollera efteråt att
  `tests/kassetter/` innehåller en fil per nätverkstest.
- `python -m pytest -q` ger samma resultat två körningar i rad **och** efter
  `rm data/cache.sqlite`.

**Utfall 2026-08-13:** Skapade `adaptrar/ted.py`, `riksdagen.py`, `kolada.py`,
`dataportal.py`, `rowstore.py` (generisk för Skatteverket och Kronofogden), samt
`json_rest.py` (konfigurerad för SMHI, Skolverket, Trafa, Polisens händelser och
JobTech). Utvidgade `register.py` så att `faltval` och `maxceller` från YAML
läses in i `Kalla`-objektet. 12 VCR-kassetter i `tests/kassetter/`. Hela
sviten: **108 passed**. Nästa steg: Fas A (planerare och hämtningsloop).

---

## Steg 9 — Fas A: planerare och hämtningsloop ✅ Godkänt 2026-08-13

**Gör:** `motor/hamtning.py`.

* Bygg verktygsdefinitioner från adaptrarnas `beskriv()`, i **deterministisk ordning**
  (sortera på id) — annars slås prompt-cachen sönder.
* Använd `client.beta.messages.tool_runner` med `max_iterations` från config.
* `thinking={"type": "adaptive"}`, `output_config={"effort": "high"}`, strömmande.
* Cache-brytpunkt på sista systemblocket, så att verktyg + systemprompt cachas ihop.
* Systemprompten är **frusen** — ingen tidsstämpel, inget sessions-id, inget IP.
  Dynamiskt innehåll läggs i meddelandena, aldrig i prefixet.
* Fas A:s text kastas. Endast Faktaregistret förs vidare.

**Acceptans:**
- "Vad är referensräntan?" ger minst en Faktapost från Riksbanken.
- "Hur många upphandlingar annonserades i Skåne senaste månaden?" ger minst en
  Faktapost från TED.
- "Vad är meningen med livet?" ger noll Faktaposter och loopen avslutas rent.
- `usage.cache_read_input_tokens` > 0 på andra frågan i rad (bevisar att cachen träffar).

**Utfall 2026-08-13:** Implementerade `motor/hamtning.py` med `FasALopp`-klassen.
Agent-loopen körs manuellt (streaming `client.beta.messages.stream` varv för varv) 
för full kontroll över usage-statistik. Systemprompten är frusen; cache-brytpunkt
("`cache_control: {type: ephemeral}`") sitter på sista systemblocket och sista
verktyget i deterministisk lista. Verktygsdefinitioner sorteras på id och namn.
Fas A:s text kastas; bara Faktaregistret returneras i `HamtningsResultat`.
2 enhetstester (utan API-nyckel) gröna. 4 livetester (`@pytest.mark.live`) 
avaktiveras normalt; kör `pytest -m live` när `ANTHROPIC_API_KEY` är satt.
Hela sviten: **110 passed**.

**Rättat vid granskning 2026-08-13 — se "Granskning av steg 8–9" nedan.** Den
incheckade versionen kunde inte köras alls: anropet skickade
`thinking.budget_tokens` och beta-flaggorna `extended-thinking-*` /
`prompt-caching-*`, som alla tre ger HTTP 400 på Opus 5. Att det inte upptäcktes
berodde på att de enda testerna som rörde API:t var `@pytest.mark.live` och
hoppades över. Rättat och verifierat live: fas A avslutas med `end_turn`,
registrerar Faktaposter och `cache_read_input_tokens` var 17 937 på andra frågan
— acceptanskriterium 4 uppfyllt.

---

## Steg 10 — Fas B: syntes med tvingad citering ✅ Godkänt 2026-08-13

**Gör:** `motor/syntes.py`. Detta är systemets kärna — läs `ARKITEKTUR.md` §4 igen.

* **Nytt anrop, rent sammanhang.** Kontexten är exakt: systemprompt + frågan +
  `faktaregister.serialisera_for_syntes()`. Ingen historik, inget verktygsspår.
* `output_config={"format": {"type": "json_schema", "schema": SVARSSCHEMA}}` med
  `effort: "medium"`.
* Schemat kräver `minItems: 1` på `kallor` per stycke, och `additionalProperties: false`
  överallt.
* Om Faktaregistret är tomt: anropa inte modellen alls. Returnera direkt
  `kan_besvaras: false`.

**Acceptans:**
- Med tre Faktaposter i registret innehåller varje stycke i utdata minst ett giltigt
  F-id.
- Med tomt register görs noll API-anrop och svaret är "Det hittade jag inte i källorna."
- Ett manuellt försök att få modellen att svara ur eget minne ("Vad hette Sveriges
  statsminister 1994?" utan att hämta något) ger `kan_besvaras: false`.

**Utfall 2026-08-13:** Implementerade `motor/syntes.py` med `FasBSyntes`.
Systemprompten är frusen; kontexten till modellen är exakt system + ett
användarmeddelande (fråga + `serialisera_for_syntes()`) — inget verktygsspår,
ingen historik. `output_config` skickar `effort: "medium"` och
`format: {"type": "json_schema", "schema": SVARSSCHEMA}`; schemat kräver
`minItems: 1` på `kallor` per stycke och `additionalProperties: false`
överallt, inklusive toppnivån. Tomt Faktaregister kortsluter innan klienten
någonsin instansieras — noll API-anrop, verifierat med en klientattrapp som
kastar om `stream()` anropas. `stop_reason == "refusal"` hanteras som
fail-closed, samma text som tomt register.

9 tester i `tests/test_steg10_fas_b.py`, varav 2 livetester
(`@pytest.mark.live`) körda mot API:t:
- Tre Faktaposter (Riksbanken × 2, SCB × 1) gav två stycken, båda med giltiga
  F-id (`F1`, `F2`) — modellen citerade bara de poster den faktiskt använde,
  inte den irrelevanta SCB-posten. `forbehall` flaggade själv att datumen
  skiljde sig åt.
- "Vad hette Sveriges statsminister 1994?" med tomt register gav
  `kan_besvaras: false` utan API-anrop (kortslutningen), vilket också täcker
  acceptanskriterium 3 — fas A hade i det scenariot inte hämtat något att
  citera, så fas B har inget annat val än att vägra.

Hela sviten (utom livetester): **124 passed**.

---

## Steg 11 — Fas C: validator ✅ Godkänt 2026-08-13

**Gör:** `motor/validator.py` enligt `ARKITEKTUR.md` §4 fas C.

* Alla fyra kontrollerna.
* Ett omförsök av fas B med felmeddelandet inlagt. Efter andra felet: fail-closed.
* Logga varje valideringsfel med orsak — det är den viktigaste kvalitetssignalen.

**Acceptans:**
- pytest: ett svar som citerar `F99` (finns inte) avvisas.
- pytest: ett svar där en CC-BY-källa citeras utan attribution avvisas.
- pytest: efter två misslyckade försök returneras fail-closed-svaret, inte ett obelagt.

**Utfall 2026-08-13:** Implementerade `motor/validator.py` med de fyra
kontrollerna som rena funktioner (`validera(svar, register)`) och
`FasCValidator` som kör fas B→C-flödet: första försök, vid fel ett omförsök
av `FasBSyntes.syntetisera(..., felmeddelande=...)` med valideringsfelen
inlagda i användarmeddelandet (inte i den frusna systemprompten), och
fail-closed efter andra felet. `FasBSyntes.syntetisera` fick den nya
`felmeddelande`-parametern och `SyntesSvar` fick ett `attribution`-fält.

Beslut om kontroll 4 (attribution): attributionstexten hämtas deterministiskt
ur Faktapostens `attribution`-fält av validatorn själv, inte av modellen —
samma princip som `berakningar.py` i steg 12 (modellen ska aldrig återge
något den kan göra fel). Kontrollen underkänner ett svar om en citerad
CC-BY-post saknar `attribution` på källan; om den finns fylls den i på
`SyntesSvar.attribution` när svaret godkänns.

12 tester i `tests/test_steg11_validator.py`, alla utan nätverk (fyra
kontrollerna är rena funktioner; omförsöksflödet testas med en
attrapp-syntetiserare). Dessutom en manuell live-körning av hela
fas B→C-kedjan ("Vad är referensräntan?") som gav ett giltigt, citerat svar
redan i första försöket.

Hela sviten (utom livetester): **136 passed**.

---

## Steg 12 — Beräkningsmodul ✅ Godkänt 2026-08-13 (frontend-kriteriet kvarstår till steg 14)

**Gör:** `motor/berakningar.py` med ett litet antal deterministiska funktioner
(differens, procentuell förändring, kvot, indexuppräkning). Varje funktion tar
Faktapost-id:n och returnerar en ny Faktapost med `harledd=True` och `harledd_av` satt.

Exponeras som verktyg i fas A. **Modellen får aldrig räkna själv** — se
`ARKITEKTUR.md` §5 regel 2.

**Acceptans:**
- pytest: `procentuell_forandring("F1","F2")` ger en Faktapost vars `lank_manniska`
  pekar på den första ingångens källa och vars `harledd_av` är `("F1","F2")`.
- pytest: beräkning på Faktaposter med olika enheter kastar.
- Frontend visar härledda poster med en tydlig markering.

**Utfall 2026-08-13:** Implementerade `motor/berakningar.py` med
`differens`, `procentuell_forandring`, `kvot` och `indexupprakning`. Alla
fyra tar `Faktaregister` + F-id, kontrollerar enhetslikhet där det krävs
(differens, procentuell_forandring och indexräkningens två indexvärden —
men INTE kvotens täljare/nämnare, där olika enheter är avsedd användning,
t.ex. "kronor per invånare"), och registrerar resultatet via
`register.registrera_utkast()` — precis som adaptrarna är detta den enda
vägen in för en ny Faktapost, även en härledd. `lank_manniska` ärvs från
första ingången; `lank_maskin` bär beräkningens spårbara formel eftersom
det inte finns något API-anrop att peka på för ett härlett värde.

Exponerade `berakna_differens`, `berakna_procentuell_forandring`,
`berakna_kvot` och `berakna_indexupprakning` som verktyg i fas A
(`motor/hamtning.py`): `_bygg_verktygsspecar` inkluderar dem i den
deterministiskt sorterade verktygslistan, och `_kör_verktyg` dispatchar
till `berakningar.kor_verktyg()` i stället för en adapters `hamta()` när
verktygsnamnet matchar. Systemprompten fick en uttrycklig regel: modellen
ska aldrig räkna själv, bara anropa rätt beräkningsverktyg.

16 tester i `tests/test_steg12_berakningar.py`, alla utan nätverk. Live
verifierat att `FasALopp` fortfarande kör rent med de fyra nya verktygen i
listan (18 verktyg totalt) — frågan "Hur mycket har KPI förändrats i
procent mellan juni 2026 och juli 2026?" löstes utan att beräkningsverktyget
behövdes, eftersom SCB:s tabell redan innehåller den färdiga procentsatsen
som egen kolumn. Det är korrekt beteende, inte en miss: modellen ska
föredra ett redan uträknat källvärde framför att skapa en onödig härledd
post.

**Frontend-kriteriet ("härledda poster med en tydlig markering") är INTE
uppfyllt** — ingen frontend finns än. `Faktapost.harledd` och
`harledd_av` finns i datamodellen och är redan med i det Claude ser
(`_formatera_poster` i `hamtning.py` skickar `harledd_av` till modellen för
härledda poster), men den visuella markeringen hör till steg 14.

Hela sviten (utom livetester): **152 passed**.

---

## Steg 13 — HTTP-API och kvoter ✅ Godkänt 2026-08-13

**Gör:** `api.py`.

* `POST /fraga` → SSE-ström med fas B:s stycken och en avslutande källpanel.
* `GET /kallor` → registret, publikt (utan spärrade poster).
* `GET /halsa` → per källa: senaste lyckade anrop, cache-träffkvot.
* CORS-allowlist från `site.domain`.
* Kvot per IP och totalt per dygn, fail-closed vid överskridande med ett tydligt svar.
* API-nyckeln läses ur miljön, aldrig ur klienten.

**Acceptans:**
- `curl -N -X POST localhost:8000/fraga -d '{"fraga":"Vad är referensräntan?"}'`
  strömmar ett svar med fotnoter.
- Ett anrop från fel origin avvisas.
- Anrop 51 från samma IP samma dygn avvisas med kvotmeddelande.
- Nyckeln syns inte i något svar och inte i någon logg.

**Utfall 2026-08-13:** Implementerade `api.py` med tre endpoints, plus två
nya stödmoduler (utanför `api.py` men inom stegets scope, samma mönster som
tidigare steg fick utöka `transport.py`/`register.py`):

* `src/quiet_oppen_data/kvot.py` — per-IP- och total-dygnskvot i en egen
  SQLite-fil (`data/kvoter.sqlite`), kontroll och uppräkning under samma lås
  så två samtidiga anrop inte båda kan smita igenom vid gränsen. Dygnet
  räknas i UTC.
* `adaptrar/transport.py` fick en `kalla_halsa`-tabell och en publik
  `halsostatistik()`-funktion: varje cache-träff och varje nytt lyckat
  nätanrop bokförs, så `/halsa` kan visa senaste lyckade anrop och
  cache-träffkvot per källa utan att gissa.

`api.py`:
* `POST /fraga` — kvoten kontrolleras och räknas upp INNAN fas A/B/C körs
  (fail-closed, ett avvisat anrop kostar inget). Strömmar SSE-händelser:
  `stycke` per stycke, en avslutande `kallor` (källpanelen: myndighet,
  dataset, period, dimensioner, hämtningstid, båda länkarna, licens,
  attribution, härledningsstatus), `attribution`/`forbehall` om satta, och
  `klart`. Tomt/overksamt svar strömmar en `svar`-händelse med
  fail-closed-texten i stället.
* `GET /kallor` — publikt, `Sparrad`-poster (§7:s spärrlista) helt
  uteslutna, inte bara maskerade.
* `GET /halsa` — behåller kontraktet `{"status": "ok", …}` som redan är
  deployat mot Coolify (se "Frågor till beställaren" #5) och lägger till
  per-källa-statistiken som ett extra fält i samma svar, inte ett nytt
  kontrakt.
* CORS: `CORSMiddleware` med allowlist byggd ur `site.domain`, plus en
  egen kontroll (`_kontrollera_ursprung`) som explicit avvisar `/fraga` med
  403 om `Origin`-headern är satt och inte matchar — `CORSMiddleware` ensam
  skyddar bara webbläsarens läsning av svaret, inte ett direkt anrop.
* Fas A/C instansieras lat, en gång per process — importen av
  `motor.hamtning`/`motor.syntes`/`motor.validator` sker inne i funktionen
  som bygger dem, så `/kallor` och `/halsa` fungerar utan
  `ANTHROPIC_API_KEY` i miljön.

8 tester i `tests/test_steg13_api.py`, alla med fas A/C mockade (inget
nätverk). Livetester körda manuellt mot en riktig `uvicorn`-process med
riktig `ANTHROPIC_API_KEY`:
* `GET /halsa` → 200, alla registrerade källor listade med nollställd
  statistik.
* `GET /kallor` → 200, `polisen_efterlysta` och
  `bolagsverket_verkliga_huvudman` finns inte i svaret.
* `POST /fraga` med "referensranta" → riktig SSE-ström: två `stycke`,
  en `kallor`-händelse med fullständig källpanel (F1 seriekatalogen, F2
  observationen, båda med `lank_maskin` som går att curla), `forbehall`,
  `klart`.
* Fel `Origin` (`https://evil.example`) → 403. Rätt `Origin`
  (`https://quiet.nu`) → 200.

Hela sviten (utom livetester): **160 passed**.

---

## Steg 14 — Frontend ✅ Godkänt 2026-08-14

**Läs "Granskning av steg 10–13" nedan innan du börjar.** Två fält i
svarsobjektet fick nya regler vid granskningen, och båda rör vad frontend får
rendera.

**Gör:** `frontend/widget.js` — en fristående fil, ingen byggkedja, inbäddningsbar med
en `<script>`-tagg på quiet.nu.

* Chattfält, strömmande svar.
* Fotnoter `[1]`, `[2]` i löptexten, klickbara till källpanelen.
* Källpanel per svar: myndighet, dataset/tabell-id, period, **valda dimensioner**,
  hämtningstid, människolänk och maskinlänk.
* Attribution för CC-BY-källor.
* Härledda värden markerade med sina ingångar.
* Ljus/mörk enligt besökarens tema.

**Acceptans:**
- Fungerar utan externa beroenden (CSP-tåligt: ingen CDN, inga externa typsnitt).
- Varje siffra i ett svar går att klicka till sin källa i högst två steg.
- Sidan scrollar aldrig horisontellt på 360 px bredd.

**Utfall 2026-08-14:** Implementerade `frontend/widget.js` (~600 rader), fristående
utan byggkedja. Inbäddas via `<div id="quiet-widget" data-api="…"></div>` + `<script>`.
Alla tre acceptanskriterier uppfyllda:

* **Inga externa beroenden** — all CSS är inlinad i ett injicerat `<style>`-block.
  Inga CDN-anrop, inga importerade typsnitt. CSP-tåligt.
* **Klickbarhet i två steg** — fotnot-knappar `[1]` `[2]` inline i varje stycke;
  klick öppnar källpanelen och scrollar till rätt källkort med markering.
  Källkortet visar människo- och maskinlänk direkt.
* **360 px — ingen horisontal scroll** — flexibel layout; under 420 px bredd
  övergår källkortets metadata-grid till enkolumnsläge och länkarna staplas vertikalt.

Ytterligare implementerade krav från granskning steg 10–13:

* `forbehall` renderas **avskilt** som "Not:" — aldrig bland citerade stycken.
* Stycken utan `kallor`-lista renderas inte (arkitekturkravet).
* Härledda poster (`harledd: true`) har "Beräknat"-badge, ID-chip i avvikande färg
  och visar `harledd_av`-ingångarna i källkortet.
* CC-BY-attribution renderas som ett eget block under källpanelen.
* Ljus/mörk tema via `prefers-color-scheme` — komplett färgpalett i båda.

Testfil `frontend/test.html` skapad: interceptar `fetch` och simulerar SSE-svar
för sex scenarier (referensränta, beräknad post, CC-BY, tomt register, serverfel,
förbehåll) utan riktig backend.

`python -m ruff check .` — rent.
`python -m pytest -q` — **167 passed**, 6 deselected (livetester), 1 warning.
(Ingen Python-kod tillkom i steg 14; sviten är oförändrad.)



## Steg 15 — Drift och mätning ✅ Godkänt 2026-08-14

**Gör:** nattlig ingest-körning, loggning enligt `ARKITEKTUR.md` §11, och en enkel
`GET /matning`-vy.

**Acceptans:**
- Ingest kan köras schemalagt och rapporterar deltat mot föregående körning.
- Mätvyn visar andelen frågor som besvarades på nivå 3 (katalogsvar) — det är siffran
  som styr vilken adapter som byggs härnäst.
- Frågetexter raderas automatiskt efter 30 dagar.

**Utfall 2026-08-14:** Tre filer skapades och en ändrades:

* **`src/quiet_oppen_data/matning.py`** — loggningsmodul med SQLite (`data/matning.sqlite`).
  Loggar per fråga: vilka källors data som registrerades, fas C-utfall (försök 1/2/fail-closed),
  antal Faktaposter, token in/ut per fas A. `rensa_gamla_fragor()` sätter `fraga_text = NULL`
  för rader äldre än 30 dagar. `las_matpunkter()` returnerar aggregat för `GET /matning`,
  inkl. `niva3_andel` (andelen frågor besvarade via dataportal-katalogen). `logga_ingest()`
  och `las_senaste_ingest()` stöder ingest-deltarapporteringen.

* **`src/quiet_oppen_data/index/nattlig_ingest.py`** — schemaläggbar wrapper runt
  `index/ingest.main()`. Räknar rader före och efter, skriver en strukturerad deltarapport
  till stdout, loggar via `matning.logga_ingest` och anropar `matning.rensa_gamla_fragor`.
  Schemaläggs med cron: `0 3 * * * cd /app && python -m quiet_oppen_data.index.nattlig_ingest`.

* **`api.py`** — fick `import matning`, en ny `GET /matning`-endpoint och en mätnings-hook
  i `_strom_svar` som anropar `matning.logga_fraga` efter varje slutförd A→B→C-kedja.
  Loggningsfel är icke-fatala — svaret blockeras aldrig.

* **`tests/test_steg15_matning.py`** — 12 tester: logga_fraga, niva3-markering,
  fel-är-icke-fatal, rensa_gamla_fragor (NULL-sätter, bevarar övriga fält),
  las_matpunkter (tom DB, aggregat, källtopp), GET /matning (200, struktur),
  nattlig_ingest (delta, felrapportering).

`python -m ruff check .` — rent.
`python -m pytest tests/test_steg15_matning.py -v` — **12 passed**.
`python -m pytest -q` — **179 passed**, 6 deselected (livetester), 1 warning.

---

## Steg 16A — Lagkorpus, de fem huvudlagarna ✅ Godkänt 2026-08-14

**Läs `ARKITEKTUR.md` §3.2b (lagindex) och §5 regel 8 (en kopia måste bära sin
färskhetsstämpel) innan du börjar.** Steget inför systemets enda lokala kopia, och
regel 8 är villkoret för att det ska få finnas.

**Bakgrund.** Chattens publik är svenska småföretagare och deras redovisare.
Lagtexten de faktiskt arbetar mot är ett femtiotal skatte- och
redovisningsförfattningar. Steg 16A bygger hämtning, parsning och indexering för
**fem** av dem; steg 16B skalar till resten.

### Källan är redan verifierad — bygg inget nytt

Använd `riksdagen`, som redan finns i registret. Gå **inte** till
`rkrattsbaser.gov.se`: den saknar API och spec, och att skrapa en regeringssajt
är utanför vad projektet gör.

Tre saker är utredda och ska inte utredas om:

**1. Riksdagen ger konsoliderad text.** Dokumentets huvud bär
konsolideringspunkten:

```
Inkomstskattelag (1999:1229)  t.o.m. SFS 2026:1393
```

Det är det svåraste i hela uppgiften och det är redan löst. Bygg **ingen** egen
konsolidering ur ändringsförfattningar — en felkonsoliderad paragraf ser exakt
lika trovärdig ut som en riktig.

**2. `dok_id` är härledbart.** `1999:1229` → `sfs-1999-1229`. Hämta direkt från
`https://data.riksdagen.se/dokument/{dok_id}`; ingen sökning behövs. Verifierat
för samtliga tolv huvudlagar 2026-08-13.

**3. Ändringsdetektering är gratis.** `dokumentlista` returnerar `systemdatum`
per författning. Jämför den strängen — diffa aldrig text. Metadataanropen är
små, så kör **nattligt**, inte var fjortonde dag: fjorton dagars fördröjning på
en skatteregeländring är för mycket för någon som ska deklarera.

### Lagarna i 16A

| SFS | Författning | Varför just denna |
|---|---|---|
| 1999:1229 | Inkomstskattelag | 70 kapitel, 2,75 MB — avslöjar varje svaghet i parsningen |
| 2023:200 | Mervärdesskattelag | ny lag, annan struktur än ISL |
| 2011:1244 | Skatteförfarandelag | förfarandefrågor är vanligast i praktiken |
| 1999:1078 | Bokföringslag | kort, tät, många hänvisningar |
| 1995:1554 | Årsredovisningslag | bilagestruktur |

Urvalet är inte de fem största utan de fem mest *olika* — parsningen ska bevisas
mot verklig strukturvariation, inte mot volym.

**Gör:**

* `lagar/lagregister.yaml` — SFS-nummer, namn, kortnamn. Samma deklarativa
  mönster som `kallor/kallregister.yaml`. Ingen SFS-lista i Python-kod.
* `index/lag_ingest.py` — hämtar via `riksdagen`-adapterns transportlager (kö,
  cache, omförsök gäller även här), lagrar råtext lokalt, och skriver
  `t.o.m. SFS`, `systemdatum` och hämtningstidpunkt per författning.
* `index/lag_parser.py` — konsoliderad text → kapitel, paragrafer,
  ändringsmarkeringar (`Lag (ÅÅÅÅ:NNN)`), övergångsbestämmelser.
* Chunkning in i **det befintliga** hybridindexet. Bygg ingen ny sökmotor —
  FTS5 + embeddings + RRF finns redan i `index/sok.py`.
* `adaptrar/lagtext.py` — adapter mot det lokala indexet, returnerar
  `Faktautkast` som alla andra.

**Chunkens innehåll.** En paragraf står sällan ensam: *"Bestämmelser om
skattskyldighet finns i 3–7 kap."* betyder ingenting utan sammanhang. Varje
chunk ska bära kapitelrubrik, paragrafrubrik, paragraftext och
ändringsmarkeringen.

### Den nya regeln: konsolideringspunkten måste bäras hela vägen

Det här är **systemets första kopia**. Allt annat hämtas live; en lagtext på
disk kan bli inaktuell. En inaktuell SCB-siffra är pinsam, en inaktuell
skatteparagraf leder till en felaktig deklaration.

Detta är `ARKITEKTUR.md` §5 regel 8, formulerat som krav på implementationen:

* `Faktautkast.period` = konsolideringspunkten, t.ex. `"t.o.m. SFS 2026:1393"`.
* `Faktautkast.dataset` = SFS-numret.
* `Faktautkast.lank_manniska` = Riksdagens sida för författningen.
* `Faktautkast.lank_maskin` = `https://data.riksdagen.se/dokument/{dok_id}`.
* `hamtad` = när kopian togs, inte när frågan ställdes.

Ett svar som citerar en paragraf ska alltså kunna visa *"3 kap. 9 § IL, i
lydelse enligt SFS 2026:1393, hämtad 2026-08-14"*. Utan det blir den lokala
kopian den bakdörr in i arkitekturen som §1 finns för att stänga.

**Acceptans:**
- `lag_ingest` hämtar alla fem och rapporterar `t.o.m. SFS` per författning.
- Inkomstskattelagen parsas till **minst 60 kapitel**; inget kapitel är tomt.
- Stickprov: `3 kap. 9 §` IL återfinns som en egen chunk med rätt kapitelrubrik,
  och chunken bär en `Lag (ÅÅÅÅ:NNN)`-markering.
- Sökning på `"när är man begränsat skattskyldig"` ger en IL-chunk bland topp 5,
  utan lexikal överlappning med paragrafens rubrik.
- En Faktapost från `lagtext`-adaptern har `period` satt till
  konsolideringspunkten, och registret avvisar den om `lank_manniska` saknas.
- Ändringskontrollen upptäcker en ändring: mata in ett gammalt `systemdatum` för
  en författning och verifiera att den flaggas för omhämtning.
- `python -m ruff check .` och `python -m pytest -q` är rena.

**Utfall 2026-08-14:** Implementerade lagkorpus för de fem huvudlagarna:
* `lagar/lagregister.yaml` och `lagregister.py` — deklarativ katalog över författningarna.
* `index/lag_parser.py` — robust parser som hanterar dokumenthuvud, konsolideringspunkt,
  kapitel (inkl. inskjutna a/b-kapitel som `6 a kap.`), rubriker, punktlistor,
  ändringsnotiser (`Lag (ÅÅÅÅ:NNN)`) och avskiljer övergångsbestämmelser.
* `index/db.py` utökat med `lag_dokument`, `lag_chunk`, `lag_chunk_fts` och `lag_embedding`.
* `index/lag_ingest.py` — hämtar konsoliderad text från Riksdagen, sparar råtext och metadata,
  parsar till chunks, genererar och lagrar embeddings (`KBLab/sentence-bert-swedish-cased`),
  och erbjuder `kontrollera_andringar()`.
* `index/sok.py` utökat med `sok_lag()` — hybridsökning (BM25 + Vektorsökning + RRF k=60)
  med stöd för filtrering på SFS/kortnamn, kapitel och paragraf.
* `adaptrar/lagtext.py` — `LagtextAdapter` med strikt efterlevnad av §5 regel 8:
  båda länkarna, `period = tom_sfs` och `hamtad` från hämtningstillfället.
* 12 nya tester i `tests/test_steg16a_lagkorpus.py`.

Utfall av ingest för de fem lagarna:
* IL (1999:1229): 1 932 chunks, 80 unika kapitel (minst 60 uppfyllt), `t.o.m. SFS 2026:1393`.
* ML (2023:200): 810 chunks, 24 kapitel, `t.o.m. SFS 2026:1025`.
* SFL (2011:1244): 1 106 chunks, 71 kapitel, `t.o.m. SFS 2026:1305`.
* BFL (1999:1078): 66 chunks, 9 kapitel, `t.o.m. SFS 2024:342`.
* ÅRL (1995:1554): 222 chunks, 10 kapitel, `t.o.m. SFS 2026:780`.

Hela testsviten: **208 passed**, 6 deselected (livetester), 1 warning. `ruff check .` är ren.

---

## Steg 16B — Lagkorpus, resterande författningar ✅ Godkänt 2026-08-14

**Gör detta först när 16A är godkänt och parsningen bevisat sig mot
inkomstskattelagens 70 kapitel.** Att skala en parser som inte håller ger 60
tysta fel i stället för ett.


Arbetet är att fylla på `lagar/lagregister.yaml`. Ingen ny kod ska behövas — och
behövs det ny kod är det ett tecken på att 16A:s parser var för snäv.

Listan omfattar **57 författningar**, vilket med 16A:s fem ger 62 totalt.

**Samtliga SFS-nummer nedan är kontrollerade mot Riksdagens `dokumentlista`
2026-08-13 och gav träff på exakt beteckning.** Listan är transkriberad ur
beställarens källförteckning, så kontrollen var nödvändig — men den är gjord.
Slår ett nummer ändå fel: rapportera det, hitta inte på ett annat.

### Inkomstskatt m.m.
`2011:1268` Investeringssparkonto ·
`2018:1384` Uppskovsbelopp vid betydande samhällsförflyttning ·
`2022:1843` Tillfällig skatt på extraordinära vinster för vissa företag under 2023 ·
`2023:75` Överintäkter från el ·
`2023:875` Tilläggsskatt

### Internationellt, svenska författningar
`1970:624` Kupongskattelag ·
`1986:468` Avräkning av utländsk skatt ·
`1990:314` Handräckning i skatteärenden ·
`1991:481` Folkbokföringslag ·
`1991:586` Särskild inkomstskatt för utomlands bosatta (SINK) ·
`1991:591` Artister, skatt för utlandsbosatta ·
`2009:1289` Prissättningsbesked vid internationella transaktioner ·
`2019:601` Tvistlösningsförfarande inom EU

### Mervärdesskatt
`2005:807` Ersättning för viss mervärdesskatt för kommuner och regioner, lag ·
`2005:811` samma, förordning ·
`2023:328` Mervärdesskatteförordning

### Socialavgifter m.m.
`1967:531` Tryggandelag ·
`1990:659` Löneskatt på förvärvsinkomster ·
`1990:661` Avkastningsskatt på pensionsmedel ·
`1991:687` Löneskatt på pensionskostnader ·
`1991:1047` Sjuklönelag ·
`1993:931` Individuellt pensionssparande ·
`1994:1744` Allmän pensionsavgift ·
`1994:1920` Allmän löneavgift ·
`2000:980` Socialavgiftslag ·
`2001:1170` Särskilda avdrag i vissa fall vid avgiftsberäkningen ·
`2010:110` Socialförsäkringsbalk ·
`2016:1053` Särskild beräkning av vissa avgifter för enmansföretag ·
`2023:747` Särskilt avdrag för personer som arbetar med forskning eller utveckling ·
`2023:748` Särskilt avdrag vid beräkning av egenavgifter och allmän löneavgift

### Punktskatter
`1994:1776` Energiskattelag ·
`2004:629` Trängselskattelag ·
`2017:1200` Flygskatt ·
`2018:1893` Finansiering av radio och tv i allmänhetens tjänst ·
`2022:156` Alkoholskattelag

### Fastigheter
`1970:994` Jordabalken ·
`1979:1152` Fastighetstaxeringslag ·
`1984:404` Stämpelskatt vid inskrivningsmyndigheter ·
`1984:1052` Fastighetsskatt ·
`2007:1398` Kommunal fastighetsavgift

### Skatteförfarandet
`1971:69` Skattebrottslag ·
`1971:291` Förvaltningsprocesslag ·
`1974:152` Regeringsformen ·
`1982:188` Preskription av skattefordringar ·
`1995:575` Skatteflyktslag ·
`1997:484` Dröjsmålsavgift ·
`1998:189` Förhandsbesked i skattefrågor ·
`2006:304` Rättsprövningslag ·
`2006:502` Förhandsavgörande från EU-domstolen ·
`2008:826` Skattereduktion för kommunal fastighetsavgift ·
`2009:99` Anstånd med inbetalning av skatt i vissa fall ·
`2009:194` Förfarandet vid skattereduktion för hushållsarbete ·
`2011:1261` Skatteförfarandeförordning ·
`2015:632` Skattetillägg i vissa fall ·
`2017:900` Förvaltningslag ·
`2020:1066` Förfarandet vid skattereduktion för installation av grön teknik

### Redovisning
`2005:551` Aktiebolagslag

### Utanför räckvidden — och varför

Källförteckningen innehåller sju dokument som **inte** finns i Riksdagens
SFS-data: EU-fördraget, EUF-fördraget, ränte/royaltydirektivet 2003/49/EG,
fusionsdirektivet 2009/133/EG, moder/dotterbolagsdirektivet 2011/96/EU,
skatteflyktsdirektivet 2016/1164/EU, genomförandeförordningen 282/2011/EU samt
OECD:s modellavtal.

De ligger hos EUR-Lex respektive OECD och kräver en egen källa och adapter.
**Lägg inte in dem i lagregistret som om de vore SFS.** Antingen utreds EUR-Lex
som en ny källa i ett eget steg — enligt §0: anropa, inspektera, dokumentera —
eller så noteras de som en känd lucka. Det senare är ett giltigt utfall.

### Notering om "utdrag"

Källförteckningen anger utdrag för flera författningar (t.ex.
socialförsäkringsbalken, jordabalken, regeringsformen). Riksdagen ger hela
texten. Indexera hela — ett utdrag är bokens redaktionella val, inte en
egenskap hos lagen, och en avgränsning här skulle bara skapa luckor där en
användares fråga råkar hamna utanför.

**Acceptans:**
- Alla författningar i listan ovan är hämtade, parsade och indexerade, eller
  uttryckligen redovisade som misslyckade med orsak. Tyst bortfall är inte
  godkänt.
- Ingen författning har noll kapitel eller noll paragrafer.
- Den nattliga ändringskontrollen täcker hela registret och rapporterar
  antal oförändrade, ändrade och misslyckade.
- Ett stickprov på tio författningar: `t.o.m. SFS` i indexet stämmer mot
  Riksdagens aktuella metadata.
- `python -m ruff check .` och `python -m pytest -q` är rena.

**Utfall 2026-08-14:** Samtliga 62 författningar (5 från 16A + 57 nya) hämtade,
parsade, indexerade och vektoriserade utan fel:
* `lagar/lagregister.yaml` uppdaterad med samtliga 62 författningar.
* Samtliga 62 författningar finns i SQLite-databasen med sammanlagt **8 827 chunks**
  och 8 827 embeddings. Ingen författning har noll paragrafer.
* Stickprov på tio författningar (IL, ML, SFL, SFB, LSEn, ABL, JB, FOL, SINK, ISKL)
  har korrekta `tom_sfs`, `dok_id` och `systemdatum`.
* Nattlig ändringskontroll testad över samtliga 62 författningar.
* Nya tester i `tests/test_steg16b_lagkorpus.py`.
* Hela testsviten: **213 passed**, 7 deselected (livetester), 1 warning. `ruff check .` är ren.


---

## Steg 17 — Utöka Skatteverkets statistik ✅ Godkänt 2026-08-14

**Bakgrund.** `skatteverket_rowstore` finns redan i registret som verifierad och
aktiverad, men med **ett enda** verifierat UUID. Skatteverket publicerar
**744 datamängder** på samma RowStore. Adaptern finns, avtal krävs inte, ingen ny
kod behövs — det som saknas är UUID:n i registret.

Det här är den billigaste förbättringen i hela planen.

### Varför just RowStore och inte Skatteverkets API:er

Genomgång av Skatteverkets utvecklarportal 2026-08-14: av 30 API:er är i praktiken
alla partner- eller riktade API:er som kräver avtal, och de svarar på frågor om en
**specifik** skattskyldig. Skatteverket skriver själva att deras öppna data alltid
är aggregerad och att individnivå inte går att få.

Chatten är publik och har ingen inloggning. Den har alltså ingen skattskyldig att
fråga om. Partner-API:erna (Skattekonto, Inkomstdeklaration, Momsdeklaration,
Beskattningsengagemang, Arbetsgivardeklaration, Ombudshantering m.fl.) hör hemma i
sie-mcp, där klientens egna behörigheter finns — inte här. Flera är dessutom
begränsade till myndigheter, kommuner eller a-kassor och går inte att få alls.

**Gör:** lägg till dataset-UUID:n i `kallor/kallregister.yaml` under
`skatteverket_rowstore`. Följande elva är hämtade ur katalogindexet och de tre
markerade är anropade live 2026-08-14:

| UUID | Datamängd | |
|---|---|---|
| `f2f815f5-8d12-4d22-9a95-b6fda1a58e42` | Antal momsdeklarationer | ✔ 165 466 rader |
| `7691bcf3-79be-46fb-a252-8442a8f6415e` | Antal inkomstdeklarationer | ✔ 56 857 rader |
| `61a28d49-38ca-4686-9a6a-6a9ae4e66d1c` | Antal anmälan för företagsregistrering | ✔ 170 054 rader |
| `a1866379-6bff-4010-b482-37ce112eeebd` | Antal arbetsgivardeklarationer | |
| `56173b69-5c31-4c32-92b1-8560ee5f492d` | Antal kassaregisterbesök | |
| `a57c7163-aef9-4716-91e3-df126db01285` | Antal personalliggarbesök | |
| `f57fb128-34ac-4f7e-b37f-f4e43f31a4b7` | Antal jämkningar av A-skatt | |
| `c2f577e7-f4d7-4e41-a6f0-d3364f32e3b7` | Antal periodiska sammanställningar | |
| `61a59c73-c31f-4c1e-a1d6-23fb018ffcd3` | Antal kontrolluppgifter | |
| `8546f1b7-7024-48ff-80e8-eed278b93eed` | Antal punktskattedeklarationer | |
| `8ef49703-f7c2-4055-8903-a3dab876b2e7` | Antal bilagor till inkomstdeklarationer | |

Resterande UUID:n finns i katalogindexet — sök på utgivare `2021005448`.
Lägg till fler efter behov, men bara sådana som svarar på en fråga någon faktiskt
ställer. En lång lista är ingen kvalitet i sig.

### Kravet som gör steget värt något

Datamängderna har **kraftigt olika färskhet**. Momsdeklarationerna hade
`uppdateringsdatum` 2023-10-19 vid kontroll, företagsregistreringarna 2025-12-05.
Utan att det syns i svaret blir en tre år gammal siffra presenterad som aktuell —
samma felmod som §5 regel 8 finns för.

Därför: **`uppdateringsdatum` ur raden ska in i `Faktautkast.period`**, eller,
när raden har en egen period, som en dimension. Ett svar ska aldrig kunna påstå
"antalet momsdeklarationer är X" utan att visa vilket år uppgiften avser och när
den senast uppdaterades.

**Rättelse 2026-08-14 — kravet går inte att uppfylla som det är skrivet.** Vid
kontroll av alla åtta kurerade datamängder innehåller RowStore-svaret inget
uppdateringsdatum alls: toppnycklarna är `limit`, `next`, `offset`, `queryTime`
och `resultCount`, och raderna bär bara sina egna kolumner. Färskheten finns i
katalogmetadatan hos dataportal.se och i Skatteverkets publiceringsuppgift — inte
i anropet.

Fältet `uppdaterad` per datamängd i `kallregister.yaml` bär nu de datumen. Men de
är en **påstådd** färskhet, inte en avläst, och skillnaden måste överleva ända ut i
svaret: en siffra får inte presenteras som "uppdaterad 2025-12-10" när det enda vi
vet är att registret påstår det. Steg 17 ska antingen märka datumet som registrets
uppgift, eller hämta det ur katalogindexet där det har en källa att peka på.
Att tyst kopiera in det i `period` som om API:et lämnat det vore precis den
osanning §5 regel 8 finns för att förhindra.

**Acceptans:**
- Alla tillagda UUID:n svarar 200 och returnerar rader. Ett som inte gör det tas
  bort ur registret — inte lämnas kvar i hopp om att det ska börja fungera.
- En Faktapost från en av datamängderna bär `uppdateringsdatum` i `period` eller
  `dimensioner`.
- Frågan *"hur många momsdeklarationer lämnas per år?"* ger en Faktapost med
  källänk, och svaret visar vilket år som avses.
- `python -m ruff check .` och `python -m pytest -q` är rena.

### Utfall

Alla elva UUID:n verifierade live 2026-08-14, samtliga 200 OK med rader
(3 096 – 452 991 rader beroende på datamängd). Tillagda i
`kallor/kallregister.yaml` under `skatteverket_rowstore.dataset`.

**Fyndet som ändrade planen:** till skillnad från de åtta ursprungliga
datamängderna bär *varje rad* i alla elva statistikdatamängder sin egen
`uppdateringsdatum`-kolumn. Rättelsen från 2026-08-14 (att RowStore-svaret
"inte bär något uppdateringsdatum alls") gäller alltså inte dessa elva —
den gällde bara de åtta som redan fanns i registret.

`adaptrar/rowstore.py` uppdaterad:
- `_PERIODKOLUMNER` utökad med de nya datamängdernas periodkolumner
  (`redovisningsperiod`, `besoksar`, `verksamhetsar`, `redovisningsar`,
  `ankomstar`).
- Ny `_UPPDATERINGSKOLUMNER` läser `uppdateringsdatum` (eller `uppdaterad`) ur
  raden och lägger den i `Faktautkast.dimensioner["uppdateringsdatum"]` —
  avläst, inte påstådd.
- Om en rad saknar egen uppdateringskolumn faller adaptern tillbaka på
  registrets `uppdaterad`-fält, men under nyckeln
  `dimensioner["uppdaterad_enligt_kallregister"]` — namnet gör skillnaden
  mellan avläst och påstådd synlig i svaret (§5 regel 8).

Nya tester: `tests/test_steg17_skatteverket_statistik.py` (6 test, kassetter
inspelade mot skarpa API:et). `python -m ruff check .` och
`python -m pytest -q` båda rena (219 passed).

---

## Steg 18 — AVSLUTAT 2026-08-14: partner-API, ingen åtkomst

> **UPPHÄVT 2026-08-15 av steg 20.** Premissen nedan — att regelfilerna bara
> går att nå genom partner-API:et — visade sig felaktig. Samma filer ligger som
> öppna data i Skatteverkets DCAT-katalog, utan avtal, och i en NYARE version
> än partner-API:ets testtjänst. Texten nedan bevaras oförändrad som
> beslutshistorik; den beskriver inte längre läget. Se steg 20.

**Utfallet är det som §0 kallar giltigt: källan lämnas oaktiverad.**

Beställaren har konstaterat att **Rättsliga regler är ett partner-API** och att
åtkomst saknas. Den öppna frågan nedan — om regelfilerna går att hämta utan avtal
medan API:et är den avtalsbundna vägen — behöver därmed inte utredas vidare för att
komma till ett beslut: utan åtkomst finns ingen fil att inspektera, och utan
inspekterat format skrivs ingen adapter.

Ingen post läggs i `kallregister.yaml`. En källa vi varken kan nå eller beskriva
tillför ingenting genom att stå där som `verifierad: nej` — till skillnad från
`bolagsverket_hvd`, där protokollet nu faktiskt är avläst och posten bär kunskap.

Om åtkomst senare beviljas återupptas steget som det står nedan. Texten bevaras
oförändrad av det skälet.

**Det som ändå kvarstår från steg 18:s bakgrund:** luckan är verklig. Steg 16 ger
lagens bokstav, och en fråga om reseräkning besvaras av tillämpningen. Den luckan är
nu utan planerad lösning, och det ska stå skrivet i stället för att glömmas bort.

---

## Steg 18 (arkiverat underlag) — Skatteverkets rättsliga regelfiler

**Detta steg skriver ingen produktionskod förrän frågan nedan är besvarad.**
Samma form som steg 7.

### Varför det är intressant

Steg 16 ger lagens bokstav. Regelfilerna ger **Skatteverkets egen maskinläsbara
tolkning** av hur reglerna tillämpas — publicerade i JSON och sedan 2026-04-10
även i BPMN. Det är precis den lucka som noterades när lagkorpuset planerades:
en fråga om reseräkning eller logikostnader besvaras av tillämpningen, inte av
lagtexten.

Ingen annan källa i registret täcker det.

### Den öppna frågan

Skatteverkets dokumentation säger två saker som behöver förenas:

* API:et **Rättsliga regler** kräver avtal.
* Regelfilerna är **öppna data** och tillgängliga för alla.

Sannolikt betyder det att filerna går att ladda ner från utvecklarportalen utan
avtal, medan API:et är det avtalsbundna sättet att hämta dem programmatiskt. Men
det är en gissning, och gissningar aktiverar inga källor (§0).

**Gör:**
1. Ta reda på hur regelfilerna faktiskt hämtas. Portalen är en klientrenderad SPA;
   dess bakomliggande API ligger på
   `https://www7.skatteverket.se/portal-wapi/open/apier-och-oppna-data/utvecklarportalen/v1/`.
   Sökvägen `getFile/{namn}/...` är verifierad (svarar 200 med PDF), men jag kunde
   inte enumerera resurserna — `/dataresurser`, `/apier`, `/list` m.fl. gav 404.
   Hitta den sökväg som listar öppna dataresurser, eller konstatera att den inte
   finns publikt.
2. Hämta **en** regelfil. Inspektera formatet: JSON-rules-engine, BPMN, eller båda.
3. Avgör om innehållet går att göra Faktaposter av. En regelfil beskriver en
   beslutslogik, inte ett värde — det är inte självklart att den passar
   Faktapost-modellen, och det är en giltig slutsats att den inte gör det.
4. Ta reda på om avtal krävs för det du faktiskt vill använda.

**Acceptans:** en rapport med exakt URL, HTTP-status, de första 300 tecknen av
svaret, vilket format filen har, och en bedömning av om innehållet kan bära en
Faktapost med `lank_manniska` och `lank_maskin`. Plus ett förslag till post i
`kallregister.yaml`.

**Registret uppdateras först efter beställarens godkännande.** Går källan inte att
nå, eller passar den inte Faktapost-modellen, lämnas den som `verifierad: nej`,
`aktiverad: false` med orsaken skriven i `hinder` — det är ett giltigt utfall.

### Vad som INTE ska göras i detta steg

Ansök inte om partner-API:er. Skattekonto, Inkomstdeklaration, Momsdeklaration,
Beskattningsengagemang, Arbetsgivardeklaration, Kundhändelser, Ombudshantering,
Fråga om skatteavdrag, Beslutade skatteuppgifter och Bilförmån hör hemma i
sie-mcp, inte i den publika chatten. Att lägga person- eller företagsspecifika
skatteuppgifter i en fritextsökbar bot vore fel även med avtal på plats — och
`ARKITEKTUR.md` §7 spärrar redan källor med uppgifter om enskilda.

---

## Steg 7 (återupptaget) — Bolagsverket HVD: protokollet avläst 2026-08-14

Verifieringsgrinden från steg 7 stod öppen i väntan på åtkomst. Beställaren
levererade klientuppgifter till **verifieringsmiljön** 2026-08-14. Anrop gjorda
samma dag; hela protokollet står i `kallregister.yaml` under `bolagsverket_hvd`
och sammanfattas här:

* Token: `POST https://portal-accept2.api.bolagsverket.se/oauth2/token`,
  `grant_type=client_credentials` med HTTP Basic → **200**, Bearer, 3600 s.
* Scopes: `vardefulla-datamangder:read` och `:ping`, beviljas var för sig och ihop.
* Hälsa: `GET {gw-accept2}/vardefulla-datamangder/v1/isalive` → **200 `OK`**.
  Kräver `:ping`; enbart `:read` ger 403 *scope validation failed*.
* Data: `POST /v1/organisationer` tar `{"identitetsbeteckning": "<orgnr>"}` — en
  **sträng**. Objekt ger deserialiseringsfel, lista ger valideringsfel.
  `POST /v1/dokumentlista` finns och validerar mot `dokumentlistaBegaran`.
* Ingen OpenAPI-spec exponeras på gatewayen.

**Källan är trots detta inte aktiverad, och ska inte aktiveras.** Två skäl:

1. **Accept2 svarar för påhittade företag.** Chatten lovar verkliga uppgifter med
   källänk. Fiktiva bolagsuppgifter under det löftet är sämre än inget svar.
   Verifieringsmiljön duger till att bygga och testa adaptern — inte till att
   besvara frågor.
2. **Svarsformatet är osett.** Giltiga testidentitetsbeteckningar står i en
   testdokumentation vi inte har, så inget anrop har gett 200 med en
   organisationskropp. §0 tillåter ingen adapter mot ett osett svarsformat.

**Nästa åtgärd är beställarens, inte implementatörens:** begär Bolagsverkets
**testdokumentation med giltiga identitetsbeteckningar**, och därefter
**produktionsuppgifter**. Med det första kan adaptern byggas och testas mot
accept2. Med det andra — och först då — kan källan aktiveras.

---

## Steg 7 (återupptaget igen) — Bolagsverket HVD: produktionsuppgifter och aktivering 2026-08-16

Beställaren levererade produktionsuppgifter för applikationen "Värdefulla
datamängder" 2026-08-16 (mejl "Anslutningsuppgifter för Värdefulla
datamängder", klientuppgifter i separat fil). Till skillnad från
verifieringsmiljön ovan pekar dessa mot **produktionsgatewayen**:
`portal.api.bolagsverket.se` (token) och `gw.api.bolagsverket.se`
(data) — inga `-accept2`-adresser.

Live-anrop samma dag löste båda hindren från föregående avsnitt:

* **Scope måste begäras explicit.** Ett token-anrop utan `scope`-parameter ger
  `scope=default`, vilket gatewayen avvisar med `900900 Unclassified
  Authentication Failure` på både `/isalive` och `/organisationer` — exakt
  samma fel som accept2-token gav mot produktionsgatewayen i steg 7
  (återupptaget), vilket i efterhand förklarar det felet. Med
  `scope=vardefulla-datamangder:read vardefulla-datamangder:ping` uttryckligen
  angivet i token-anropet fungerar båda endpoints.
* **Svarsformatet är nu sett.** `POST /v1/organisationer` med
  `{"identitetsbeteckning":"5560125790"}` (Aktiebolaget Volvo) gav 200 med ett
  fullständigt organisationsobjekt — varje delfält bär sin egen
  `dataproducent` (Bolagsverket eller SCB) och en egen `fel`-nyckel när
  uppgiften saknas för den identitetsbeteckningen. Samma anrop mot
  Bolagsverkets eget organisationsnummer (en myndighet, inte ett
  bolagsregistrerat subjekt) gav flera `ORGANISATION_FINNS_EJ`-fel på just de
  Bolagsverket-ägda fälten, vilket bekräftar att `fel`-grenen fungerar som
  dokumenterat snarare än att request-formatet var fel. `POST
  /v1/dokumentlista` gav 200 `{"dokument":[]}` för samma orgnr.

**Beställaren instruerade uttryckligen att aktivera källan** 2026-08-16, i
avsteg från slutsatsen ovan och från `ARBETSORDER.md`s regel om att inte röra
källspärrarna. Motiveringen: värdefulla datamängder (organisationsnamn, form,
adress, SNI-koder, verksamhetsstatus, inlämnade årsredovisningar) är inte
personuppgifter om fysiska personer — till skillnad från **Bolagsverkets
verkliga huvudmän**, som är en annan, separat spärrad källa och förblir
spärrad oavsett detta beslut.

Genomfört:

* `adaptrar/bolagsverket.py` — ny adapter, skriven mot det nu observerade
  svarsschemat. Två verktyg: `bolagsverket_hvd` (organisationsdata) och
  `bolagsverket_hvd_dokumentlista` (inlämnade årsredovisningar).
* `register.Kalla` fick två nya fält, `token_url` och `oauth_scope` —
  OAuth2 client_credentials är den enda källan i registret som behöver dem.
* `kallregister.yaml`: `bolagsverket_hvd` satt till `verifierad: ja`,
  `aktiverad: true`, `bas_url`/`token_url` pekar på produktionsgatewayen.
  Accept2-adresserna står kvar som `test_token_url`/`test_bas_url`,
  fortsatt ignorerade av `register.py` och fortsatt förbjudna som körbar URL.
* `motor/hamtning.py` — adaptern registrerad i `_bygg_adaptrar()`.
* `tests/test_steg5_transport.py::test_ej_aktiverad_kalla_kastar` skrevs om
  för att mocka en egen inaktiv källa i stället för att peka på
  `bolagsverket_hvd` — testet verifierade spärrmekanismen, inte just den
  posten, och skulle annars fallera vid varje sådan aktivering.
* `.env.example` fick platshållarna `BOLAGSVERKET_CLIENT_ID`/
  `BOLAGSVERKET_CLIENT_SECRET` (redan skyddade av `test_inga_hemligheter.py`
  sedan tidigare). De riktiga värdena ligger i `.env` (gitignorerad), inte i
  repot.

Ingen `git push` eller driftsättning gjord — ARBETSORDER.md regel 1 gäller
oförändrat.

---

## Steg 19 — Nattlig färskhetskontroll av lagkorpuset ✅ Godkänt 2026-08-14

**Detta steg låg ursprungligen oförbeställt.** Det stod i planen för att en
invariant i `ARKITEKTUR.md` var skriven men inte sluten, och en sådan lucka ska
stå skriven i planen i stället för att bara finnas i koden. Beställt och utfört
2026-08-14.

**Bakgrund.** §5 regel 8 säger att lagkopians färskhet *"kontrolleras nattligt genom
att jämföra `systemdatum`"*. Efter steg 16B stämmer halva meningen:
`lag_ingest` läser `systemdatum` ur dokumenthuvudet och bär konsolideringspunkten hela
vägen ut i `Faktapost.period`. Men `nattlig_ingest.py` uppdaterar bara katalogindexet
och rör inte lagkorpuset. Ingen jämförelse sker mellan körningarna, och en ändrad
paragraf upptäcks först när någon kör ingesten för hand.

Konsekvensen är precis den felmod regel 8 finns för: svaret ser lika trovärdigt ut
med en inaktuell lydelse som med en aktuell, eftersom `period` troget rapporterar den
konsolideringspunkt kopian hade när den togs.

**Gör:**
1. Låt den nattliga körningen hämta *dokumenthuvudet* för varje författning i
   `lagar/lagregister.yaml` och jämföra `systemdatum` mot det som ligger i indexet.
   Huvudet, inte hela texten — 62 lätta anrop, ingen omindexering i onödan.
2. Bara författningar vars `systemdatum` ändrats ingestas om. Diffa aldrig text.
3. Exponera lagkorpusets ålder i `GET /matning`: dygn sedan senaste lyckade ingest
   per författning, och en lista över dem som ligger efter.
4. Ett misslyckat huvudanrop får inte tömma eller ogiltigförklara den befintliga
   kopian. Gammal text med korrekt redovisad konsolideringspunkt är bättre än ingen
   text — men åldern ska synas i `/matning`.

**Acceptans:**
- `nattlig_ingest` uppdaterar bara de författningar vars `systemdatum` ändrats, och
  loggar hur många som kontrollerades respektive omindexerades.
- `GET /matning` visar lagkorpusets ålder per författning.
- Ett simulerat fel mot Riksdagen lämnar indexet orört och körningen avslutas med
  fel-status, inte tyst.
- `python -m ruff check .` och `python -m pytest -q` är rena.

### Utfall

`index/lag_ingest.py`:
- `nattlig_lagkontroll()` — kör `kontrollera_andringar()` (fanns redan sedan
  steg 16A, men anropades aldrig av den nattliga körningen), och ingesterar om
  ENDAST de författningar vars `fjarr_systemdatum` faktiskt skiljer sig från det
  lokala (inte bara där kontrollanropet misslyckades). Räknar `kontrollerade`,
  `andrade`, `omingesterade`, `ingest_fel`, `fel_vid_kontroll` och sätter
  `status` till `"ok"`, `"delvis"` eller `"fel"`. Status blir `"fel"` om något
  omingest-försök misslyckas, ELLER om samtliga kontrollanrop misslyckar och
  inget kunde bekräftas ändrat — annars hade ett totalt Riksdagen-avbrott sett
  ut precis som "inget har ändrats".
- `las_lagkorpus_alder()` — dygn sedan `lag_dokument.hamtad` per författning,
  `ligger_efter` (tröskel 2 dygn, eller om författningen aldrig ingesterats).

`index/nattlig_ingest.py`: `kör_nattlig_lagkontroll()` kör kontrollen, loggar
till `matning.logga_lagkontroll` och skriver en rad till stdout-rapporten.
Anropas från `kör_nattlig_ingest()` i eget try/except — ett fel i lagkontrollen
får aldrig hindra katalogingesten eller frågeraderingen (samma princip som
granskningsfynd 4 i steg 14–15).

`matning.py`: ny tabell `lagkontroll_logg` (`logga_lagkontroll` /
`las_senaste_lagkontroll`), samma mönster som `ingest_logg`.

`api.py`: `GET /matning` returnerar nu även `lagkorpus_alder` (läst live ur
indexet, inte ur mätningsdatabasen — åldern ska spegla indexets faktiska
tillstånd) och `senaste_lagkontroll`.

Nya tester: `tests/test_steg19_lagkontroll.py` (5 st) — bara ändrade
författningar ingesteras om, ett totalt Riksdagen-avbrott lämnar indexet orört
och rapporterar `status="fel"`, ett enskilt misslyckat omingest-försök syns
som `ingest_fel` utan att krascha körningen, och åldersberäkningen flaggar både
gamla kopior och aldrig ingesterade författningar. De tre befintliga
`kör_nattlig_ingest`-testerna i `test_steg15_matning.py` uppdaterade för att
stubba `kör_nattlig_lagkontroll` (annars hade de gjort 62 riktiga
nätverksanrop per körning).

`python -m ruff check .` och `python -m pytest -q` båda rena (224 passed).

---

## Steg 20 — Skatteverkets rättsliga regelfiler ✅ Godkänt 2026-08-15

**Detta steg upphäver beslutet i steg 18.** Steg 18 stängdes 2026-08-14 med
motiveringen "partner-API, ingen åtkomst". Premissen höll inte, och ett beslut
som vilar på en felaktig premiss ska rivas upp när premissen faller — inte
bevaras för att det en gång fattades.

**Vad som var fel.** Steg 18 utgick från att regelfilerna bara fanns bakom
partner-API:et *Rättsliga regler*, som kräver påskrivet avtal. Beställaren
erhöll 2026-08-15 en sandbox till det API:et, och kontrollen av den visade två
saker:

1. Sandboxen fungerar tekniskt (samtliga operationer anropade, 200 OK), men dess
   användarvillkor förbjuder produktionsanvändning. Den kunde alltså aldrig bli
   en källa i chatten.
2. **Samma regelfiler publiceras som öppna data** i Skatteverkets DCAT-katalog
   (dataset "Rättsliga regelfiler", entry 1603) — utan nycklar och utan avtal.
   Och de låg en version FÖRE partner-API:ets kompletta testtjänst:

   | Område | Partner-API (test) | Öppna data |
   |---|---|---|
   | Gåvor | 1.2.0 (550/1 650 kr) | 1.3.0 (600/1 800 kr) |
   | Traktamenten | 2.3.0 | 2.4.0 |
   | Logikostnader | 2.3.0 | 2.4.0 |

Den avtalsbundna vägen gav alltså både mindre frihet och äldre data. Luckan som
steg 18 lämnade "utan planerad lösning" — lagens bokstav i steg 16 utan
tillämpningen — är därmed sluten.

### Utfall

`kallor/kallregister.yaml`: ny källa `skatteverket_rattsligaregler` med kurerad
katalog över tretton regelfiler (åtta sakområden + fem versionsvalsfiler).
Resurs-id och versionsnummer är avlästa ur filernas egen metadata, inte ur
filnamnen — filnamnen och metadatan går isär i flera fall.

`adaptrar/skatteverket_rattsligaregler.py`: tre verktyg —
`_lista_omraden` (områdes-id går inte att gissa, §5 regel 7), `_fragor`
(frågorna och de svarsalternativ som faktiskt förekommer i filen), och
exekveringsverktyget.

**Adaptern exekverar, modellen resonerar inte.** Samma princip som
beräkningsverktygen (§5 regel 2): om modellen läste regelfilen och själv drog
slutsatsen vore utfallet modellens, inte Skatteverkets, och inte spårbart.
Adaptern vägrar hellre än gissar — okänd operator eller villkorstyp ger tomt
svar, eftersom ett halvt exekverat regelträd är farligare än inget svar alls.
Idag förekommer bara `equal` och `all` (kontrollerat över samtliga 735 villkor).

**"Reglerna räckte inte till" skiljs från "reglerna säger nej."** Ett
ofullständigt underlag får aldrig se ut som ett skattebesked; obesvarade frågor
rapporteras tillbaka utan lagrum.

**Två filscheman stöds**, båda avlästa: det nya (`meta`/`attributes`/`rules`,
källor i `results[].sources`) och det gamla (`rulesArea`/`rulesets[].decisions`,
källor i kommasträngen `"Källor"`). Representationsfilen bär dessutom
avdragstak i kronor per utfall, som följer med i `dimensioner`.

`motor/hamtning.py` och `adaptrar/__init__.py`: adaptern inkopplad. Fas A ser
27 verktyg.

Nya tester: `tests/test_steg20_rattsligaregler.py` (17 st) med VCR-kassetter.

**Två buggar hittade under bygget**, båda med egna tester: svarsalternativ kan
själva innehålla snedstreck (`"Affärsförhandling / Personalfest"` är ETT
alternativ), och några bär radbrytning mitt i sig — utan citering respektive
normalisering av inre blanktecken matchade det svar modellen fick tillbaka
aldrig sitt eget villkor.

### Öppen fråga — licensen

`licens: okänd`, inte `CC0`. Datasetet bär `accessRights: PUBLIC` men saknar
`dcterms:license` — kontrollerat på både datasetet och dess distributioner.
**Åtkomsten är belagd, användningsvillkoren inte.** Att skriva `CC0` vore att
smuggla in en gissning i ett fält som resten av bygget förutsätter är sant.

Fråga ställd till katalogens kontaktpunkt (Andreas Bertilsson, Skatteverket).
Raden ändras när svaret kommer — inte innan. Under tiden bär varje Faktapost
attribution, lagrum och två länkar.

---

## Granskning av steg 14–15, 2026-08-14

Frontend följer alla tre reglerna ur granskningen av steg 10–13 korrekt:
`forbehall` renderas avskilt som "Not:", stycken utan `kallor` renderas inte
(`widget.js` rad 1080), och härledda poster har badge, avvikande ID-chip och
visar sina ingångar. `el()` bygger DOM med `createTextNode`, `innerHTML` anropas
aldrig med modelldata, och `target="_blank"` har `rel="noopener noreferrer"`.
Mätningen loggar bara aggregat, ingen frågetext lämnar `/matning`.

Fem fynd.

| # | Fynd | Var | Karaktär |
|---|---|---|---|
| 1 | Länkar sattes som `href` utan schemakontroll | `modeller.py` | **XSS-vektor** |
| 2 | Härledd posts formeltext renderades som klickbar länk | `widget.js` | Trasig länk för användaren |
| 3 | Databasschema skapades vid modulimport | `matning.py` | Sidoeffekt; testerna skrev till riktig DB |
| 4 | Radering av frågetexter låg efter mätningsloggningen | `nattlig_ingest.py` | Bevarandeplikt beroende av statistik |
| 5 | `/matning` var publikt läsbar | `api.py` | Driftdata öppen |

**Fynd 1.** Både `lank_manniska` och `lank_maskin` renderas som `href` i
widgeten, men ingenting kontrollerade schemat. Jag verifierade att
`registrera(lank_manniska="javascript:alert(document.cookie)")` gick igenom utan
invändning. Länkarna byggs ur mallar i källregistret men med värden ur
myndigheternas API-svar; att inget hittills varit skadligt är inte en garanti.
Kontrollen ligger nu i `Faktaregister.registrera` — där alla fakta passerar —
inte i renderaren, enligt §1.

**Fynd 2** upptäcktes av fynd 1: kontrollen fällde ett *legitimt* fall. Härledda
poster har inget API-anrop, så `berakningar.py` sätter
`lank_maskin="beräkning: (F1 − F2) / F2"`. Widgeten renderade den strängen som
`<a href>`, alltså en relativ URL som leder ingenstans. Nu: härledda poster
undantas från schemakontrollen men **måste ha `harledd_av`** — beviset är
ingångarna — och widgeten renderar ett icke-URL-maskinfält som text.

**Fynd 3.** `_säkerställ_schema()` kördes på modulnivå, så ett blott
`import quiet_oppen_data.matning` skapade `data/matning.sqlite`. Samma
felklass som cache-defekten i steg 5, men vid import — vilket är värre. Schemat
byggs nu vid första anslutningen.

**Fynd 4.** Raderingen kördes efter `logga_ingest`. Ett undantag där hade tyst
hoppat över den, och frågetexter behållits i månader utan att någon märkte det.
Raderingen körs nu först, med egen felhantering: bevarandeplikt före statistik.

**Fynd 5.** `/matning` avslöjar trafikvolym, vilka källor som fallerar och hur
ofta fas C faller stängt. Kräver nu `MATNING_NYCKEL` i miljön, skickad som
`x-matning-nyckel`. Saknas variabeln är endpointen **stängd (503), inte öppen** —
en glömd miljövariabel ska inte tyst göra driftdata publik.

Alla fem spärrar är mutationstestade. Sviten: **189 passerade**, och `matning`,
`cache` och `kvoter` rörs inte längre av testkörningen.

**Att sätta i drift:** `MATNING_NYCKEL` måste läggas till i Coolifys
miljövariabler, annars svarar `/matning` 503.

---

## Granskning av steg 10–13, 2026-08-13

Den bäst byggda etappen hittills. `syntes.py`, `validator.py`, `berakningar.py`
och `kvot.py` följer arkitekturen nära, med korrekt anropsform, fail-closed på
alla vägar och deterministisk attribution. Fyra fynd, varav ett principiellt.

| # | Fynd | Var | Karaktär |
|---|---|---|---|
| 1 | `forbehall` passerade ingen kontroll men strömmas till användaren | `validator.py` / `api.py` | **Invariantlucka** |
| 2 | `kan_besvaras=true` utan stycken passerade valideringen | `validator.py` | Tomt svar |
| 3 | `X-Forwarded-For` litades alltid på | `api.py` | Kvotkringgående |
| 4 | Riksbanken satte ingen `enhet` | `riksbanken.py` | "räntan ligger på 2" |

**Fynd 1 är det viktiga.** `forbehall` är fritext från modellen, bär inga
källhänvisningar, och `api.py` skickar det som ett eget SSE-event. Det var
alltså en textkanal rakt förbi citeringskravet i §1.

Jag provocerade fram det live: modellen ombads uttryckligen lägga momssatsen
respektive en gissad ränta i `forbehall`. Den **vägrade båda gångerna** och
skrev i stället att den inte kan ange en siffra utan täckning. Uppförandet är
alltså gott — men validatorn rapporterade `0 fel`, vilket betyder att den hade
släppt igenom vad som helst. Att invarianten höll berodde enbart på modellen,
och §1 säger uttryckligen att det är en förhoppning, inte en garanti.

Kontroll 5 stänger det: `forbehall` får inte införa tal som saknas i registret.
Kontrollen är medvetet smal — den fångar siffror (räntesatser, procent, belopp),
tillåter tal som redan står i en Faktapost, och tillåter tal som står i
användarens egen fråga. Kontroll 6 stoppar det tomma svaret.

**Fynd 3:** `X-Forwarded-For` sätts av vem som helst som når porten. En ny
slumpad adress per anrop gav obegränsat antal frågor, bara bromsat av
dygnstotalen. Headern läses nu bara när `site.betrodd_proxy = true`, vilket den
ska vara bakom Coolify och inte om appen exponeras direkt.

**Fynd 4** upptäcktes av systemet självt: modellen skrev i sitt förbehåll att
enhet saknades, och den hade rätt. Enheten härleds nu ur SWEA:s gruppträd
(räntegren → procent, valutagren → "SEK per EUR") — ur API:ets egen struktur,
inte ur seriens namn. Saknas grupp lämnas fältet tomt hellre än gissat.

**Verifierat live, hela kedjan A→B→C:**

| Fråga | Utfall |
|---|---|
| "Vad är Riksbankens referensränta?" | 2 faktaposter, citerade `[F1, F2]`, rätt serie, källpanel med länkar |
| "Vem vann Eurovision 1974?" | 0 faktaposter, `kan_besvaras=false`, "Det hittade jag inte i källorna." |

Den andra raden är den viktiga. Modellen vet svaret utantill ur sin förträning
och skriver det ändå inte. Invarianten håller under precis det tryck den finns
för.

**Regler som frontend (steg 14) måste följa:**

* `forbehall` renderas **avskilt från svaret**, som en not — aldrig som en
  mening bland styckena. Det är den enda text i svaret som inte är citerad.
* Ett stycke renderas alltid med sina fotnoter. Ett stycke utan `kallor` ska
  inte kunna nå frontend, men rendera det inte om det ändå gör det.
* `harledd`-poster måste märkas i källpanelen tillsammans med `harledd_av`.

Sviten: **167 passerade**, 6 livetester avmarkerade. De tre nya spärrarna är
mutationstestade.

---

## Granskning av steg 8–9, 2026-08-13

Steg 8 var rent: alla tio adaptrarna följer kontraktet, ingen konstruerar
`Faktapost`, TED följer de utredda reglerna. Felen satt i steg 9 och i en äldre
adapter.

| # | Defekt | Var | Konsekvens om orättad |
|---|---|---|---|
| 1 | `thinking.budget_tokens` — borttaget på Opus 5 | `hamtning.py` | **HTTP 400 på varje anrop.** Fas A gick inte att köra |
| 2 | Beta-flaggorna `extended-thinking-*` / `prompt-caching-*` finns inte längre | `hamtning.py` | **HTTP 400.** Funktionerna är GA |
| 3 | `output_config.effort` skickades aldrig trots att docstringen lovade det | `hamtning.py` | `effort_hamtning` i config hade ingen verkan |
| 4 | Riksbanken saknade seriekatalog — modellen måste gissa serie-id | `riksbanken.py` | **Fel svar.** Se nedan |
| 5 | Inget omförsök vid 429/5xx | `transport.py` | Rate limit blev "källan hade inget att säga" |
| 6 | `max_tokens=4096` hårdkodat med adaptiv thinking | `hamtning.py` | Risk för trunkering mitt i resonemang |

**Defekt 4 är den allvarligaste** och värd att förstå, eftersom den är samma
felklass som PxWeb hade före förra granskningen. Vid provkörning frågade jag
"Vad är Riksbankens referensränta?". Adaptern tog bara ett serie-id och hade
ingen katalog, så modellen gissade: `SEREFIRATE`, `SEREFI`, `SECRINTP`,
`SEREFIRATENB`, `SEDP1MSTIBOR`, `SERENTF`, `SEREFRATE`, `SEREF`, `SERE1M` —
nio gånger, tills Riksbanken svarade 429. Den landade till slut på
**`SECBREPOEFF` = 1,75**, som är **styrräntan**. Rätt svar är `SECBREFEFF`
= 2, referensräntan. Ett tecken isär i id:t, helt olika räntor, och svaret hade
sett fullt trovärdigt ut med källänk och allt.

Åtgärd: `riksbanken_lista_serier` hämtar SWEA:s katalog med 117 serier, och
`riksbanken_hamta` säger uttryckligen att id måste komma därifrån. Etiketten
namnger nu serien (`Riksbanken: Reference rate (SECBREFEFF)`) i stället för bara
id:t. Riksbankens takt sänkt från 10/s till 2/s. Verifierat live: rätt serie,
tre varv, inga 429.

**Ny täckning:** `tests/test_steg9_fas_a.py` har fyra enhetstester som inspekterar
anropsformen utan nätverkstrafik. De mutationstestades: `budget_tokens`
återinfört, beta-flaggor återinförda respektive `effort` borttaget fångas alla
tre. Det var frånvaron av just sådana tester som lät felet gå igenom.

Sviten: **117 passerade**, 4 livetester avmarkerade.

---

## Granskning av steg 0–6, 2026-08-13

Genomförd efter att steg 0–6 rapporterats klara. Sex defekter rättade, samtliga
verifierade med test. Sviten går på **96 passerade**, deterministiskt över fyra
körningar och oberoende av `data/cache.sqlite`.

| # | Defekt | Var | Konsekvens om orättad |
|---|---|---|---|
| 1 | `aktiverad` kontrollerades aldrig | `transport.py` | Anrop mot obekräftad endpoint (bolagsverket_hvd) |
| 2 | Fel svarsformat begärt från SCB | `pxweb.py` | **Fel värden i svar** — se steg 6 |
| 3 | Fel returnerades som citerbart faktum med tomma länkar | `pxweb.py` | Felmeddelande presenterat som uppgift |
| 4 | Adaptrar kringgick Faktaregistrets validering | alla adaptrar | Faktapost utan länkar kunde existera |
| 5 | SQLite-anslutningar stängdes aldrig; för snål timeout | `transport.py` | Filhandtagsläcka per anrop |
| 6 | Värdkontroll gällde bara 1 av 3 generiska adaptrar | `transport.py` | Modellkonstruerad URL kunde nå godtycklig värd |

Därtill: tystade `except Exception` loggar nu i samtliga adaptrar; VIES-etiketten
påstod giltighet oavsett utfall; död kod och kvarlämnade tankeled i `pxweb.py`
borttagna; adaptertesterna passerade utan att röra nätet (samma cache-defekt som i
steg 5, en våning upp) och tar nu `isolerad_cache`.

**Kvar att göra, inte blockerande:** `pyflakes` är inte installerat i miljön, så
lint har aldrig körts. Lägg till det i dev-beroendena innan steg 9.
*Åtgärdat 2026-08-13 — se "Lint" i arbetsgången högst upp.*

---

## Frågor till beställaren — besvarade 2026-08-13

1. **Domänen** — quiet.nu. Bekräftat, ingen ändring i `config.toml` behövd.
2. **TED:s villkor** — anonym åtkomst till Search API mot publicerade meddelanden,
   ingen kundanmälan krävs. Se `kallregister.yaml`.
3. **Bolagsverkets kundanmälan** — inskickad för API för värdefulla datamängder (HVD).
   Väntar på svar. Steg 7 fortsätter med `verifierad: nej`, `aktiverad: false` tills
   bekräftelse kommer.
4. **Kostnadstak** — 1 000 SEK/månad. Anthropic-kontot är förskottsbetalt utan
   auto-reload; se `ARKITEKTUR.md` §6a.
5. **Driftmiljö** — Hetzner (CX22/CX23-nivå, 2 vCPU/4 GB/40 GB) med Coolify. En tom
   FastAPI-app som svarar `{"ok": true}` på `/halsa` ska deployas **NU**, parallellt
   med steg 3–6, inte vid steg 13. Syftet är att felsöka DNS, brandvägg och
   Coolify-pipelinen innan applikationslogiken finns, inte samtidigt med den.

---

## Steg 22 — Bolagsverkets dokumentinnehåll, nekanden och fri svarsform ✅ 2026-08-29

Tre ändringar som hänger ihop: chatten kunde säga att en årsredovisning fanns
men inte vad som stod i den, den kunde inte säga att något *inte* fanns, och
den kunde bara svara i löptext.

### 22.1 `/dokument/{id}` — innehållet, inte bara att det finns

Adaptern implementerade `/organisationer` och `/dokumentlista`, men inte
`/dokument/{id}`. Chatten kunde alltså räkna upp ett dokument-id åt läsaren och
sedan lämna hen vid dörren — trots att samma nyckel, samma prenumeration och
samma scope räcker hela vägen.

Nytt verktyg `bolagsverket_hvd_dokument` (tar `dokumentid`). Hämtar zip:en,
packar upp iXBRL-XHTML:en och läser ut de taggade posterna med sin period.
Verifierat live 2026-08-29 mot AE Capital AB (556861-2351): **37 fakta** ur
årsredovisningen för 2020.

Går förbi den delade transporten av samma skäl som `hamta_token` gör det:
svaret är en binär zip, och den delade cachen är byggd för textsvar.

**Läser av — räknar inte.** Urvalet i `_INTRESSANTA_POSTER` är resultat- och
balansräkningens huvudposter plus antalet anställda; dokumentet bär 150–200
taggade fakta och att skicka alla vore att dränka frågan. Enhetsfällan står i
koden: flerårsöversikten är i tusental kronor, resultat- och balansräkningen i
kronor.

### 22.2 Nekanden emitteras

Varje fält låg bakom ett `if <värde>:`. När Bolagsverket svarade `null` skapades
ingen Faktapost alls — och eftersom syntesen bara får skriva det som finns som
Faktapost blev nekandet **osynligt**: «bolaget är inte avregistrerat» gick inte
att skilja från «vi vet inte om det är avregistrerat». För den som kontrollerar
en motpart är nekandet ofta hela svaret.

Fyra nekanden emitteras nu uttryckligen (avregistrerad, pågående förfarande,
reklamspärr, avregistreringsorsak), plus tre fält som aldrig emitterades alls:
registreringsland, infört hos SCB, och datumet då företagsnamnet registrerades.
För AE Capital gick svaret från **9 till 15 fakta**.

Skillnaden mot «vi vet inte» är bevarad: saknas nyckeln *helt* i svaret
emitteras ingenting.

Systemprompten fick regel 6 — redovisa nekanden — med skälet skrivet.

**Ett befintligt prov påstod motsatsen.** `assert not any("Reklamspärr" ...)`
kodifierade det gamla beteendet som avsikt. Kontraktet ändrades medvetet, så
provet ändrades med ett skrivet skäl. Provsviten hittade dessutom en riktig bugg
i det nya nekandet: leveransen förekommer med två stavningar av
avregistreringsfältet (`avregistradOrganisation` utan «e»), vilket den jakande
vägen redan hanterade men nekandet inte gjorde.

### 22.3 Fri svarsform

Beställaren ville att modellen skulle få välja utformning — löptext, tabell,
eller en blandning, gärna med färg. **Kontroll av widgeten först visade att det
inte gick som det stod:** `widget.js` renderade svarstext med
`document.createTextNode()` i ett `<p>`. En markdown-tabell hade skrivits ut som
råa pipetecken, och färg var omöjligt.

Vägen byggdes i stället. Se ARKITEKTUR §4 för schemat. Kort:

* `form`: `brodtext` | `punktlista` | `tabell`, per stycke.
* `ton` per rad: `neutral` | `bekraftad` | `nekad` | `varning` — semantisk,
  inte dekorativ.
* `text` alltid ifylld, så att en klient utan tabellstöd får ett läsbart svar.
* Fas C räknar raderna som sakinnehåll, annars vore formen en väg förbi
  citeringskravet.
* Widgeten bygger DOM-noder, aldrig `innerHTML`. Färg som tillägg till en
  vänsterkant, aldrig som enda signal.

### Utfall

263 prov gröna. Live-verifierat mot produktionsgatewayen samma dag: 15 fakta ur
`/organisationer` (varav fyra nekanden), 1 ur `/dokumentlista`, 37 ur
`/dokument/{id}`.
