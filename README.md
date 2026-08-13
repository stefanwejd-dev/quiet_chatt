# Quiet Öppen Data

Fristående chattfunktion för quiet.nu som besvarar frågor **enbart** med uppgifter
hämtade i realtid från offentligt finansierade organisationers API:er, och som redovisar
varje uppgift med fotnot och klickbar källänk.

## Dokumenten

| Fil | Vad den är | Läs den om du… |
|---|---|---|
| `ARKITEKTUR.md` | Systemets design och de invarianter som gör citeringskravet strukturellt i stället för prompt-baserat | …ska förstå *varför* |
| `PLAN.md` | 16 steg med acceptanskriterier, avsedda för en implementerande kod-AI | …ska bygga |
| `kallor/kallregister.yaml` | Systemets enda sanning om vilka källor som finns, hur de nås, och vilka som är verifierade | …ska röra en källa |

## Kort om designen

Ett svar produceras i tre faser. Fas A är en agentisk hämtningsloop som fyller ett
**Faktaregister**. Fas B är ett *nytt* modellanrop vars hela kontext är frågan plus
Faktaregistret — ingen historik, inget verktygsspår, ingen förträningskunskap att luta
sig mot — med en utgång tvingad till ett JSON-schema som kräver minst en källhänvisning
per stycke. Fas C validerar och faller stängt.

Poängen är att modellen i fas B **inte kan** citera något den inte fått, och inte kan
veta något den inte fått. Citeringskravet är arkitektur, inte instruktion.

## Två saker som måste redas ut innan bygget

1. **Domänen.** Dokumenten är skrivna mot `quiet.nu`. `quiet.se` svarar med titeln
   `.:QUIET:.` och tillhör någon annan. Domänen ligger på en rad i `config.toml`.

2. **Källverifiering.** Elva källor är anropade live och bekräftade 2026-08-13.
   Fyra till är listade men **ej verifierade** — deras sökvägar är inte bekräftade och
   de är avstängda i registret. Ingen kod får skrivas mot en gissad endpoint.

## Uteslutna källor

På beställarens instruktion: **Polisens efterlysta** och **Bolagsverkets verkliga
huvudmän**. Spärren ligger i källregistret och kontrolleras i adapterlagrets ingång,
inte i en systemprompt. Polisens *händelse*-API är tillåtet.
