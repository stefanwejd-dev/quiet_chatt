# Arkitektur — Quiet Öppen Data

Chattfunktion på quiet.nu som besvarar frågor **enbart** med uppgifter från offentligt
finansierade organisationers API:er, och som redovisar varje uppgift med fotnot och
klickbar källänk.

Allt hämtas i realtid, med ett undantag: lagtexten speglas lokalt (§3.2b). Den
kopian måste bära sin färskhetsstämpel hela vägen ut i svaret — se §5 regel 8.

Version 1.1 · 2026-08-14 · Författare: arkitekturunderlag för implementerande kod-AI

---

## 0. Två saker att bekräfta innan bygget

**Domänen.** Systemet skrivs mot `quiet.nu` (Quiet Numbers). `quiet.se` är en annan sajt
som inte tillhör beställaren. Domänen ligger som en enda rad i `config.toml`
(`site.domain`) och används bara för CORS-allowlist och attribution — byt där om det
visar sig fel.

**Källverifiering.** Varje källa i `kallor/kallregister.yaml` har fältet `verifierad`.
`ja` = anropad live och bekräftad 2026-08-13. `nej` = sökväg eller anropsform är inte
bekräftad. **Implementatören får inte aktivera en källa märkt `nej` förrän den anropats
och svaret inspekterats.** Registret är sanningen; gissa aldrig en endpoint.

---

## 1. Designprincip

Systemet bygger på en enda invariant, och allt annat följer av den:

> **Ingen mening i ett svar får innehålla en uppgift som inte kommer från en
> registrerad Faktapost. Modellen får aldrig svara ur eget minne.**

Invarianten upprätthålls **strukturellt, inte genom instruktioner**. En systemprompt som
säger "hitta inte på" är en förhoppning. Här görs det omöjligt: syntessteget får aldrig
se frågan och sin egen världskunskap i samma sammanhang som en fri textutgång. Det får en
lista Faktaposter och ett JSON-schema som kräver minst en källhänvisning per stycke, och
en validator som kastar svaret om en hänvisning inte går att lösa upp.

Detta är samma fail-closed-hållning som utkastgrinden i sie-mcp: systemet föreslår aldrig
något det inte kan belägga, och när det inte kan belägga något säger det så.

---

## 2. Systemöversikt

```
                     ┌──────────────────────────────────────┐
   Besökare ───────► │  Frontend (statisk JS-widget)        │
   på quiet.nu       │  chatt, strömmande svar, fotnoter    │
                     └───────────────┬──────────────────────┘
                                     │ SSE / JSON
                     ┌───────────────▼──────────────────────┐
                     │  Backend (FastAPI)                   │
                     │                                      │
                     │  ┌────────────────────────────────┐  │
                     │  │ 1. Planerare (Claude + verktyg)│  │
                     │  │    frihandsfråga → sökplan     │  │
                     │  └───────────┬────────────────────┘  │
                     │              ▼                       │
                     │  ┌────────────────────────────────┐  │
                     │  │ 2. Adapterlager                │  │
                     │  │    PxWeb │ RowStore │ TED │ …  │  │
                     │  └───────────┬────────────────────┘  │
                     │              ▼                       │
                     │  ┌────────────────────────────────┐  │
                     │  │ 3. Faktaregister (per session) │  │
                     │  │    F1, F2, F3 … med källa+länk │  │
                     │  └───────────┬────────────────────┘  │
                     │              ▼                       │
                     │  ┌────────────────────────────────┐  │
                     │  │ 4. Syntes (Claude, JSON-schema)│  │
                     │  │    ser BARA frågan + F-posterna│  │
                     │  └───────────┬────────────────────┘  │
                     │              ▼                       │
                     │  ┌────────────────────────────────┐  │
                     │  │ 5. Validator (fail-closed)     │  │
                     │  └────────────────────────────────┘  │
                     └───────┬──────────────────────────────┘
                             │
        ┌────────────────────┼────────────────────┐
        ▼                    ▼                    ▼
   Katalog- och          Cache/kö            Myndighets-API:er
   lagindex              (SQLite, TTL        (~11 direkta +
   (SQLite + FTS5         per källa)          PxWeb/RowStore-familjer)
    + embeddings)                            + Riksdagen SFS (kopia, §3.2b)
```

---

## 3. Lagren

### 3.1 Källregister (`kallor/kallregister.yaml`)

Deklarativ katalog. Ingen kod får innehålla hårdkodade bas-URL:er — allt läses härifrån.
Fält per källa:

| Fält | Innebörd |
|---|---|
| `id` | stabil nyckel, används i Faktapost.källa |
| `myndighet` | visningsnamn för attribution |
| `adapter` | vilken protokolladapter som hanterar källan |
| `bas_url` | rot-URL |
| `verifierad` | `ja` / `nej` — se §0 |
| `licens` | `CC0`, `CC-BY`, `okänd` |
| `attribution` | text som måste följa med i svaret om licensen kräver |
| `takt` | anrop per tidsfönster, för kön |
| `cache_ttl` | sekunder |
| `blockerad` | `true` för spärrade källor, se §7 |
| `manniskolank_mall` | mall för den klickbara länken |

### 3.2 Katalogindex

Nattlig ingest av dataportal.se DCAT-dump (23 293 datamängder, 34 246 distributioner,
598 dataservices). Lagras i SQLite:

* `datamangd(id, titel, beskrivning, utgivare, licens, tema, nyckelord)`
* `distribution(id, datamangd_id, format, access_url, access_service)`
* FTS5-index på titel + beskrivning + nyckelord
* `embedding(datamangd_id, vektor BLOB)` — vektorsökning för frihandsfrågor

Hybridsökning: FTS5 (BM25) för exakta termer + kosinuslikhet på embeddings för
begreppsfrågor, sammanvägt med reciprocal rank fusion. Detta är discovery-lagret —
det gör att boten kan svara "det finns hos Boverket, här är datamängden" även när
ingen adapter kan exekvera mot den.

### 3.2b Lagindex (steg 16)

Samma hybridsökning, andra korpus: 62 skatte- och redovisningsförfattningar hämtade
som **konsoliderad** text från Riksdagens öppna data. Chunk = kapitelrubrik +
paragrafrubrik + paragraftext + ändringsmarkering (`Lag (ÅÅÅÅ:NNN)`). En paragraf
står sällan ensam — *"Bestämmelser om skattskyldighet finns i 3–7 kap."* betyder
ingenting utan sitt sammanhang.

Riksdagen konsoliderar åt oss; dokumenthuvudet bär punkten:
`Inkomstskattelag (1999:1229) t.o.m. SFS 2026:1393`. Systemet bygger **ingen** egen
konsolidering ur ändringsförfattningar — en felkonsoliderad paragraf ser exakt lika
trovärdig ut som en riktig.

**Detta är systemets enda kopia.** Se §5 regel 8.

### 3.3 Adapterlager

Alla adaptrar implementerar samma gränssnitt:

```python
class Adapter(Protocol):
    id: str
    def beskriv(self) -> list[VerktygsSpec]: ...      # → Claude-verktygsdefinitioner
    def hamta(self, plan: Fragplan) -> list[Faktautkast]: ...
```

**Adaptrar returnerar `Faktautkast`, aldrig `Faktapost`** (se §3.4). Vid fel loggar
adaptern och returnerar tom lista — den skapar aldrig ett utkast som bär ett
felmeddelande som värde. Ett fel är inte ett faktum.

**Nivå 1 — handskrivna adaptrar (verifierade källor):**
`riksbanken`, `scb_pxweb`, `ted`, `riksdagen`, `kolada`, `vies`, `dataportal`,
`smhi`, `skolverket`, `trafa`, `polisen_handelser`

**Nivå 2 — generiska protokolladaptrar:**
`pxweb` (godtycklig PxWeb-instans), `rowstore` (godtycklig EntryScape RowStore),
`json_rest` (enkel GET → JSON-path). Dessa når dussintals myndigheter via
katalogindexets `access_url` utan ny kod per källa.

**Nivå 3 — katalogsvar:** när ingen adapter passar returnerar `dataportal`-adaptern
metadata om datamängden som Faktaposter. Boten svarar då *var* uppgiften finns i stället
för *vad* den är. Det är ett giltigt svar, inte ett fel.

**Utanför nivåerna — `lagtext` (steg 16):** den enda adaptern som läser ur ett lokalt
index i stället för att anropa en källa. Den finns i sin egen kategori just för att den
avviker; se §3.2b och §5 regel 8.

### 3.4 Faktapost

Den bärande datatypen. Allt som får nämnas i ett svar finns som en Faktapost.

```python
@dataclass(frozen=True)
class Faktapost:
    id: str                 # "F1", "F2" … unikt per session
    etikett: str            # "Växelkurs SEK/EUR"
    varde: str              # "10.9965"   (alltid sträng — ingen omtolkning)
    enhet: str | None       # "SEK per EUR"
    period: str | None      # "2026-08-12"
    dimensioner: dict       # {"region": "Malmö", "kön": "totalt"} — vad som valdes
    kalla_id: str           # → kallregister
    myndighet: str
    dataset: str | None     # tabell-id / dataset-uuid / publiceringsnummer
    licens: str
    attribution: str | None
    hamtad: datetime
    lank_manniska: str      # klickbar sida hos myndigheten
    lank_maskin: str        # det exakta API-anrop som gjordes
    harledd: bool = False   # se §5
    harledd_av: tuple[str, ...] = ()
```

`lank_maskin` är inte kosmetik. Den är beviset: användaren ska kunna klistra in den och
få samma siffra. Den skrivs alltid ut i källpanelen.

**`Faktautkast` — vad adaptrar returnerar.** Samma fält som `Faktapost` men utan `id`.
Bara `Faktaregister` får mynta ett F-id, och bara registret kontrollerar att båda
länkarna finns:

```python
poster = faktaregister.registrera_alla(adapter.hamta(plan))
```

Skälet är konkret, inte formellt. I den första implementationen byggde adaptrarna
`Faktapost(id="", …)` direkt och tänkte fylla i id senare. Eftersom länkkontrollen bara
låg i `registrera()` kunde en post då existera utan länkar — vilket inträffade i
pxweb-adapterns felgren, där ett felmeddelande returnerades som ett citerbart faktum med
tomma länkar. Med `Faktautkast` är den vägen stängd: en `Faktapost` kan inte uppstå utan
att passera valideringen.

### 3.5 Cache och kö

En process per källa-id. SCB:s PxWeb rapporterar själv `maxCallsPerTimeWindow: 30`,
`timeWindow: 10` — det är den strängaste gränsen och styr designen. Alla utgående anrop
går genom en token bucket per källa, konfigurerad av `takt` i registret. Svaren cachas i
SQLite med `cache_ttl` per källa (valutakurs 1 h, statistiktabell 24 h, DCAT-metadata
7 dygn). Cachenyckeln är det normaliserade anropet, inte frågan.

### 3.6 Katalogstruktur och Hantering av Hemligheter

Projektet är uppdelat i två rot-mappar på maskinen för att skydda API-nycklar och hemlig miljöinformation från publik källkod:
1. `G:\My Drive\Claude Cowork\quiet_chatt` — Huvudprojektet. Här ligger all öppen källkod (Python, konfig, adaptrar och publicerad dokumentation).
2. `G:\My Drive\Claude Cowork\quiet_chatt_hemligt` — Hemlig arbetsyta. Används exklusivt för att spara API-nycklar (t.ex. mot Bolagsverket), privata certifikat och lokal utvecklingsmiljö-information som aldrig ska lämnas ut.
Kod i huvudprojektet konfigureras att läsa referenser från den hemliga mappen där det behövs utan att stiga över till GitHub.

---

## 4. Frågeflödet

### Fas A — Planering och hämtning (agentisk)

Claude får frågan, katalogsökverktyget och adaptrarnas verktygsspecar. Den loopar:
söker i katalogen, väljer källa, anropar adapter, ser Faktaposterna, avgör om den behöver
mer. Loopen avslutas när modellen slutar kalla verktyg, eller vid `max_iterations`.

**Fas A:s textutgång kastas.** Det enda som förs vidare är Faktaregistret.

### Fas B — Syntes (isolerad)

Ett **nytt** anrop med ett rent sammanhang. Kontexten består av exakt två saker:
användarens fråga och Faktaposterna serialiserade. Ingen konversationshistorik, inget
verktygsspår, ingen katalog. Utgången tvingas till JSON-schema:

```json
{
  "kan_besvaras": true,
  "stycken": [
    { "text": "Riksbankens referensränta är 3,5 procent.", "kallor": ["F1"] }
  ],
  "forbehall": "…"
}
```

Schemat kräver `minItems: 1` på `kallor` för varje stycke och `additionalProperties: false`
överallt, inklusive toppnivån. Modellen kan inte producera ett ociterat stycke eller ett
extra fält — det är inte ett giltigt svar enligt schemat. Är Faktaregistret tomt anropas
modellen inte alls; svaret kortsluts direkt till `kan_besvaras: false`.

**Varför två faser i stället för en:** i ett enda agentiskt anrop bär modellen med sig
allt den läst under vägen, inklusive sitt eget resonemang och sin förträning. Då blir
citeringsregeln en instruktion. Med den isolerade fas B kan syntesmodellen bokstavligen
inte citera något den inte fått, och inte veta något den inte fått. Invarianten flyttas
från prompt till arkitektur.

### Fas C — Validering (fail-closed)

Innan svaret lämnar backend:

0. **Allt som renderas måste vara täckt.** `forbehall` är fritext från modellen
   och bär inga källhänvisningar. Det får därför inte införa tal som saknas i
   registret — annars är fältet en textkanal rakt förbi invarianten i §1. Vid
   granskningen 2026-08-13 vägrade modellen att lägga fakta där även vid direkt
   uppmaning, men validatorn hade släppt igenom vad som helst. Att invarianten
   höll berodde enbart på modellens uppförande, vilket är precis vad §1 säger
   att man inte får förlita sig på. Ett svar med `kan_besvaras: true` men utan
   stycken avvisas av samma skäl: då vore förbehållet svarets hela innehåll.
1. Varje `kallor`-id måste finnas i sessionens Faktaregister. Annars kasta.
2. Varje stycke som innehåller en siffra, ett datum eller ett egennamn måste ha minst
   en källa. (Schemat garanterar detta redan; kontrollen fångar schema-drift.)
3. Varje Faktapost som citeras måste ha `lank_manniska` satt.
4. Varje citerad Faktapost med `licens == "CC-BY"` måste ha `attribution` satt på källan.

Attributionstexten skrivs aldrig av modellen — den hämtas deterministiskt ur den citerade
Faktapostens `attribution`-fält av validatorn själv och bifogas svarsobjektet när
kontrollerna går igenom. Samma princip som härledda beräkningar (§5 regel 2): allt som kan
hämtas ur redan känd data ska hämtas därifrån, inte återges av modellen.

Vid fel: ett omförsök av fas B med validatorns felmeddelande inlagt i användarmeddelandet
(den frusna systemprompten rörs inte). Vid andra felet returneras `kan_besvaras: false` med
texten *"Det hittade jag inte i källorna."* — aldrig ett obelagt svar.

---

## 5. Grundregler (invarianter som kod, inte prompt)

| # | Regel | Var den upprätthålls |
|---|---|---|
| 1 | Inget svar utan minst en Faktapost | JSON-schema + validator |
| 2 | Modellen räknar aldrig | Härledningar sker i `berakningar.py`; resultatet blir en ny Faktapost med `harledd=True` och `harledd_av=("F1","F2")`. Om modellen behöver en kvot anropar den `berakna_kvot`-verktyget. |
| 3 | Ingen modellkunskap | Fas B:s kontextisolering |
| 4 | Två länkar per Faktapost | Adapterkontrakt; validator kontrollerar |
| 5 | Blocklista i kod | `kallregister.yaml` + hård kontroll i `transport.py`, inte i prompt. Tre spärrar före all nätverkstrafik: `SparradKalla` för `blockerad: true`, `EjAktiveradKalla` för `aktiverad: false`, och värdkontroll mot katalogindexet för alla `generisk: true` |
| 6 | Frågan är synlig | `dimensioner` + `lank_maskin` renderas alltid i källpanelen |
| 7 | Vägra hellre än gissa | Om planeraren inte kan mappa frågan till konkreta dimensioner returnerar adaptern valalternativen som Faktaposter i stället för ett gissat värde |

Regel 7 har ett generellare krav bakom sig, lärt av två separata fel: **varje
adapter vars data har en identifierare — tabell-id, dimensionskod, serie-id,
dataset-UUID — måste exponera ett katalogverktyg som listar de giltiga
värdena.** Utan det gissar modellen, och den gissar övertygande.

PxWeb saknade det först och hade returnerat en godtycklig skiva. Riksbanken
saknade det och besvarade en fråga om referensräntan med styrräntan
(`SECBREPOEFF` i stället för `SECBREFEFF` — ett tecken isär). Båda felen hade
passerat en läsare som bara ser svaret, eftersom källänken pekade på en riktig
sida hos en riktig myndighet.

Av samma skäl måste `etikett` säga **vad** uppgiften är, inte bara vilket id den
hämtades med. "Riksbanken, serie SECBREPOEFF" gör felet osynligt i källpanelen;
"Riksbanken: Policy rate (SECBREPOEFF)" gör det uppenbart.

Regel 2 är den som skyddar mot den farligaste felmoden. Ett självsäkert felaktigt tal är
värre än inget tal, och en modell som får multiplicera två hämtade värden har fem sätt
att göra det fel. Låt den hämta och citera; låt koden räkna.

### Regel 8 — en kopia måste bära sin färskhetsstämpel

Allt utom lagindexet hämtas i realtid. Lagtexten är den enda kopian, och en kopia kan
bli inaktuell utan att se inaktuell ut.

Därför: varje Faktapost ur lagindexet bär konsolideringspunkten i `period`
(`"t.o.m. SFS 2026:1393"`), SFS-numret i `dataset`, och `hamtad` = när kopian togs,
inte när frågan ställdes. Ett svar som citerar en paragraf ska kunna visa i vilken
lydelse, och `lank_manniska` pekar på Riksdagens sida med den nu gällande texten.

Skälet är proportionerligt: en inaktuell SCB-siffra är pinsam, en inaktuell
skatteparagraf leder till en felaktig deklaration. Färskheten kontrolleras nattligt
genom att jämföra `systemdatum` — aldrig genom att diffa text.

Regeln gäller varje framtida källa som cachas i stället för att hämtas. Det finns bara
en i dag, och det är avsiktligt.

---

## 6. Modellval och API-användning

| Beslut | Värde | Motiv |
|---|---|---|
| Modell | `claude-opus-5` | Fas A är agentiskt verktygsval mot ~15 verktyg; fas B är faktabunden syntes med schema. Båda är kvalitetskänsliga. |
| Tänkande | `thinking={"type": "adaptive"}` | Standard på Opus 5. Sätts explicit för tydlighet. |
| Effort | Fas A `high`, fas B `medium` | Fas B är mekanisk när fakta redan finns. |
| Strömning | Ja, båda faserna | `max_tokens` över ~16 000 kräver det; fas B strömmas till frontend. |
| Struktur | `output_config.format` med json_schema i fas B | Se §4. |
| Cache | Breakpoint efter systemprompt + verktygsdefinitioner | Stabil prefix. Minsta cachebara prefix på Opus 5 är 512 token. |
| SDK | `anthropic` (Python) | Rå HTTP är förbjudet — använd SDK:t. |

**Anropsformen, verifierad mot API:t 2026-08-13.** Tre fällor har redan kostat
en hel omskrivning av fas A — de ger alla HTTP 400 på Opus 5:

* **Inget `budget_tokens`.** Det är borttaget. Djupet styrs med
  `output_config.effort`, inte med ett tokentak.
* **Inga beta-flaggor** för thinking eller prompt-caching. Båda är GA, och de
  gamla flaggorna finns inte kvar. Använd `client.messages.stream`, inte
  `client.beta.messages.stream`.
* **`max_tokens` täcker thinking + svarstext.** Med adaptiv thinking räknas båda
  mot samma tak, så ett snålt värde trunkerar mitt i ett resonemang.

Kontrollera `stop_reason` innan `content` läses — Opus 5 kan svara `refusal`.

**Kostnad, som beställaren måste ta ställning till.** Opus 5 kostar 5 USD per miljon
input-token och 25 per miljon output. En publik chatt på en öppen sajt har obegränsad
trafik. Två åtgärder är inbyggda i designen och måste vara på från dag ett:

* Hård kvot per IP och per dygn (`config.toml → kvot`), fail-closed vid överskridande.
* Prompt-caching på den stabila prefixen, som tar bort ~90 % av input-kostnaden på
  återkommande anrop.

Om kostnaden ändå blir för hög är modellbytet beställarens beslut, inte
implementatörens. Ändra inte modell utan att fråga.

### 6a. Kostnadsspärr, beslutad 2026-08-13

Beställarens hårda tak är 1 000 SEK/månad. Det upprätthålls i tre lager:

1. **Anthropic-kontot är förskottsbetalt** (prepaid credits) utan auto-reload. När
   krediten tar slut misslyckas anrop med ett faktureringsfel — kontot kan inte dra
   mer pengar än det som satts in.
2. `config.toml → kvot.kostnadstak_sek_per_manad` är ett sekundärt, internt larm i
   applikationen (mätpunkt, inte betalningsspärr).
3. Per-IP- och total-dygnskvoterna i samma sektion är den operativa begränsningen mot
   enskild missbruk.

Implementatören ska inte förvänta sig att lager 2 och 3 ensamma räcker som finansiellt
skydd — lager 1 är den faktiska garantin och ligger utanför koden, i kontoinställningarna
hos Anthropic.

---

## 7. Spärrade källor

Två källor är **explicit uteslutna** på beställarens instruktion och får inte finnas i
registret annat än som spärrposter:

* `polisen_efterlysta` — Polisens efterlysta personer
* `bolagsverket_verkliga_huvudman` — Bolagsverkets register över verkliga huvudmän

Spärren ligger i `kallregister.yaml` som `blockerad: true` och kontrolleras i
adapterlagrets ingång, inte i systemprompten. En blockerad källa kan inte anropas ens om
modellen försöker.

**Polisens händelse-API (`polisen.se/api/events`) är tillåtet** — det är bara
efterlysta-delen som är utesluten.

Utöver de två: varje källa som returnerar uppgifter om enskilda fysiska personer ska
behandlas som spärrad tills beställaren uttryckligen godkänner den. En fritextsökbar bot
har en annan karaktär än en myndighetssida med formulär, och det är den skillnaden som
motiverar spärren.

---

## 8. Licens och attribution

SCB:s PxWeb rapporterar `license: CC0` i sin egen konfiguration. Andra källor är CC-BY,
vilket gör attribution till ett villkor och inte en artighet. Registret bär
`licens` och `attribution` per källa; validatorn kräver att attributionen finns i
svarsobjektet för varje citerad CC-BY-källa. Frontend renderar den under källpanelen.

TED:s dokumentation anger varken rate limits eller licensvillkor. Innan TED-adaptern
går i produktion ska implementatören mejla TED:s helpdesk och få villkoren skriftligt.
Fram till dess körs TED bakom en konservativ takt (1 anrop/sekund) och flaggas i
registret som `licens: okänd`.

---

## 9. Teknikval

| Del | Val | Motiv |
|---|---|---|
| Backend | Python 3.11+, FastAPI, uvicorn | Beställarens stack; `anthropic`-SDK:t är förstklassigt i Python |
| Lagring | SQLite (WAL) + FTS5 | Ingen infrastruktur att drifta; 23k datamängder är litet |
| Vektorer | `sqlite-vec`, fallback numpy-minnesindex | Undviker separat vektordatabas |
| HTTP ut | `httpx` med per-källa-klient | Timeout och retry per källa |
| Frontend | Fristående JS, ingen byggkedja | Ska kunna klistras in på quiet.nu med en `<script>`-tagg |
| Konfiguration | `config.toml` + `.env` för nycklar | Nycklar aldrig i klienten |
| Test | pytest, med inspelade svar (`vcr`-stil) | Testerna får inte anropa myndigheterna |

**Nycklar ligger alltid i backend.** Bolagsverkets avtalsbundna API:er kräver
klientcertifikat; den anslutningen får aldrig ske från webbläsaren.

---

## 10. Vad som medvetet inte byggs i version 1

* Inloggning och användarkonton
* Sparade konversationer över sessioner
* Skrivande operationer mot någon källa (det finns inga)
* Koppling till sie-mcp — se nedan
* Egen adapter per myndighet på nivå 2; katalogen plus generiska adaptrar räcker
* Egen konsolidering av lagtext ur ändringsförfattningar — Riksdagen gör det åt oss
  (§3.2b), och att bygga om det vore att införa en felkälla utan motsvarande vinst
* EU-rätt och OECD-material. Sju dokument i beställarens lagförteckning ligger hos
  EUR-Lex respektive OECD och saknas i Riksdagens SFS-data. De är en känd lucka, inte
  något som ska smygas in i lagregistret som om de vore svenska författningar

**Förhållandet till sie-mcp.** Detta system är fristående. Det delar ingen kod, ingen
process och ingen datamodell med sie-mcp. Skälet är att sie-mcp arbetar i användarens
egen bokföring med BYOK, utkastgrind och sekretesskrav, medan detta system arbetar med
publik data utan autentisering. Att blanda dem skulle ge den publika datan restriktioner
den inte behöver och bokföringen felmoder den inte har. Om MCP:n senare ska nå samma
data sker det via ett fåtal generiska verktyg mot detta systems HTTP-API, inte genom
delad kod.

---

## 11. Mätpunkter

Loggas per fråga, i SQLite, utan att spara frågetexten längre än 30 dagar:

* vilka källor som anropades och om de svarade
* om fas C validerade i första försöket, andra, eller föll igenom
* antal Faktaposter per svar
* cache-träffkvot per källa
* token in/ut per fas

Den viktigaste siffran är **andelen frågor som besvaras på nivå 3** (katalogsvar i
stället för exekverat svar). Den siffran talar om vilken adapter som ska byggas härnäst,
och den är hela skälet till att version 1 släpps med få adaptrar.
