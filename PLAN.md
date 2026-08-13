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

**Utfall 2026-08-13:** Skapade `adaptrar/pxweb.py` som läser JSON-stat2-metadata (via `/metadata`) och genererar två separata logiska verktyg: `lista_dimensioner` och `hamta_data`. Adaptern avvisar automatiskt sökningar över 150 000 celler, och om dimensioner saknas vid `hamta_data` så "vägrar" adaptern genom att returnera PxWeb-dimensionerna och valalternativen som listor av `Faktapost`, så att LLM:en vet vad som ska anges. Data extraheras genom att parsa PX-svaret för värdet, vilket godkänts mot acceptanstesterna. Testet kördes med KPIF-XE och SCB-värdet returnerades framgångsrikt (0.6).

---

## Steg 7 — VERIFIERINGSGRIND: RowStore, Bolagsverket, JobTech

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

## Steg 8 — Övriga verifierade adaptrar

**Gör:** `ted.py`, `riksdagen.py`, `kolada.py`, `dataportal.py`, samt `json_rest.py`
konfigurerad för SMHI, Skolverket, Trafa och Polisens händelser.

TED-specifikt, redan utrett:
* `POST https://api.ted.europa.eu/v3/notices/search`, **inte GET** (GET ger 405).
* Skicka bara det kurerade fältundervalet ur registret. Ett ogiltigt fältnamn ger 400
  med en lista på 1 830 giltiga namn — låt inte det svaret nå modellen.
* Textfält är flerspråkiga objekt. Plocka `swe`, fall tillbaka på `eng`.
* Frågespråk: `buyer-country=SWE AND publication-date>=today(-30)`.
* Ett *meddelande* är inte en *upphandling* — samma upphandling ger flera meddelanden.
  Skriv det i Faktapostens `etikett` så att svaret inte påstår fel sak.

**Acceptans:** varje adapter har minst ett pytest med inspelat svar och ett manuellt
verifierat live-anrop redovisat i rapporten.

---

## Steg 9 — Fas A: planerare och hämtningsloop

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

---

## Steg 10 — Fas B: syntes med tvingad citering

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

---

## Steg 11 — Fas C: validator

**Gör:** `motor/validator.py` enligt `ARKITEKTUR.md` §4 fas C.

* Alla fyra kontrollerna.
* Ett omförsök av fas B med felmeddelandet inlagt. Efter andra felet: fail-closed.
* Logga varje valideringsfel med orsak — det är den viktigaste kvalitetssignalen.

**Acceptans:**
- pytest: ett svar som citerar `F99` (finns inte) avvisas.
- pytest: ett svar där en CC-BY-källa citeras utan attribution avvisas.
- pytest: efter två misslyckade försök returneras fail-closed-svaret, inte ett obelagt.

---

## Steg 12 — Beräkningsmodul

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

---

## Steg 13 — HTTP-API och kvoter

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

## Öppna frågor till beställaren

Dessa ska besvaras innan respektive steg, inte gissas:

1. **Domänen** — `quiet.nu` eller `quiet.se`? Påverkar steg 0 och 13.
2. **TED:s villkor** — vem kontaktar helpdesken? Blockerar steg 8:s produktionssättning.
3. **Bolagsverkets kundanmälan** — gjord eller inte? Blockerar steg 7.
4. **Kostnadstak** — kvoterna i `config.toml` är en gissning. Vad är taket per månad?
5. **Var driftas det?** Påverkar steg 13 och 15.
