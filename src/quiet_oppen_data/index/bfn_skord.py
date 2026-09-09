"""Skörd av Bokföringsnämndens webbplats (steg 23).

BFN har inget API för sitt sakinnehåll. De publicerar allmänna råd,
vägledningar och uttalanden som pdf. Enligt BFN:s egen PSI-sida är de fritt
tillgängliga och får vidareutnyttjas utan avtal, mot källangivelse — se
kallregister.yaml → bokforingsnamnden.

Skörden går i fyra steg:

  1. Räkna upp sidorna via WordPress REST (/wp-json/wp/v2/pages). 131 sidor.
     Det ger sidträdet (parent -> slug) och därmed den sökväg som
     kategoriseringen vilar på.
  2. Räkna upp bilagorna via /wp-json/wp/v2/media. Ger ändringsdatum och en
     fullständig lista att stämma av mot.
  3. Hämta varje sida som HTML och plocka ut pdf-länkar med ankartext.
     REST-svarets content.rendered är TOMT på bfn.se — kontrollerat 2026-09-09,
     noll tecken på samtliga stickprov. Sidorna MÅSTE hämtas som HTML, och det
     är också bara där BFN:s egen indelning finns.
  4. Ladda ner pdf:erna för de nivåer registret pekar ut.

Steg 2 är det som gör skörden fullständig: en pdf som ingen sida länkar till
skulle annars falla bort tyst.

Härkomst: strukturen är portad från systerprojektets regelverksmodul. Varje
uppgift om bfn.se är omkontrollerad mot källan 2026-09-09 enligt ARBETSORDER
princip 1 — en verifiering ärvs inte.
"""
from __future__ import annotations

import hashlib
import html as htmlmod
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from quiet_oppen_data import bfnregister
from quiet_oppen_data.adaptrar import transport
from quiet_oppen_data.bfnregister import BfnRegister
from quiet_oppen_data.konfig import las as las_konfig
from quiet_oppen_data.register import Kalla, hamta

logger = logging.getLogger(__name__)

KALLA = "bokforingsnamnden"
BAS = "https://www.bfn.se"

_ANKARE = re.compile(r'<a\b[^>]*?href="([^"]+)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
_TAGG = re.compile(r"<[^>]+>")
_SKRIPT = re.compile(r"<(script|style|nav|header|footer)\b.*?</\1>", re.IGNORECASE | re.DOTALL)


def _nu() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", htmlmod.unescape(_TAGG.sub(" ", fragment))).strip()


def bfn_rot() -> Path:
    """Skördens rot, granne med indexdatabasen (data/bfn/)."""
    return Path(las_konfig().index.db).parent / "bfn"


@dataclass
class Bilaga:
    """En pdf hos BFN, med den kategori sidan eller filnamnet gav den."""

    filnamn: str
    url: str
    titel: str
    kategori: str
    typ: str
    niva: str
    sidor: list[str] = field(default_factory=list)
    url_alternativ: list[str] = field(default_factory=list)
    media_id: int | None = None
    andrad: str | None = None
    lokal_fil: str = ""
    storlek_byte: int = 0
    sha256: str = ""
    hamtad: str = ""
    status: str = "ny"
    fel: str = ""

    def till_dict(self) -> dict[str, Any]:
        return {
            "filnamn": self.filnamn, "url": self.url, "titel": self.titel,
            "kategori": self.kategori, "typ": self.typ, "niva": self.niva,
            "sidor": self.sidor, "url_alternativ": self.url_alternativ,
            "media_id": self.media_id, "andrad": self.andrad,
            "lokal_fil": self.lokal_fil, "storlek_byte": self.storlek_byte,
            "sha256": self.sha256, "hamtad": self.hamtad,
            "status": self.status, "fel": self.fel,
        }


# ---------------------------------------------------------------------------
# Steg 1-2: uppräkning via WordPress REST
# ---------------------------------------------------------------------------


def _rest_alla(resurs: str, falt: str) -> list[dict[str, Any]]:
    """Hämtar alla poster ur en paginerad WP-REST-resurs.

    Sidantalet läses ur X-WP-TotalPages, ALDRIG ur antalet poster i svaret.
    bfn.se levererar 93 poster på en sida med per_page=100 mitt i en följd om
    950 — WordPress filtrerar bort poster efter att sidan skurits ut.
    Villkoret "färre än per_page betyder sista sidan" ger därför ett fåtal
    poster i stället för alla, och det är precis den sortens fel som inte syns
    i en lyckad körning. Kontrollerat mot bfn.se 2026-09-09.

    Att huvudena alls går att läsa är skälet till att den ocachade
    transportvägen används här — HTTP-cachen sparar bara kroppen.
    """
    ut: list[dict[str, Any]] = []
    sida = 1
    antal_sidor: int | None = None
    uppgivet_totalt: str | None = None

    while True:
        url = f"{BAS}/wp-json/wp/v2/{resurs}"
        params = {"per_page": 100, "page": sida, "_fields": falt}
        try:
            svar = transport.hamta_ocachat(KALLA, "GET", url, params=params)
        except Exception as e:
            if sida == 1:
                raise
            logger.warning("Uppräkningen av %s bröts vid sida %d: %s", resurs, sida, e)
            break

        if antal_sidor is None:
            try:
                antal_sidor = int(svar.huvuden.get("x-wp-totalpages") or 0)
            except ValueError:
                antal_sidor = 0
            uppgivet_totalt = svar.huvuden.get("x-wp-total")
            logger.info("%s: %s poster på %s sidor", resurs, uppgivet_totalt, antal_sidor)

        ut.extend(json.loads(svar.text()))

        if antal_sidor and sida >= antal_sidor:
            break
        if not antal_sidor:
            logger.warning(
                "%s saknade X-WP-TotalPages; uppräkningen avbryts efter sida 1 och "
                "kan vara ofullständig.", resurs,
            )
            break
        sida += 1
        if sida > 100:
            logger.warning("Avbryter uppräkning av %s vid sida 100.", resurs)
            break

    if uppgivet_totalt and str(len(ut)) != str(uppgivet_totalt):
        # Känt beteende hos bfn.se (942 av 950 vid kontrollen 2026-09-09). Det
        # får inte tystna: den dag differensen växer är det ett riktigt
        # bortfall, och då ska den här raden redan finnas i loggen att jämföra
        # mot.
        logger.warning(
            "%s: hämtade %d poster men API:t uppger %s. Differensen är känd för "
            "bfn.se (WordPress filtrerar efter sidindelningen) — men kontrollera "
            "innan skörden räknas som fullständig.",
            resurs, len(ut), uppgivet_totalt,
        )
    return ut


def hamta_sidtrad() -> list[dict[str, Any]]:
    """Alla sidor med härledd sökväg (t.ex. 'redovisningsregler/vagledningar')."""
    sidor = _rest_alla("pages", "id,parent,slug,link,title,modified")
    per_id = {s["id"]: s for s in sidor}

    def sokvag(s: dict[str, Any]) -> str:
        delar: list[str] = []
        cur: dict[str, Any] | None = s
        sedda: set[int] = set()
        while cur is not None and cur["id"] not in sedda:
            sedda.add(cur["id"])
            delar.append(str(cur.get("slug") or ""))
            cur = per_id.get(cur.get("parent") or 0)
        return "/".join(reversed([d for d in delar if d]))

    for s in sidor:
        s["sokvag"] = sokvag(s)
        s["titel"] = _text(str((s.get("title") or {}).get("rendered", "")))
    return sorted(sidor, key=lambda s: s["sokvag"])


def hamta_mediabibliotek() -> list[dict[str, Any]]:
    """Alla bilagor med mime_type, källurl och ändringsdatum."""
    media = _rest_alla("media", "id,date,modified,slug,link,title,source_url,mime_type")
    for m in media:
        m["titel"] = _text(str((m.get("title") or {}).get("rendered", "")))
    return media


# ---------------------------------------------------------------------------
# Steg 3: sidorna som HTML
# ---------------------------------------------------------------------------


def _pdf_lankar(html: str, filtyper: tuple[str, ...]) -> list[tuple[str, str]]:
    """(url, ankartext) för varje länk till en fil av registrerad typ."""
    ut: list[tuple[str, str]] = []
    for href, inner in _ANKARE.findall(html):
        url = htmlmod.unescape(href.strip())
        if not any(url.lower().split("?")[0].endswith(t) for t in filtyper):
            continue
        if url.startswith("/"):
            url = BAS + url
        elif not url.startswith("http"):
            continue
        ut.append((url, _text(inner)))
    return ut


def _brodtext(html: str) -> str:
    kropp = _SKRIPT.sub(" ", html)
    m = re.search(r"<main\b.*?</main>", kropp, re.IGNORECASE | re.DOTALL)
    if m:
        kropp = m.group(0)
    return _text(kropp)


def skorda_sidor(
    sidor: list[dict[str, Any]], reg: BfnRegister
) -> tuple[dict[str, Bilaga], list[dict[str, Any]]]:
    """Hämtar varje sida som HTML och samlar bilagor och sidtext.

    Sidorna hämtas ocachat. Skälet är inte att svaret skulle vara binärt utan
    att en skörd är ett satsjobb: 131 sidor à ~55 KB hade lagt närmare 7 MB
    HTML i den cache som finns för att svara på frågor, och trängt ut det som
    faktiskt efterfrågas.
    """
    bilagor: dict[str, Bilaga] = {}
    rang: dict[str, int] = {}
    sidposter: list[dict[str, Any]] = []
    sidtext_rot = bfn_rot() / "sidor"

    for i, s in enumerate(sidor, 1):
        url = s.get("link") or f"{BAS}/{s['sokvag']}/"
        kat = reg.kategorisera_sidvag(s["sokvag"])
        try:
            html = transport.hamta_ocachat(KALLA, "GET", url).text()
        except Exception as e:
            logger.warning("Sidan %s kunde inte hämtas: %s", s["sokvag"], e)
            sidposter.append({"sokvag": s["sokvag"], "url": url, "status": "fel", "fel": str(e)})
            continue

        lankar = _pdf_lankar(html, reg.filtyper)
        for lank_url, ankartext in lankar:
            filnamn = lank_url.split("?")[0].rsplit("/", 1)[-1]
            befintlig = bilagor.get(filnamn)
            if befintlig is None:
                bilagor[filnamn] = Bilaga(
                    filnamn=filnamn, url=lank_url, titel=ankartext or filnamn,
                    kategori=kat.kategori, typ=kat.typ, niva=kat.niva,
                    sidor=[s["sokvag"]],
                )
                rang[filnamn] = kat.rang
            else:
                if s["sokvag"] not in befintlig.sidor:
                    befintlig.sidor.append(s["sokvag"])
                if lank_url != befintlig.url and lank_url not in befintlig.url_alternativ:
                    befintlig.url_alternativ.append(lank_url)
                # Bättre kategori (lägre rang) vinner, och tar ankartexten med sig.
                if kat.rang < rang.get(filnamn, 10**6):
                    rang[filnamn] = kat.rang
                    befintlig.kategori = kat.kategori
                    befintlig.typ = kat.typ
                    befintlig.niva = kat.niva
                    if ankartext and len(ankartext) > len(befintlig.titel):
                        befintlig.titel = ankartext

        post: dict[str, Any] = {
            "sokvag": s["sokvag"], "titel": s.get("titel", ""), "url": url,
            "kategori": kat.kategori, "niva": kat.niva,
            "andrad": s.get("modified", ""), "antal_pdf": len(lankar), "status": "ok",
        }
        # Frågor och svar bär sitt värde i sidtexten, inte i bilagor.
        if kat.skorda_sidtext:
            txt = _brodtext(html)
            filnamn = s["sokvag"].replace("/", "__") + ".json"
            sidtext_rot.mkdir(parents=True, exist_ok=True)
            (sidtext_rot / filnamn).write_text(
                json.dumps({
                    "sokvag": s["sokvag"], "titel": s.get("titel", ""), "url": url,
                    "kategori": kat.kategori, "niva": kat.niva,
                    "andrad": s.get("modified", ""), "hamtad": _nu(), "text": txt,
                }, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
            post["sidtext_fil"] = f"sidor/{filnamn}"
            post["tecken"] = len(txt)

        sidposter.append(post)
        if i % 20 == 0:
            logger.info("Skördat %d/%d sidor, %d bilagor hittills", i, len(sidor), len(bilagor))

    return bilagor, sidposter


# ---------------------------------------------------------------------------
# Steg 2b: bilagor som ingen sida länkar till
# ---------------------------------------------------------------------------


def komplettera_med_media(
    bilagor: dict[str, Bilaga], media: list[dict[str, Any]], reg: BfnRegister
) -> int:
    """Lägger till bilagor som ingen sida länkar till, och fyller på ändringsdatum."""
    tillagda = 0
    for m in media:
        url = m.get("source_url") or ""
        if not url:
            continue
        if not any(url.lower().split("?")[0].endswith(t) for t in reg.filtyper):
            continue
        filnamn = url.split("?")[0].rsplit("/", 1)[-1]
        if filnamn in bilagor:
            b = bilagor[filnamn]
            b.media_id = m.get("id")
            b.andrad = m.get("modified")
            # Mediabibliotekets source_url är den auktoritativa adressen.
            # BFN:s sidor länkar ibland till en gammal sökväg som ger 404 —
            # vägledningen Bokföring länkas från många sidor till
            # /uploads/2020/06/, men ligger på /uploads/. Utan den här raden
            # faller ett nyckeldokument bort utan att någon sida är trasig.
            if url != b.url:
                if b.url not in b.url_alternativ:
                    b.url_alternativ.append(b.url)
                b.url = url
            continue
        if not reg.ta_med_foraldralosa_bilagor:
            continue
        kategori, typ, niva = reg.kategorisera_filnamn(filnamn)
        bilagor[filnamn] = Bilaga(
            filnamn=filnamn, url=url, titel=m.get("titel") or filnamn,
            kategori=kategori, typ=typ, niva=niva, sidor=[],
            media_id=m.get("id"), andrad=m.get("modified"),
        )
        tillagda += 1
    return tillagda


# ---------------------------------------------------------------------------
# Steg 4: nedladdning
# ---------------------------------------------------------------------------


def ladda_ner(
    bilagor: dict[str, Bilaga], reg: BfnRegister, *, tvinga: bool = False
) -> dict[str, int]:
    """Laddar ner pdf:erna för de nivåer registret pekar ut."""
    pdf_rot = bfn_rot() / "pdf"
    pdf_rot.mkdir(parents=True, exist_ok=True)
    maxbyte = reg.max_filstorlek_mb * 1024 * 1024

    rakning = {"hamtade": 0, "oforandrade": 0, "hoppade": 0, "externa": 0, "fel": 0}

    for i, b in enumerate(sorted(bilagor.values(), key=lambda x: x.filnamn), 1):
        # BFN länkar vidare till andra myndigheters pdf:er (t.ex. SOU:er på
        # regeringen.se). De hämtas INTE här. Två skäl: de omfattas inte av
        # BFN:s PSI-villkor, och en fil hämtad via den här källan skulle bära
        # attributionen "Källa: Bokföringsnämnden" — vilket vore fel om
        # dokumentet är Regeringskansliets. Länken sparas i registret så att
        # ingenting försvinner; den som vill ha dokumentet får registrera
        # källan i kallregister.yaml först.
        if not b.url.startswith(("https://www.bfn.se/", "https://bfn.se/")):
            b.status = "extern_lank"
            b.fel = "Länken pekar utanför bfn.se. Registrera källan i kallregister.yaml först."
            rakning["externa"] += 1
            continue

        if not reg.far_laddas_ner(b.niva):
            b.status = "ej_hamtad_enligt_register"
            rakning["hoppade"] += 1
            continue

        mal = pdf_rot / b.filnamn
        if mal.exists() and not tvinga:
            data = mal.read_bytes()
            b.lokal_fil = f"pdf/{b.filnamn}"
            b.storlek_byte = len(data)
            b.sha256 = hashlib.sha256(data).hexdigest()
            b.status = "oforandrad"
            rakning["oforandrade"] += 1
            continue

        # Huvudadressen först, sedan de alternativ samma filnamn förekommit på.
        data = None
        sista_fel = ""
        for adress in [b.url, *b.url_alternativ]:
            if not adress.startswith(("https://www.bfn.se/", "https://bfn.se/")):
                continue
            try:
                data = transport.hamta_ocachat(KALLA, "GET", adress).innehall
            except Exception as e:
                sista_fel = str(e)
                continue
            if adress != b.url:
                logger.info("%s: huvudadressen svarade inte, hämtad från %s", b.filnamn, adress)
                b.url = adress
            break

        if data is None:
            b.status = "fel"
            b.fel = sista_fel
            rakning["fel"] += 1
            logger.warning("Kunde inte hämta %s: %s", b.filnamn, sista_fel)
            continue
        if len(data) > maxbyte:
            b.status = "fel"
            b.fel = (
                f"Filen är {len(data) // 1024 // 1024} MB, "
                f"över gränsen {reg.max_filstorlek_mb} MB."
            )
            rakning["fel"] += 1
            continue
        if not data.startswith(b"%PDF"):
            b.status = "fel"
            b.fel = "Svaret var inte en pdf."
            rakning["fel"] += 1
            continue

        mal.write_bytes(data)
        b.lokal_fil = f"pdf/{b.filnamn}"
        b.storlek_byte = len(data)
        b.sha256 = hashlib.sha256(data).hexdigest()
        b.hamtad = _nu()
        b.status = "hamtad"
        rakning["hamtade"] += 1
        if rakning["hamtade"] % 25 == 0:
            logger.info("Nedladdat %d filer (%d av %d behandlade)",
                        rakning["hamtade"], i, len(bilagor))

    return rakning


# ---------------------------------------------------------------------------
# Nyckeldokumentkontroll
# ---------------------------------------------------------------------------


def kontrollera_nyckeldokument(
    bilagor: dict[str, Bilaga], reg: BfnRegister
) -> list[dict[str, Any]]:
    """Stämmer av att registrets minimikrav faktiskt kom hem.

    Konsoliderade vägledningar byter filnamn när de uppdateras, så poster med
    `monster` matchas på mönstret och den senaste (sist i bokstavsordning,
    vilket för BFN:s namnschema är samma som senast daterad) väljs.

    En körning som inte fått hem allihop är INTE fullständig, och det ska stå
    rakt ut i skörderapporten i stället för att rapporteras som "klart".
    """
    hamtade = {namn for namn, b in bilagor.items() if b.status in ("hamtad", "oforandrad")}
    rapport: list[dict[str, Any]] = []
    for nd in reg.nyckeldokument:
        traff: str | None = None
        alternativ: list[str] = []
        if nd.fil:
            traff = nd.fil if nd.fil in hamtade else None
        elif nd.monster:
            alternativ = sorted(f for f in hamtade if re.search(nd.monster, f, re.IGNORECASE))
            traff = alternativ[-1] if alternativ else None
        rapport.append({
            "id": nd.id, "bfnar": nd.bfnar, "namn": nd.namn, "krav": nd.krav,
            "fil": traff, "alternativ": alternativ if len(alternativ) > 1 else [],
            "status": "ok" if traff else "SAKNAS",
        })
    return rapport


# ---------------------------------------------------------------------------
# Orkestrering
# ---------------------------------------------------------------------------


def registerfil() -> Path:
    return bfn_rot() / "bfn_register.json"


def skorda(*, tvinga: bool = False) -> dict[str, Any]:
    """Full skörd av bfn.se. Skriver bfn_register.json och returnerar rapporten."""
    reg = bfnregister.las()
    kalla = hamta(KALLA)
    if not isinstance(kalla, Kalla):
        raise RuntimeError(f"Källan {KALLA} saknas eller är blockerad i källregistret.")
    bfn_rot().mkdir(parents=True, exist_ok=True)

    logger.info("Räknar upp sidor ...")
    sidor = hamta_sidtrad()
    logger.info("%d sidor.", len(sidor))

    logger.info("Räknar upp mediabiblioteket ...")
    media = hamta_mediabibliotek()
    logger.info("%d bilagor, varav %d pdf.", len(media),
                sum(1 for m in media if m.get("mime_type") == "application/pdf"))

    logger.info("Skördar sidorna som HTML ...")
    bilagor, sidposter = skorda_sidor(sidor, reg)
    logger.info("%d bilagor länkade från sidor.", len(bilagor))

    tillagda = komplettera_med_media(bilagor, media, reg)
    logger.info("%d ytterligare bilagor fanns bara i mediabiblioteket.", tillagda)

    logger.info("Laddar ner nivåerna %s ...", sorted(reg.ladda_ner_nivaer))
    rakning = ladda_ner(bilagor, reg, tvinga=tvinga)

    nyckel = kontrollera_nyckeldokument(bilagor, reg)
    saknade = [n for n in nyckel if n["status"] == "SAKNAS"]

    attribution = (kalla.attribution or "").replace(
        "{datum}", datetime.now(UTC).date().isoformat()
    )
    register = {
        "kalla": kalla.myndighet,
        "licens": kalla.licens,
        "attribution": attribution,
        "manniskolank_mall": kalla.manniskolank_mall,
        "skordad": _nu(),
        "antal_sidor": len(sidor),
        "antal_bilagor": len(bilagor),
        "nedladdning": rakning,
        "endast_i_mediabiblioteket": tillagda,
        "nyckeldokument": nyckel,
        "sidor": sidposter,
        "bilagor": [b.till_dict() for b in sorted(bilagor.values(), key=lambda x: x.filnamn)],
    }
    registerfil().write_text(
        json.dumps(register, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    if saknade:
        logger.error("%d nyckeldokument SAKNAS: %s", len(saknade),
                     ", ".join(n["id"] for n in saknade))

    return register


def main() -> None:
    import argparse
    import sys

    p = argparse.ArgumentParser(description="Skörd av bfn.se (steg 23)")
    p.add_argument("--tvinga", action="store_true", help="Ladda ner även filer som redan finns")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        stream=sys.stdout)
    rapport = skorda(tvinga=args.tvinga)
    print("\n--- Skörd klar ---")
    print(f"  sidor:   {rapport['antal_sidor']}")
    print(f"  bilagor: {rapport['antal_bilagor']}")
    print(f"  nedladdning: {rapport['nedladdning']}")
    saknade = [n for n in rapport["nyckeldokument"] if n["status"] == "SAKNAS"]
    ok = len(rapport["nyckeldokument"]) - len(saknade)
    print(f"  nyckeldokument: {ok} ok, {len(saknade)} SAKNAS")
    for n in saknade:
        print(f"    SAKNAS: {n['id']} ({n['bfnar']}) - {n['namn']}")


if __name__ == "__main__":
    main()
