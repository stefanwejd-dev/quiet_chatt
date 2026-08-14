# Quiet Öppen Data

Fristående chattfunktion för quiet.nu som besvarar frågor **enbart** med uppgifter
hämtade i realtid från offentligt finansierade organisationers API:er, och som redovisar
varje uppgift med fotnot och klickbar källänk.

## Status

| Steg | Vad | Status |
|---|---|---|
| 0 | Projektskelett | ✅ |
| 1 | Faktapost & Faktaregister | ✅ |
| 2 | Katalogingest (23 289 datamängder) | ✅ |
| 3 | Semantisk sökning (FTS5 + embeddings + RRF) | ✅ |
| 4 | Adaptergränssnitt, Riksbanken & VIES | ✅ |
| 5 | Kö, cache och blocklista | ✅ |
| 6 | PxWeb-adapter (SCB) | ✅ |
| 7 | Verifieringsgrind: RowStore, Bolagsverket, JobTech | ✅ |
| 8 | Övriga adaptrar (TED, Riksdagen, Kolada, Dataportal, RowStore, SMHI, Skolverket, Trafa, Polisen, JobTech) | ✅ |
| 9 | Fas A: planerare och hämtningsloop | ✅ |
| 10 | Fas B: syntes med tvingad citering | ✅ |
| 11 | Fas C: validator (fail-closed) | ✅ |
| 12 | Beräkningsmodul (`berakningar.py`) | ✅ |
| 13 | HTTP-API och kvoter (`api.py`) | ✅ |
| 14 | Frontend (`frontend/widget.js`) | ✅ |
| 15 | Drift och mätning (`matning.py`, `nattlig_ingest.py`, `GET /matning`) | ✅ |
| 16A | Lagkorpus — de fem huvudlagarna | ✅ |
| 16B | Lagkorpus — resterande 57 författningar | ✅ |

**Hela testsviten: 213 passed** (2026-08-14).



## Dokumenten

| Fil | Vad den är | Läs den om du… |
|---|---|---|
| `ARKITEKTUR.md` | Systemets design och de invarianter som gör citeringskravet strukturellt i stället för prompt-baserat | …ska förstå *varför* |
| `PLAN.md` | 18 steg med acceptanskriterier, avsedda för en implementerande kod-AI | …ska bygga |
| `kallor/kallregister.yaml` | Systemets enda sanning om vilka källor som finns, hur de nås, och vilka som är verifierade | …ska röra en källa |

## Kort om designen

Ett svar produceras i tre faser. Fas A är en agentisk hämtningsloop som fyller ett
**Faktaregister**. Fas B är ett *nytt* modellanrop vars hela kontext är frågan plus
Faktaregistret — ingen historik, inget verktygsspår, ingen förträningskunskap att luta
sig mot — med en utgång tvingad till ett JSON-schema som kräver minst en källhänvisning
per stycke. Fas C validerar och faller stängt.

Poängen är att modellen i fas B **inte kan** citera något den inte fått, och inte kan
veta något den inte fått. Citeringskravet är arkitektur, inte instruktion.

## Köra lokalt

```bash
cp .env.example .env          # fyll i ANTHROPIC_API_KEY och MATNING_NYCKEL
pip install -e ".[dev]"
python -m quiet_oppen_data.index.ingest   # engångskörning, ~2 min
uvicorn quiet_oppen_data.api:app --reload
```

Öppna `frontend/test.html` i webbläsaren för att testa widgeten utan en riktig fråga.

`MATNING_NYCKEL` skyddar `GET /matning` — driftdata, till skillnad från `/kallor`
och `/halsa` som är avsiktligt öppna. Saknas variabeln svarar endpointen 503 i
stället för att ligga öppen. Skicka nyckeln som headern `x-matning-nyckel`.

## Nattlig ingest

```bash
# cron: varje dag kl 03:00
0 3 * * * cd /app && python -m quiet_oppen_data.index.nattlig_ingest >> logs/ingest.log 2>&1
```

## Uteslutna källor

På beställarens instruktion: **Polisens efterlysta** och **Bolagsverkets verkliga
huvudmän**. Spärren ligger i källregistret och kontrolleras i adapterlagrets ingång,
inte i en systemprompt. Polisens *händelse*-API är tillåtet.

## Källverifiering

Elva källor är anropade live och bekräftade 2026-08-13. Skatteverkets åtta
öppna datamängder (skattesatser, skattetabeller, traktamenten, kostförmån)
verifierades 2026-08-14 och ligger som kurerad katalog under
`skatteverket_rowstore`.
Fyra till är listade men **ej verifierade** — deras sökvägar är inte bekräftade och
de är avstängda i registret (`aktiverad: false`). Ingen kod får skrivas mot en gissad
endpoint — se `kallor/kallregister.yaml` och `ARKITEKTUR.md §0`.
