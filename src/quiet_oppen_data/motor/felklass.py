"""Felklassificering av undantag från Anthropic-anropen.

Bakgrund: 2026-09-13 svarade chatten "Ett tekniskt fel inträffade" på varje
fråga i produktion. Orsaken var slut på kredit — ett *förväntat* drifttillstånd
(kontot är avsiktligt förskottsbetalt utan auto-reload, ARKITEKTUR.md §6a) —
men i loggen såg det likadant ut som ett nätverksglapp eller ett kodfel. Ingen
märkte något på veckor.

Modulen är avsiktligt en ren funktion utan klient och utan nyckelkrav: den kan
importeras på modulnivå i api.py utan att bryta invarianten att /kallor och
/halsa fungerar utan ANTHROPIC_API_KEY.

Klassificeringen är körd och verifierad mot undantagshierarkin i **båda**
ändarna av pinningen `anthropic>=0.112,<2`: 0.112.0 (versionen koden i övrigt
är verifierad mot) och 1.5.0 (versionen produktionsavbilden fick vid
ombyggnaden 2026-09-10). Hierarkin och attributnamnen är identiska i båda —
1.x bytte HTTP-klient från `httpx` till `httpx2`, men undantagen läses bara
på `status_code`, `body` och `str()`, som finns i båda.
"""

from __future__ import annotations

import anthropic

# Slut på kredit är ett HTTP 400 från Anthropic, inte en egen undantagsklass —
# den enda skillnaden mot ett vanligt formatfel ligger i svarskroppens text.
_BILLINGMARKORER = ("credit balance", "billing")

# Status 529 = "Overloaded". SDK:n har en egen OverloadedError-klass, men den
# står bara i _exceptions.__all__ från 1.x (i 0.112.0 är den odokumenterad).
# Statuskoden är det dokumenterade kontraktet och fångar båda: OverloadedError
# ärver APIStatusError och bär 529.
_OVERBELASTAD_STATUS = 529


def _feltext(exc: BaseException) -> str:
    """Textunderlag för strängmatchningen, gemener.

    SDK:n bygger meddelandet för ett statusfel som ``"Error code: 400 - {body}"``,
    så svarskroppen ligger redan i ``str(exc)``. ``body`` läses ändå med, för det
    fall en framtida SDK-version slutar väva in den i meddelandet.
    """
    delar = [str(exc)]
    kropp = getattr(exc, "body", None)
    if kropp is not None:
        delar.append(str(kropp))
    return " ".join(delar).lower()


def klassificera_anthropic_fel(exc: BaseException) -> str:
    """Klassificerar ett undantag från fas A/B/C i en grep-vänlig felklass.

    Returnerar exakt en av:

      ``auth``       — nyckeln avvisades (401). Driftstopp tills en människa agerar.
      ``billing``    — slut på kredit / spärrad fakturering. Driftstopp.
      ``rate_limit`` — 429. Övergående.
      ``overloaded`` — 529 eller 5xx hos Anthropic. Övergående.
      ``natverk``    — anslutning bröts eller tog timeout. Övergående.
      ``api_ovrigt`` — övriga fel från API:t.
      ``internt``    — allt annat: kodfel, adapterfel, fel utanför anthropic.

    Klassificeringen tittar bara på undantaget som faktiskt kastades, inte på
    ``__cause__``-kedjan: motorlagret släpper igenom SDK:ns undantag orört
    (se ``motor/hamtning.py`` och ``motor/syntes.py``), så det finns inget att
    packa upp.
    """
    if not isinstance(exc, anthropic.APIError):
        return "internt"

    if isinstance(exc, anthropic.AuthenticationError):
        return "auth"

    if isinstance(exc, anthropic.BadRequestError | anthropic.PermissionDeniedError):
        text = _feltext(exc)
        if any(markor in text for markor in _BILLINGMARKORER):
            return "billing"

    if isinstance(exc, anthropic.RateLimitError):
        return "rate_limit"

    if isinstance(exc, anthropic.InternalServerError):
        return "overloaded"

    if (
        isinstance(exc, anthropic.APIStatusError)
        and getattr(exc, "status_code", None) == _OVERBELASTAD_STATUS
    ):
        return "overloaded"

    if isinstance(exc, anthropic.APIConnectionError):
        return "natverk"

    return "api_ovrigt"
