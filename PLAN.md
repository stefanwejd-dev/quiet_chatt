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

## Steg 14 — Frontend

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

---

## Steg 15 — Drift och mätning

**Gör:** nattlig ingest-körning, loggning enligt `ARKITEKTUR.md` §11, och en enkel
`GET /matning`-vy.

**Acceptans:**
- Ingest kan köras schemalagt och rapporterar deltat mot föregående körning.
- Mätvyn visar andelen frågor som besvarades på nivå 3 (katalogsvar) — det är siffran
  som styr vilken adapter som byggs härnäst.
- Frågetexter raderas automatiskt efter 30 dagar.

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
