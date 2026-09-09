"""Parser för Bokföringsnämndens pdf:er (steg 23).

BFN:s vägledningar har en mycket regelbunden uppbyggnad:

    Kapitel 12 - Materiella anläggningstillgångar     <- kapitelrubrik
    Anskaffningsvärde                                 <- avsnittsrubrik
    Lagtext                                           <- blockmarkör
    4 kap. 3 § årsredovisningslagen ...
    Allmänt råd                                       <- blockmarkör
    12.7 I anskaffningsvärdet ingår ...               <- numrerat råd
    Kommentar                                         <- blockmarkör
    Med anskaffningsvärde avses ...

Blockmarkörerna står ensamma på en rad. Det är dem parsern hänger upp sig på,
inte typsnitt eller position — textextraktion ger inte tillförlitlig
formatinformation, och en parser som gissar på indrag går sönder vid nästa
uppdatering av mallen.

Det som INTE görs, med avsikt:
  * Ingen tolkning av innehållet. Parsern flyttar text, den förstår den inte.
  * Ingen sammanslagning av allmänt råd och kommentar. Det ena är bindande,
    det andra inte, och den skillnaden ska inte suddas ut i datalagret — den
    är hela skälet till att blocktypen följer med hela vägen till svaret.
  * Ingen OCR. En pdf utan textlager rapporteras som sådan i stället för att
    tystna.

Härkomst: portad från systerprojektets regelverksmodul, där mönstren är
framtagna mot K3-vägledningen (358 sidor), K2 och K1.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Blockmarkörer, ensamma på en rad.
_MARKORER = {
    "lagtext": "lagtext",
    "lagregler": "lagtext",
    "allmänt råd": "allmant_rad",
    "allmänna råd": "allmant_rad",
    "kommentar": "kommentar",
    "kommentarer": "kommentar",
    "exempel": "exempel",
}

_KAPITEL = re.compile(r"^Kapitel\s+(\d+[a-zA-Z]?)\s*[–—-]\s*(.+)$")
# Punkter i allmänna råd: "12.7", "3.1a", "5.10 b" — och i de äldre mallarna
# med avslutande punkt och tabb: "3.1. \t En tillgång skall bokföras..."
# (BFNAR 2006:1, K1 för enskilda näringsidkare). Utan den valfria punkten fick
# hela K1 noll numrerade råd.
#
# MELLANSLAGET avgör, inte skiftläget. Ett bokstavstillägg som sitter DIREKT
# mot numret är en äkta underpunkt; en bokstav efter mellanslag är textens
# första ord eller en hänvisning inne i en mening.
#
# Källan jag portade från tillät ett valfritt mellanslag före bokstaven, och åt
# då upp rådets första ord: "12.7 I anskaffningsvärdet ingår ..." blev punkten
# "12.7I" med texten "anskaffningsvärdet ingår ...". Mätt på den befintliga
# leveransens råd 2026-09-09 var 420 av 444 bokstavspunkter sådana uppslukade
# förstaord.
#
# Att i stället bara tillåta GEMENA tillägg vore lika fel åt andra hållet:
# BFN använder versala underpunkter på riktigt — 228 rader bär "1.1A", "1.1B",
# "1.1C", "2.1A", "20.9A" (BFNAR 2025:2 m.fl.). En regel på skiftläge hade
# tappat dem. De 28 raderna med bokstav EFTER mellanslag är samtliga
# hänvisningar eller textens början, inga egna punkter — kontrollerat rad för
# rad 2026-09-09.
#
# Felet syns inte i någon räkning: antalet råd blir rätt, bara texten blir fel.
_PUNKT = re.compile(r"^(\d+\.\d+[A-Za-zÅÄÖåäö]?)\.?\s+(.*)$")
# Titelsidans versionsstämpel.
_UPPDATERAD = re.compile(r"Uppdaterad\s+(\d{4}-\d{2}-\d{2})")
_BFNAR = re.compile(r"BFNAR\s+(\d{4}:\d+)")
# Innehållsförteckningens punktrader ska inte tolkas som text.
_INNEHALL_RAD = re.compile(r"\.{6,}\s*\d+\s*$")

# Blockmarkör som INLEDER en textrad i stället för att stå ensam.
# Grupp 1 är markören, grupp 2 resten av raden. Alternativen sorteras längst
# först så att "allmänna råd" inte matchas som bara "allmänt råd".
#
# Kravet att resten inleds med VERSAL eller SIFFRA är det som skiljer en
# markör från vanlig löptext. Utan det delades "Exempel på detta är ..." som
# om "Exempel" vore en blockmarkör, och meningen tappade sitt första ord —
# samma sorts fel som versalen i _PUNKT orsakade. Ett block börjar med en ny
# mening ("Kommentar Värderingen av ...") eller med sitt punktnummer
# ("Allmänt råd 3.1 Detta kapitel ..."); löptext fortsätter med gemener.
# Skiftlägesokänsligheten gäller BARA markören, via (?i:...). Med re.IGNORECASE
# på hela mönstret blev även teckenklassen nedan skiftlägesokänslig, så gemener
# släpptes igenom och "Exempel på detta är ..." delades ändå.
_INLEDANDE_MARKOR = re.compile(
    r"^(?i:(" + "|".join(sorted((re.escape(m) for m in _MARKORER), key=len, reverse=True))
    + r"))\s+(?=[A-ZÅÄÖ0-9])(.+)$"
)

# Gränsen för "misstänkt tunn parsning" — se anmärkningen i parsa().
_TUNN_MINSTA_SIDOR = 20
_TUNN_KVOT = 0.2

# "Tillämpning Kapitel 1 -" i stället för "Kapitel 1 - Tillämpning".
_OMKASTAD_KAPITELRAD = re.compile(r"Kapitel\s+\d+[a-zA-Z]?\s*[–—-]\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Block:
    """Ett textblock ur en BFN-pdf, med sin typ och sin plats."""

    typ: str                        # allmant_rad | kommentar | lagtext | exempel | brodtext
    text: str
    sida: int
    punkt: str | None = None
    kapitel_nr: str | None = None
    kapitel_rubrik: str | None = None
    avsnitt: str | None = None


@dataclass(frozen=True)
class Dokument:
    """En parsad BFN-pdf."""

    id: str
    filnamn: str
    titel: str
    typ: str
    kategori: str
    niva: str
    url: str
    sha256: str
    sidor: int
    hamtad: str
    uppdaterad: str
    bfnar: str
    kapitel: list[dict[str, Any]] = field(default_factory=list)
    block: list[Block] = field(default_factory=list)
    parsningsanmarkning: str = ""


def _rensa(rader: list[str]) -> str:
    text = "\n".join(r for r in rader).strip()
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _texten_ar_omkastad(sidor: list[str]) -> bool:
    """Har extraktorn lagt kapitelrubrikens delar i fel ordning?

    pypdf läser textrutorna i dokumentordning. I några av BFN:s mallar ligger
    kapitelnumret i en egen ruta som kommer EFTER rubriken i filen, och då
    blir raden "Tillämpning Kapitel 1 -". Parsern kan inte känna igen den, och
    hela dokumentet får noll kapitel — utan att något ser fel ut i övrigt.

    pdfplumber sorterar efter position och får ordningen rätt. Den är
    långsammare, så den används bara när det här mönstret dyker upp.
    """
    return any(_OMKASTAD_KAPITELRAD.search(s) for s in sidor)


def las_sidor(pdf_fil: Path) -> list[str]:
    """Textlager per sida. pypdf i första hand, pdfplumber som reserv.

    Valet är mätt, inte antaget: på K3-vägledningen (358 sidor) tar pypdf
    ~7 sekunder och pdfplumber ~33, och pypdf ger fler tecken. Skillnaden är
    att pdfplumber gör layoutanalys som modulen inte använder — parsern hänger
    upp sig på blockmarkörer, inte på indrag. Över hundratals dokument blir
    det skillnaden mellan minuter och timmar.

    pdfplumber ligger kvar som reserv eftersom de två biblioteken går bet på
    olika filer. Ger båda tomt är pdf:en utan textlager, och det rapporteras
    av anroparen i stället för att tystna.
    """
    try:
        import pypdf

        lasare = pypdf.PdfReader(str(pdf_fil))
        sidor = [(s.extract_text() or "") for s in lasare.pages]
        if any(t.strip() for t in sidor) and not _texten_ar_omkastad(sidor):
            return sidor
    except Exception as e:  # noqa: BLE001 — reservvägen finns just för detta
        logger.debug("pypdf klarade inte %s (%s), provar pdfplumber.", pdf_fil.name, e)

    import pdfplumber

    with pdfplumber.open(pdf_fil) as pdf:
        return [(s.extract_text() or "") for s in pdf.pages]


def parsa(
    pdf_fil: Path,
    *,
    dok_id: str,
    titel: str,
    typ: str,
    kategori: str,
    niva: str,
    url: str,
    sha256: str,
    sidor: list[str] | None = None,
) -> Dokument:
    """Läser en BFN-pdf och returnerar den som strukturerat dokument.

    `sidor` finns för proven: den låter en textlista skickas in direkt, så att
    parsningsreglerna kan prövas utan en pdf på disk.
    """
    sidtexter = sidor if sidor is not None else las_sidor(pdf_fil)

    m_upp = _UPPDATERAD.search("\n".join(sidtexter[:3]))
    m_bfnar = _BFNAR.search("\n".join(sidtexter[:4]))

    block: list[Block] = []
    kapitel: list[dict[str, Any]] = []

    kap_nr: str | None = None
    kap_rubrik: str | None = None
    avsnitt: str | None = None

    aktuell_typ = "brodtext"
    aktuell_punkt: str | None = None
    buffert: list[str] = []
    buffert_sida = 1
    # Kapitelrubriken upprepas som sidhuvud på varje sida; bara första
    # förekomsten ska öppna ett kapitel.
    sedda_kapitel: set[str] = set()

    def spola() -> None:
        nonlocal buffert, aktuell_punkt
        text = _rensa(buffert)
        buffert = []
        if not text:
            aktuell_punkt = None
            return
        block.append(Block(
            typ=aktuell_typ, text=text, sida=buffert_sida, punkt=aktuell_punkt,
            kapitel_nr=kap_nr, kapitel_rubrik=kap_rubrik, avsnitt=avsnitt,
        ))
        aktuell_punkt = None

    # Platt radlista med sidnummer, så att nästa rad går att titta på.
    # Framförhållningen behövs på två ställen: avsnittsrubrikerna och
    # innehållsförteckningen. Punktraderna ("...... 88") ligger kvar i listan
    # just därför — de är det enda som skiljer en innehållsförteckningsrad
    # från en riktig rubrik när posten radbrutits:
    #
    #     Kapitel 10 - Byte av redovisningsprincip, ändrad uppskattning
    #     och bedömning samt rättelse ................................. 88
    #
    # Första raden ser ut precis som den riktiga kapitelrubriken på sidan 88.
    # Utan lookahead tog innehållsförteckningens post platsen i sedda_kapitel,
    # och det riktiga kapitel 10 sorterades bort som en upprepning — varvid
    # hela kapitlet hamnade under kapitel 9.
    rader: list[tuple[int, str]] = []
    for sidnr, sidtext in enumerate(sidtexter, 1):
        for r in sidtext.split("\n"):
            r = r.strip()
            if not r:
                continue
            # I de flesta av BFN:s mallar står blockmarkören ensam på raden.
            # I några — kontrollerat på vl20-5-redovisning-av-fusion
            # 2026-09-09 — inleder den i stället textens första rad:
            #
            #     Kommentar Värderingen av övertagna tillgångar ...
            #     Allmänt råd 3.1 Detta kapitel ska tillämpas ...
            #
            # Utan uppdelningen nedan såg parsern ingen enda markör i det
            # dokumentet: 113 sidor gav 6 block, trots att texten innehöll
            # 39 "Allmänt råd" och 31 "Kommentar". Raden delas i två — markören
            # för sig, resten för sig — så att den vanliga logiken tar vid, och
            # ett numrerat råd på samma rad fortfarande hittas av _PUNKT.
            delad = _INLEDANDE_MARKOR.match(r)
            if delad:
                rader.append((sidnr, delad.group(1)))
                rader.append((sidnr, delad.group(2).strip()))
                continue
            rader.append((sidnr, r))

    for idx, (sidnr, r) in enumerate(rader):
        if _INNEHALL_RAD.search(r):
            continue
        nasta = rader[idx + 1][1] if idx + 1 < len(rader) else ""

        m_kap = _KAPITEL.match(r)
        if m_kap and not _INNEHALL_RAD.search(nasta):
            nr = m_kap.group(1)
            rubrik = m_kap.group(2).strip()
            if nr in sedda_kapitel:
                # Sidhuvud, inte en ny kapitelstart. Rubriken kan vara
                # avhuggen i sidhuvudet — behåll den längsta lydelsen.
                if kap_nr == nr and len(rubrik) > len(kap_rubrik or ""):
                    kap_rubrik = rubrik
                    for k in kapitel:
                        if k["nr"] == nr:
                            k["rubrik"] = rubrik
                continue
            sedda_kapitel.add(nr)
            spola()
            kap_nr = nr
            kap_rubrik = rubrik
            avsnitt = None
            aktuell_typ = "brodtext"
            buffert_sida = sidnr
            kapitel.append({"nr": kap_nr, "rubrik": kap_rubrik, "forsta_sida": sidnr})
            continue

        markor = _MARKORER.get(r.lower().rstrip(":"))
        if markor:
            spola()
            aktuell_typ = markor
            buffert_sida = sidnr
            continue

        # Ett numrerat allmänt råd öppnar ett eget block.
        #
        # I VÄGLEDNINGARNA står punkten efter markören "Allmänt råd", en punkt
        # per markör. I de fristående ALLMÄNNA RÅDEN (bfnar*.pdf) finns inga
        # markörer alls — hela dokumentet är rådet, och punkterna står bara på
        # rad. Utan den här grenen blev ett helt BFNAR ett enda brödtextblock.
        #
        # Villkoret att punktens första tal ska vara det aktuella kapitlet
        # skiljer en punkt från en HÄNVISNING till en punkt. En kommentar i
        # kapitel 7 kan radbryta så att "7.9 a är avskrivningar..." hamnar
        # först på raden — den ska inte bli ett nytt råd. Därför prövas grenen
        # bara i brödtext- och rådläge, aldrig mitt i en kommentar.
        m_p = _PUNKT.match(r)
        if m_p and aktuell_typ in ("brodtext", "allmant_rad"):
            punktnr = re.sub(r"\s+", "", m_p.group(1))
            efter_markor = aktuell_typ == "allmant_rad" and not buffert
            matchar_kapitel = (
                kap_nr is None or punktnr.split(".")[0] == kap_nr.replace(" ", "")
            )
            if efter_markor or matchar_kapitel:
                spola()
                aktuell_typ = "allmant_rad"
                aktuell_punkt = punktnr
                buffert.append(m_p.group(2).strip())
                buffert_sida = sidnr
                continue

        # Avsnittsrubrik. Kännetecknet är inte formen på raden själv — pdf-text
        # radbryts mitt i meningar, och en avhuggen rad ser ut precis som en
        # rubrik. Kännetecknet är att NÄSTA rad är en blockmarkör. I BFN:s
        # mall följs varje avsnittsrubrik omedelbart av Lagtext, Allmänt råd
        # eller Kommentar.
        if (
            len(r) < 90
            and r[0].isupper()
            and not r.endswith((".", ":", ";", ","))
            and _MARKORER.get(nasta.lower().rstrip(":")) is not None
            and not _PUNKT.match(r)
        ):
            spola()
            avsnitt = r
            continue

        if not buffert:
            buffert_sida = sidnr
        buffert.append(r)

    spola()

    anmarkning = ""
    if not any(s.strip() for s in sidtexter):
        anmarkning = (
            "Pdf:en saknar textlager (troligen inskannad). Ingen text kunde "
            "utvinnas. Modulen gör INTE OCR — filen finns nedladdad och kan "
            "läsas av en människa."
        )
    elif not block:
        anmarkning = "Textlager fanns men inga block kunde identifieras."
    elif len(sidtexter) >= _TUNN_MINSTA_SIDOR and len(block) < len(sidtexter) * _TUNN_KVOT:
        # Halvt lyckad parsning är farligare än en misslyckad: dokumentet ser
        # indexerat ut, men nästan allt innehåll saknas. Upptäckt 2026-09-09 på
        # vl17-3-ab-kons2024, som gav 4 block ur 304 sidor och ändå
        # rapporterades som "ok".
        #
        # Gränsen är mätt, inte gissad: på BFN:s 132 hämtade dokument ligger de
        # trasiga på 0,01-0,17 block per sida medan de riktiga ligger på 0,5
        # och uppåt. Den flaggar, den utesluter inte — ett tunt dokument kan
        # vara äkta, och det är en människa som ska avgöra vilket.
        anmarkning = (
            f"Ovanligt få block ({len(block)}) för {len(sidtexter)} sidor "
            f"({len(block) / len(sidtexter):.2f} per sida). Parsningen kan ha "
            f"missat dokumentets struktur — kontrollera mot pdf:en."
        )

    return Dokument(
        id=dok_id,
        filnamn=pdf_fil.name,
        titel=titel,
        typ=typ,
        kategori=kategori,
        niva=niva,
        url=url,
        sha256=sha256,
        sidor=len(sidtexter),
        hamtad=datetime.now(UTC).isoformat(timespec="seconds"),
        uppdaterad=m_upp.group(1) if m_upp else "",
        bfnar=f"BFNAR {m_bfnar.group(1)}" if m_bfnar else "",
        kapitel=kapitel,
        block=block,
        parsningsanmarkning=anmarkning,
    )
