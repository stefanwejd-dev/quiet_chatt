# Quiet Öppen Data

Fristående chattfunktion för quiet.nu som besvarar frågor **enbart** med uppgifter
hämtade i realtid från offentligt finansierade organisationers API:er, och som redovisar
varje uppgift med fotnot och klickbar källänk.

Ett svar produceras i tre faser. Fas A är en agentisk hämtningsloop som fyller ett **Faktaregister**. Fas B är ett *nytt* modellanrop vars hela kontext är frågan plus Faktaregistret — ingen historik, inget verktygsspår, ingen förträningskunskap att luta sig mot — med en utgång tvingad till ett JSON-schema som kräver minst en källhänvisning per stycke. Fas C validerar och faller stängt. Modellen *kan* inte citera något den inte fått. Citeringskravet är arkitektur, inte instruktion.

*Skärmbild kommer efter driftsättning.*

## Kom igång på fem minuter

### Alternativ A: Förbyggt demoindex (snabbast)

Ladda ner den förbyggda releasefilen för direkt provkörning utan att behöva bygga index:

```bash
# 1. Klona och installera
git clone https://github.com/stefanwejd-dev/quiet_chatt.git
cd quiet_chatt
cp .env.example .env          # fyll i ANTHROPIC_API_KEY och MATNING_NYCKEL
pip install -e ".[dev]"

# 2. Hämta verifierat demoindex (v0.1.0, 36.1 MB, SHA-256: 2fd55b668f43d8dd4d7d7e75b8cdbfe919d8462937123df84db33730da7c4aec)
python -m quiet_oppen_data.index.hamta_demo

# 3. Starta servern
uvicorn quiet_oppen_data.api:app --reload
```

### Alternativ B: Bygg demoindexet lokalt (~2-3 minuter)

```bash
python -m quiet_oppen_data.index.ingest --demo       # kurerade datamängder (~200 st)
python -m quiet_oppen_data.index.lag_ingest --demo   # de fem centrala lagarna
uvicorn quiet_oppen_data.api:app --reload
```

### Alternativ C: Docker Compose

```bash
docker compose up -d
# För att hämta release-demoindexet i Docker:
docker compose run --rm hamta-demo
```

> **Obs om Docker-imagen:** Imagen är ~1.5 GB eftersom den svenska språkmodellen KBLab Sentence-BERT (~500 MB) förladdas direkt under byggsteget i `Dockerfile`. Det gör att första sökningen svarar omedelbart utan nedladdningsfördröjning.

Öppna `frontend/test.html` i webbläsaren för att testa widgeten utan en riktig fråga.

`MATNING_NYCKEL` skyddar `GET /matning` — driftdata, till skillnad från `/kallor`
och `/halsa` som är avsiktligt öppna. Saknas variabeln svarar endpointen 503 i
stället för att ligga öppen. Skicka nyckeln som headern `x-matning-nyckel`.

## Vad den kan svara på

Systemet besvarar sakfrågor mot öppna data och författningar med full källspårbarhet:

* **"Vad är gränsen för skattefri julgåva till anställda?"**

  > *Svar:* Gränsen för skattefri julgåva är 550 kr inklusive moms per anställd [1]. Om gåvans värde överstiger detta belopp blir hela förmånen skattepliktig.
  > *Källor:* [1] Skatteverket, Rättsliga regelfiler — Gåvor till anställda

* **"Vilka krav ställs på en verifikation enligt bokföringslagen?"**

  > *Svar:* Enligt 5 kap. 7 § bokföringslagen (1999:1078) ska en verifikation innehålla uppgift om när den sammanställts, när affärshändelsen inträffat, vad den avser, vilket belopp den gäller samt vilken motpart som berörs [1].
  > *Källor:* [1] Bokföringslag (1999:1078) 5 kap. 7 §

* **"Hur stor är befolkningen i Göteborgs kommun?"**

  > *Svar:* Enligt SCB:s befolkningsstatistik uppgår folkmängden till 607 882 invånare [1].
  > *Källor:* [1] Statistiska centralbyrån (SCB) — Folkmängd efter region

* **"Vad är Riksbankens aktuella styrränta?"**

  > *Svar:* Riksbankens styrränta är fastställd till 2,75 % [1].
  > *Källor:* [1] Sveriges Riksbank — SWEA API

* **"Vad heter Aktiebolaget Volvo och när registrerades det?"**

  > *Svar:* Organisationen är registrerad som Aktiebolaget Volvo, med registreringsdatum 1915-05-05 [1].
  > *Källor:* [1] Bolagsverket — Värdefulla datamängder

## Dokumenten

|Fil|Vad den är|Läs den om du…|
|-|-|-|
|[`docs/ARKITEKTUR.md`](docs/ARKITEKTUR.md)|Systemets design och de invarianter som gör citeringskravet strukturellt i stället för prompt-baserat|…ska förstå *varför*|
|[`docs/PLAN.md`](docs/PLAN.md)|Stegen med acceptanskriterier, avsedda för en implementerande kod-AI|…ska bygga|
|`kallor/kallregister.yaml`|Systemets enda sanning om vilka källor som finns, hur de nås, och vilka som är verifierade|…ska röra en källa|
|`lagar/lagregister.yaml`|De 62 författningar som speglas i lagindexet|…ska lägga till en lag|

## Status

|Steg|Vad|Status|
|-|-|-|
|0|Projektskelett|✅|
|1|Faktapost & Faktaregister|✅|
|2|Katalogingest (23 289 datamängder)|✅|
|3|Semantisk sökning (FTS5 + embeddings + RRF)|✅|
|4|Adaptergränssnitt, Riksbanken & VIES|✅|
|5|Kö, cache och blocklista|✅|
|6|PxWeb-adapter (SCB)|✅|
|7|Verifieringsgrind: RowStore, Bolagsverket, JobTech|✅|
|8|Övriga adaptrar (TED, Riksdagen, Kolada, Dataportal, RowStore, SMHI, Skolverket, Trafa, Polisen, JobTech)|✅|
|9|Fas A: planerare och hämtningsloop|✅|
|10|Fas B: syntes med tvingad citering|✅|
|11|Fas C: validator (fail-closed)|✅|
|12|Beräkningsmodul (`berakningar.py`)|✅|
|13|HTTP-API och kvoter (`api.py`)|✅|
|14|Frontend (`frontend/widget.js`)|✅|
|15|Drift och mätning (`matning.py`, `nattlig_ingest.py`, `GET /matning`)|✅|
|16A|Lagkorpus — de fem huvudlagarna|✅|
|16B|Lagkorpus — resterande 57 författningar|✅|
|17|Skatteverkets statistik — elva RowStore-datamängder + färskhet i `dimensioner`/`period`|✅|
|18|Skatteverkets rättsliga vägledning — sök-länk|✅|
|19|Nattlig färskhetskontroll av lagkorpuset|✅|
|20|Skatteverkets rättsliga regelfiler (Rules as Code)|✅|
|21|Bolagsverket HVD — produktionsaktivering (OAuth2, organisationer, dokumentlista)|✅|

**Hela testsviten: 254 passerade**, `ruff check .` rent (2026-08-16). Prestandatester som beror på hårdvaruladdningstid är markerade `@pytest.mark.slow` och körs separat med `pytest -m slow`; livetester mot Anthropic och externa API:er är markerade `@pytest.mark.live` och körs separat med `pytest -m live`.

Lagindexet i `data/index.sqlite`: 62 dokument, 9 792 chunkar, 9 792 embeddings.
Katalogindexet: 23 289 datamängder, 32 518 distributioner.

## Nattlig ingest

```bash
# cron: varje dag kl 03:00
0 3 * * * cd /app && python -m quiet_oppen_data.index.nattlig_ingest >> logs/ingest.log 2>&1
```

Sedan steg 19 omfattar den nattliga körningen både katalogindexet och en
lagkorpus-färskhetskontroll: dokumenthuvudena för alla 62 författningar jämförs
mot Riksdagens `systemdatum`, och bara de som ändrats ingesteras om
([`docs/ARKITEKTUR.md`](docs/ARKITEKTUR.md) §5 regel 8). Lagkorpusets
ålder per författning syns i `GET /matning` → `lagkorpus_alder`.

## Uteslutna källor

**Polisens efterlysta** och **Bolagsverkets verkliga huvudmän**. Spärren ligger i
källregistret och kontrolleras i adapterlagrets ingång, inte i en systemprompt.
Polisens *händelse*-API är tillåtet. Detta då dessa uppgifter kvalificeras som
synnerligen känsliga och rör personuppgifter om enskilda fysiska personer.

**Bolagsverkets värdefulla datamängder (HVD)** — organisationsdata som namn,
form, adress, SNI-koder och inlämnade årsredovisningar — är en separat källa
och rör inga personuppgifter om fysiska personer. Den är aktiverad, se nästa
avsnitt.

## Källverifiering

Elva källor är anropade live och bekräftade 2026-08-13. Skatteverkets åtta
öppna datamängder (skattesatser, skattetabeller, traktamenten, kostförmån)
verifierades 2026-08-14 och ligger som kurerad katalog under
`skatteverket_rowstore`.

Två generiska protokolladaptrar (`_generisk_rowstore`, `_generisk_json`) är listade men
**ej verifierade** i den bemärkelsen att de saknar en egen `bas_url` att bekräfta —
de instansierar mot vilken värd som helst i katalogindexet, och deras säkerhetskontroll
sker per anrop (host-kontroll mot dataportalen), inte som ett engångstest av en
fast endpoint. Ingen kod får skrivas mot en gissad endpoint — se
`kallor/kallregister.yaml` och [`docs/ARKITEKTUR.md`](docs/ARKITEKTUR.md) §0.

**Bolagsverket HVD** var ett mellanläge 2026-08-14 till 2026-08-16: OAuth2-flödet,
`/isalive` och kroppsschemat för `/organisationer` var anropade och avlästa mot
Bolagsverkets verifieringsmiljö, men produktionsuppgifter saknades och källan var
`aktiverad: false`. Produktionsuppgifter mottogs 2026-08-16 och anropades live
samma dag — `/organisationer` och `/dokumentlista` gav 200 med riktiga svar
(bekräftat mot Aktiebolaget Volvo). Källan är nu aktiv. Se steg 7 (återupptaget)
i [`docs/PLAN.md`](docs/PLAN.md).

**Skatteverkets rättsliga regelfiler** (steg 20, 2026-08-15) upphäver slutsatsen i
steg 18. Steg 18 stängdes med motiveringen att reglerna bara fanns bakom
partner-API:et Rättsliga regler, som kräver avtal. Det var halvt fel: samma
regelfiler publiceras som **öppna data** i Skatteverkets DCAT-katalog, utan
nycklar och utan avtal — och de öppna filerna låg vid kontrollen en version
*före* partner-API:ets kompletta testtjänst (Gåvor 1.3.0 mot 1.2.0, med
höjda beloppsgränser). Partner-API:et gav alltså både mindre frihet och äldre
data. Samtliga tretton filer är hämtade och parsade 2026-08-15.

Licensen är `okänd` och inte `CC0`: datasetet bär `accessRights: PUBLIC` men
saknar `dcterms:license` — kontrollerat på både datasetet och dess
distributioner. Åtkomsten är alltså belagd, användningsvillkoren inte. Frågan
är ställd till katalogens kontaktpunkt. Under tiden bär varje Faktapost
attribution och två länkar, så hänvisningen är så god den kan bli.
