# Arbetsorder — Quiet Öppen Data

## Uppdraget

Bygg en fristående faktachatt för quiet.nu som svarar på frågor om Sverige
**enbart** med uppgifter hämtade i realtid från offentliga myndigheters och
EU-institutioners öppna API:er — aldrig ur modellens egen förträningskunskap,
och aldrig utan en klickbar källänk per påstående.

Se [`README.md`](README.md) för hur systemet används och
[`docs/ARKITEKTUR.md`](docs/ARKITEKTUR.md) för hur citeringskravet är byggt
in strukturellt (ett tvingande JSON-schema i syntesfasen), inte som en
prompt-instruktion en modell kan glömma bort.

## Principer som styr utvecklingen

Dessa är inte stilval — de är svar på konkreta misstag och avvägningar som
gjorts under bygget, och de gäller lika mycket för framtida ändringar:

1. **Ingen kod skrivs mot en gissad API-endpoint.** En källa i
   `kallor/kallregister.yaml` markeras `verifierad: nej` tills anropet är
   gjort live och svaret faktiskt inspekterat. `aktiverad: false` spärrar
   anrop i transportlagret tills dess — se `test_ej_verifierade_ar_inte_aktiverade`
   i testsviten, som upprätthåller det som en invariant, inte en avsikt.

2. **Vissa källor är permanent avstängda på affärsmässiga/etiska grunder, inte
   tekniska.** Polisens efterlysta och Bolagsverkets register över verkliga
   huvudmän rör personuppgifter om enskilda fysiska personer och är blockerade
   i källregistret (`blockerad: true`) — kontrollerat i adapterlagrets ingång,
   inte i en systemprompt som går att kringgå eller glömma. Detta skiljer sig
   från Bolagsverkets värdefulla datamängder (organisationsdata: namn, form,
   adress, SNI-koder, inlämnade årsredovisningar), som inte är personuppgifter
   och är en aktiv källa.

3. **Testsviten får aldrig krympa.** Antalet passerade tester är en golvlinje,
   inte ett mål — varje ändring ska lämna fler eller lika många gröna tester
   som den hittade, aldrig färre.

4. **Publicering är en mänsklig handling.** Kodändringar förbereds och
   verifieras, men `git push`, ändrad synlighet på repot och driftsättning mot
   produktion görs aldrig automatiskt — det är alltid ett separat, medvetet
   beslut.

5. **Hemligheter lämnar aldrig repot.** API-nycklar och klientuppgifter
   (t.ex. `BOLAGSVERKET_CLIENT_ID`/`_SECRET`, `ANTHROPIC_API_KEY`) läses ur
   miljövariabler (`.env`, gitignorerad) och finns bara som tomma
   platshållare i `.env.example`. Detta upprätthålls av
   `tests/test_inga_hemligheter.py`, inte bara av en instruktion.

## Var man börjar

- [`docs/ARKITEKTUR.md`](docs/ARKITEKTUR.md) — systemets design och varför.
- [`docs/PLAN.md`](docs/PLAN.md) — byggloggen, steg för steg med
  acceptanskriterier och vad som faktiskt verifierades i varje steg.
- `kallor/kallregister.yaml` — enda sanningskällan för vilka datakällor som
  finns, hur de nås, och vilka som är verifierade respektive spärrade.
