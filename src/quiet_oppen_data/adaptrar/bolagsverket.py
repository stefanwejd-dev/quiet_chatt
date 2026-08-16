"""Bolagsverket HVD-adapter — API för värdefulla datamängder.

Enda källan i registret som kräver OAuth2 (client_credentials). Skiljer sig
därför från övriga adaptrar på en punkt: den hämtar en access-token separat
från transport.hamta_json och cachar den i minnet (se _hamta_token) i stället
för i HTTP-cachen — token-endpointen är inte källans dataändpunkt och ska
varken räknas i källans cache-träffkvot eller ligga kvar på disk.

OMFATTNING: enbart /organisationer och /dokumentlista under
vardefulla-datamangder/v1. Bolagsverkets övriga API:er (avgiftsbelagd
företagsinformation, Verkliga huvudmän) är uttryckligen utanför — den senare
är dessutom spärrad i registret, se kallregister.yaml.

Svarsschemat är avläst live 2026-08-16 mot produktionsgatewayen (se
kallregister.yaml, bolagsverket_hvd → verifierat_anrop). Adaptern läser bara
fält som faktiskt observerats i ett svar.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import Any

import httpx

from quiet_oppen_data.adaptrar.transport import hamta_json
from quiet_oppen_data.modeller import Faktautkast, Fragplan
from quiet_oppen_data.register import Kalla, hamta

logger = logging.getLogger(__name__)

_TOKEN_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)

# Token-cache i minnet: {kalla_id: (access_token, utgår_monotonic)}. Separat
# lås eftersom flera trådar kan hämta samtidigt — samma mönster som
# transport.TokenBucket.
_token_cache: dict[str, tuple[str, float]] = {}
_token_lock = threading.Lock()

# Marginal innan en cachad token räknas som utgången — undviker att skicka
# en token som hinner löpa ut mellan kontroll och anrop.
_TOKEN_MARGINAL_SEK = 60.0


def _hamta_token(kalla: Kalla) -> str:
    """Hämtar en OAuth2 access-token (client_credentials) och cachar den i minnet."""
    with _token_lock:
        cached = _token_cache.get(kalla.id)
        if cached and cached[1] > time.monotonic() + _TOKEN_MARGINAL_SEK:
            return cached[0]

    client_id = os.environ.get("BOLAGSVERKET_CLIENT_ID")
    client_secret = os.environ.get("BOLAGSVERKET_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError(
            "BOLAGSVERKET_CLIENT_ID/BOLAGSVERKET_CLIENT_SECRET saknas i miljön."
        )
    if not kalla.token_url:
        raise RuntimeError(f"{kalla.id}: token_url saknas i källregistret.")

    with httpx.Client(timeout=_TOKEN_TIMEOUT) as client:
        res = client.post(
            kalla.token_url,
            auth=(client_id, client_secret),
            data={"grant_type": "client_credentials", "scope": kalla.oauth_scope or ""},
        )
    res.raise_for_status()
    data = res.json()
    token = data["access_token"]
    utgar_om = float(data.get("expires_in", 3600))

    with _token_lock:
        _token_cache[kalla.id] = (token, time.monotonic() + utgar_om)

    return token


def _rensa_identitetsbeteckning(varde: str) -> str:
    """Tar bort bindestreck/mellanslag — API:t vill ha en sammanhängande sträng siffror."""
    return re.sub(r"[^0-9]", "", varde)


class BolagsverketAdapter:
    """Adapter för Bolagsverkets API för värdefulla datamängder (HVD)."""

    def __init__(self) -> None:
        k = hamta("bolagsverket_hvd")
        if not isinstance(k, Kalla):
            raise RuntimeError("Bolagsverket HVD-källan saknas eller är blockerad i registret.")
        self._kalla = k

    @property
    def id(self) -> str:
        return self._kalla.id

    def beskriv(self) -> list[dict[str, Any]]:
        identitet_prop = {
            "identitetsbeteckning": {
                "type": "string",
                "description": (
                    "Organisationsnummer, 10 siffror, med eller utan bindestreck "
                    "(t.ex. 5560125790 eller 556012-5790)."
                ),
            }
        }
        return [
            {
                "name": self.id,
                "description": (
                    "Hämtar grunddata om en svensk organisation från Bolagsverkets "
                    "värdefulla datamängder: namn, organisationsform, registreringsdatum, "
                    "postadress, SNI-koder, verksamhetsbeskrivning, reklamspärr, om "
                    "organisationen är verksam samt eventuell avregistrering."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": identitet_prop,
                    "required": ["identitetsbeteckning"],
                },
            },
            {
                "name": f"{self.id}_dokumentlista",
                "description": (
                    "Listar digitalt inlämnade årsredovisningar (iXBRL) för en svensk "
                    "organisation hos Bolagsverket."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": identitet_prop,
                    "required": ["identitetsbeteckning"],
                },
            },
        ]

    # ------------------------------------------------------------------
    # Hämtning
    # ------------------------------------------------------------------

    def hamta(self, plan: Fragplan) -> list[Faktautkast]:
        rå_identitet = plan.extra.get("identitetsbeteckning")
        if not rå_identitet:
            logger.info("%s: anrop utan identitetsbeteckning", self.id)
            return []
        identitet = _rensa_identitetsbeteckning(str(rå_identitet))
        if not identitet:
            logger.info("%s: identitetsbeteckning tom efter rensning", self.id)
            return []

        try:
            token = _hamta_token(self._kalla)
        except Exception:
            logger.warning("%s: kunde inte hämta OAuth2-token", self.id, exc_info=True)
            return []

        headers = {"Authorization": f"Bearer {token}"}
        verktyg = plan.extra.get("verktyg") or self.id

        if verktyg.endswith("_dokumentlista"):
            return self._hamta_dokumentlista(identitet, headers)
        return self._hamta_organisation(identitet, headers)

    def _hamta_organisation(self, identitet: str, headers: dict[str, str]) -> list[Faktautkast]:
        url = f"{self._kalla.bas_url}/organisationer"
        try:
            res = hamta_json(
                self.id, "POST", url, headers=headers, json={"identitetsbeteckning": identitet}
            )
        except Exception:
            logger.warning("%s: hämtning för %s misslyckades", self.id, identitet, exc_info=True)
            return []

        organisationer = (res or {}).get("organisationer") or []
        if not organisationer:
            logger.info("%s: ingen organisation för %s", self.id, identitet)
            return []
        org = organisationer[0]

        manniska = self._kalla.manniskolank_mall or self._kalla.bas_url

        def utkast(etikett: str, varde: str, myndighet: str | None, period: str | None = None) -> Faktautkast:
            return Faktautkast(
                etikett=etikett,
                varde=varde,
                period=period,
                kalla_id=self.id,
                myndighet=myndighet or self._kalla.myndighet or "Bolagsverket",
                licens=self._kalla.licens,
                attribution=self._kalla.attribution,
                dataset=identitet,
                lank_manniska=manniska,
                lank_maskin=url,
            )

        resultat: list[Faktautkast] = []

        def falt(container: dict | None) -> dict | None:
            """Returnerar containern om den finns och saknar 'fel', annars None."""
            if not container or container.get("fel"):
                return None
            return container

        namn = falt(org.get("organisationsnamn"))
        if namn:
            lista = namn.get("organisationsnamnLista") or []
            if lista:
                huvudnamn = lista[0].get("namn")
                if huvudnamn:
                    resultat.append(utkast(
                        f"Organisationsnamn för {identitet}", huvudnamn, namn.get("dataproducent"),
                    ))
                if len(lista) > 1:
                    ovriga = "; ".join(
                        n["namn"] for n in lista[1:] if n.get("namn")
                    )
                    if ovriga:
                        resultat.append(utkast(
                            f"Samtliga registrerade namn för {identitet}",
                            ovriga, namn.get("dataproducent"),
                        ))

        org_form = falt(org.get("organisationsform"))
        if org_form and org_form.get("klartext"):
            resultat.append(utkast(
                f"Organisationsform för {identitet}", org_form["klartext"], org_form.get("dataproducent"),
            ))

        jur_form = falt(org.get("juridiskForm"))
        if jur_form and jur_form.get("klartext"):
            resultat.append(utkast(
                f"Juridisk form för {identitet} (SCB)", jur_form["klartext"], jur_form.get("dataproducent"),
            ))

        reklamsparr = falt(org.get("reklamsparr"))
        if reklamsparr and reklamsparr.get("kod"):
            resultat.append(utkast(
                f"Reklamspärr för {identitet} (SCB)", reklamsparr["kod"], reklamsparr.get("dataproducent"),
            ))

        verksam = falt(org.get("verksamOrganisation"))
        if verksam and verksam.get("kod"):
            resultat.append(utkast(
                f"Verksam organisation (SCB) för {identitet}", verksam["kod"], verksam.get("dataproducent"),
            ))

        avreg = falt(org.get("avregistradOrganisation") or org.get("avregistreradOrganisation"))
        if avreg and avreg.get("avregistreringsdatum"):
            resultat.append(utkast(
                f"Avregistreringsdatum för {identitet}", avreg["avregistreringsdatum"],
                avreg.get("dataproducent"),
            ))

        avreg_orsak = falt(org.get("avregistreringsorsak"))
        if avreg_orsak and avreg_orsak.get("klartext"):
            resultat.append(utkast(
                f"Avregistreringsorsak för {identitet}", avreg_orsak["klartext"],
                avreg_orsak.get("dataproducent"),
            ))

        avveckling = falt(org.get("pagaendeAvvecklingsEllerOmstruktureringsforfarande"))
        if avveckling:
            lista = avveckling.get("pagaendeAvvecklingsEllerOmstruktureringsforfarandeLista") or []
            beskrivning = "; ".join(str(p) for p in lista if p)
            if beskrivning:
                resultat.append(utkast(
                    f"Pågående avvecklings- eller omstruktureringsförfarande för {identitet}",
                    beskrivning, avveckling.get("dataproducent"),
                ))

        datum = falt(org.get("organisationsdatum"))
        if datum and datum.get("registreringsdatum"):
            resultat.append(utkast(
                f"Registreringsdatum för {identitet}", datum["registreringsdatum"],
                datum.get("dataproducent"),
            ))

        postadress = falt(org.get("postadressOrganisation"))
        if postadress and postadress.get("postadress"):
            adr = postadress["postadress"]
            delar = [
                adr.get("utdelningsadress"), adr.get("coAdress"),
                " ".join(x for x in (adr.get("postnummer"), adr.get("postort")) if x),
            ]
            adressrad = ", ".join(d for d in delar if d)
            if adressrad:
                resultat.append(utkast(
                    f"Postadress för {identitet}", adressrad, postadress.get("dataproducent"),
                ))

        sni = falt(org.get("naringsgrenOrganisation"))
        if sni:
            koder = [
                f"{s['kod'].strip()} {s.get('klartext', '')}".strip()
                for s in (sni.get("sni") or [])
                if s.get("kod") and s["kod"].strip()
            ]
            if koder:
                resultat.append(utkast(
                    f"SNI-koder för {identitet} (SCB)", "; ".join(koder), sni.get("dataproducent"),
                ))

        verksamhet = falt(org.get("verksamhetsbeskrivning"))
        if verksamhet and verksamhet.get("beskrivning"):
            resultat.append(utkast(
                f"Verksamhetsbeskrivning för {identitet}", verksamhet["beskrivning"],
                verksamhet.get("dataproducent"),
            ))

        return resultat

    def _hamta_dokumentlista(self, identitet: str, headers: dict[str, str]) -> list[Faktautkast]:
        url = f"{self._kalla.bas_url}/dokumentlista"
        try:
            res = hamta_json(
                self.id, "POST", url, headers=headers, json={"identitetsbeteckning": identitet}
            )
        except Exception:
            logger.warning("%s: dokumentlista för %s misslyckades", self.id, identitet, exc_info=True)
            return []

        dokument = (res or {}).get("dokument") or []
        if not dokument:
            logger.info("%s: inga dokument för %s", self.id, identitet)
            return []

        manniska = self._kalla.manniskolank_mall or self._kalla.bas_url
        resultat: list[Faktautkast] = []
        for d in dokument:
            if not isinstance(d, dict):
                continue
            delar = "; ".join(f"{k}: {v}" for k, v in d.items() if v not in (None, ""))
            if not delar:
                continue
            resultat.append(Faktautkast(
                etikett=f"Digitalt inlämnad årsredovisning för {identitet}",
                varde=delar,
                kalla_id=self.id,
                myndighet=self._kalla.myndighet or "Bolagsverket",
                licens=self._kalla.licens,
                attribution=self._kalla.attribution,
                dataset=identitet,
                lank_manniska=manniska,
                lank_maskin=url,
            ))
        return resultat
