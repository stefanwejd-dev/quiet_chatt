"""Parser för EUR-Lex XHTML (steg 24).

EUR-Lex levererar två markupvarianter för samma rättsakt, och de har olika
klassnamn:

  * Officiella tidningens publikation (CELEX 3xxxx) — klasser `oj-ti-art`,
    `oj-sti-art`, `oj-normal`.
  * Den konsoliderade lydelsen (CELEX 0xxxx-ÅÅÅÅMMDD) — klasser
    `title-article-norm`, `stitle-article-norm`, `norm`.

Parsern hänger därför upp sig på **ELI-identifierarna**, inte på klassnamnen:
`<div class="eli-subdivision" id="art_9">`, `<div id="cpt_3">`,
`<div class="eli-title" id="art_9.tit_1">`. De är desamma i båda varianterna
och är källans egna stabila beteckningar — samma val som i BFN-parsern, där
blockmarkörerna bär strukturen och typsnittet inte gör det.

Artikelnumreringen får bära bokstavstillägg (`art_9a`, `art_29b`). EU-rätten
skjuter in nya artiklar i stället för att numrera om resten, precis som svensk
lagstiftningsteknik gör med kapitel.

Det som INTE görs, med avsikt:
  * Ingen tolkning. Parsern flyttar text.
  * Ingen sammanslagning av artikel och skäl. Skälen är tolkningsdata, inte
    bindande bestämmelser, och blocktypen håller isär dem hela vägen ut i
    svaret.
"""
from __future__ import annotations

import html as htmlmod
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ELI-ankare. `id` är källans egen beteckning och överlever mallbytet.
_ARTIKEL = re.compile(r'<div\b[^>]*class="[^"]*\beli-subdivision\b[^"]*"[^>]*id="(art_[^"]+)"', re.I)
# Indelningar. EUR-Lex använder fyra nivåer med egna ELI-prefix:
#   tis_ avdelning ("AVDELNING III"), cpt_ kapitel, sct_ avsnitt, sbs_ underavsnitt.
# Momsdirektivet numrerar dem hierarkiskt: "tis_XI.cpt_1.sct_2".
#
# Suffixet ".tit_N" är däremot INTE en indelning utan behållaren för en
# indelnings rubrik. Utan undantaget nedan behandlades "cpt_3.tit_1" som en
# egen avdelning som började EFTER kapitlet — varvid varje artikel fick
# rubriken men tappade numret. Kontrollerat mot 32013L0034 2026-09-09.
_DIVISION = re.compile(r'<div\b[^>]*id="((?:tis|cpt|sct|sbs)_[^"]+)"', re.I)
_ARDIVISION_TITEL = re.compile(r"\.tit_\d+$", re.I)
_SKAL = re.compile(r'<div\b[^>]*id="(rct_[^"]+)"', re.I)
_BILAGA = re.compile(r'<div\b[^>]*id="(anx_[^"]+)"', re.I)
_ELI_TITEL = re.compile(
    r'<div\b[^>]*class="[^"]*\beli-title\b[^"]*"[^>]*>(.*?)</div>', re.I | re.S
)
# Rubrikstycket öppnar varje artikel i båda mallarna: oj-ti-art respektive
# title-article-norm. Texten ("Artikel 9") är densamma.
_FORSTA_STYCKE = re.compile(r"<p\b[^>]*>(.*?)</p>", re.I | re.S)

# --- Tredje mallen: platt markup utan ELI-avdelningar ---------------------
#
# De äldre konsolideringarna (kontrollerat på 02016L1164-20220101,
# 02011L0096-20150217, 02009L0133-20130701, 02003L0049-20130701 den
# 2026-09-09) saknar `eli-subdivision` helt. Artiklarna markeras bara med
# ett styckes KLASS, och id:na är slumpade UUID:n utan struktur:
#
#     <p class="title-division-1">KAPITEL II</p>
#     <p class="title-division-2">ÅTGÄRDER MOT SKATTEFLYKT</p>
#     <p class="title-article-norm">Artikel 4</p>
#     <p class="stitle-article-norm">Räntebegränsningsregel</p>
#     <div class="norm">...</div>
#
# Reservvägen används BARA när ELI-ankarna saknas. Klassnamn är en svagare
# grund än ELI-id:n — de hör till presentationen och kan bytas — så de får
# aldrig gå före den stabila vägen.
_KLASS_ARTIKEL = re.compile(
    r'<p\b[^>]*class="[^"]*\btitle-article-norm\b[^"]*"[^>]*>(.*?)</p>', re.I | re.S
)
_KLASS_UNDERRUBRIK = re.compile(
    r'<p\b[^>]*class="[^"]*\bstitle-article-norm\b[^"]*"[^>]*>(.*?)</p>', re.I | re.S
)
_KLASS_DIVISION = re.compile(
    r'<p\b[^>]*class="[^"]*\btitle-division-1\b[^"]*"[^>]*>(.*?)</p>', re.I | re.S
)
_KLASS_DIVISION_RUBRIK = re.compile(
    r'<p\b[^>]*class="[^"]*\btitle-division-2\b[^"]*"[^>]*>(.*?)</p>', re.I | re.S
)
_TAGG = re.compile(r"<[^>]+>")
_KONSOLIDERAD_TITEL = re.compile(
    r"<title>\s*Konsoliderad\s+TEXT:\s*(\S+)\s*[—–-]\s*(\w+)\s*[—–-]\s*(\d{2}\.\d{2}\.\d{4})",
    re.I,
)


@dataclass(frozen=True)
class EuChunk:
    """Ett stycke ur en rättsakt."""

    beteckning: str          # "Artikel 9", "Skäl 12", "Bilaga III"
    blocktyp: str            # artikel | skal | bilaga
    text: str
    kapitel_nr: str | None = None
    kapitel_rubrik: str | None = None
    avsnitt: str | None = None   # artikelns egen rubrik


@dataclass(frozen=True)
class ParsadRattsakt:
    """En parsad rättsakt."""

    celex: str
    konsolideringsdatum: str = ""
    chunkar: list[EuChunk] = field(default_factory=list)
    parsningsanmarkning: str = ""


def _text(fragment: str) -> str:
    """Taggfri text med styckeindelningen bevarad."""
    # Blockslut blir radbrytning så att stycken inte klistras ihop till en
    # enda mening. Görs före taggborttagningen, annars finns inget att gå på.
    t = re.sub(r"</(p|div|li|tr|h\d)\s*>", "\n", fragment, flags=re.I)
    t = re.sub(r"<br\s*/?>", "\n", t, flags=re.I)
    t = _TAGG.sub(" ", t)
    t = htmlmod.unescape(t).replace("\xa0", " ")
    # Radvis städning, sedan hopslagning: bevarar tomrad mellan stycken men
    # tar bort den ojämna indenteringen ur källans XML.
    rader = [re.sub(r"[ \t]+", " ", r).strip() for r in t.split("\n")]
    rader = [r for r in rader if r]
    return "\n".join(rader).strip()


_INDELNINGSORD = r"(?:AVDELNING|KAPITEL|AVSNITT|UNDERAVSNITT|DEL)"


def _indelningsrubrik(fragment: str) -> tuple[str | None, str | None]:
    """(nummer, rubrik) ur en indelnings huvud.

    Huvudet står som "KAPITEL 3" eller "AVDELNING III" på egen rad, följt av
    rubriken i ett eget stycke. Numret plockas ur texten och inte ur id:t,
    eftersom id:t är en intern beteckning medan texten är den läsaren känner
    igen — och den som citeras.
    """
    txt = _text(fragment)
    if not txt:
        return None, None
    rader = txt.split("\n")
    m = re.match(rf"^{_INDELNINGSORD}\s+([0-9IVXLC]+[a-zA-Z]?)\s*$", rader[0], re.I)
    if m:
        rubrik = rader[1].strip() if len(rader) > 1 else None
        return f"{rader[0].split()[0].title()} {m.group(1)}", rubrik
    m2 = re.match(
        rf"^{_INDELNINGSORD}\s+([0-9IVXLC]+[a-zA-Z]?)\s*[—–-]?\s+(.+)$", rader[0], re.I
    )
    if m2:
        return f"{rader[0].split()[0].title()} {m2.group(1)}", m2.group(2).strip()
    return None, rader[0][:120]


def _snitt(text: str, start: int, granser: list[int]) -> str:
    """Texten från `start` fram till närmast följande gräns."""
    slut = len(text)
    for g in granser:
        if g > start:
            slut = min(slut, g)
    return text[start:slut]


def _parsa_platt(kropp: str) -> list[EuChunk]:
    """Artiklar ur den platta mallen, där bara styckets klass bär strukturen.

    Se kommentaren vid _KLASS_ARTIKEL. Kapitelrubriken byggs av paret
    title-division-1 ("KAPITEL II") och title-division-2 (rubriken), som står
    som syskon till artiklarna i stället för att omsluta dem — därför räcker
    det att hålla reda på den senast passerade.
    """
    artiklar = [(m.start(), _text(m.group(1))) for m in _KLASS_ARTIKEL.finditer(kropp)]
    if not artiklar:
        return []

    divisioner = [(m.start(), _text(m.group(1))) for m in _KLASS_DIVISION.finditer(kropp)]
    divisionsrubriker = [
        (m.start(), _text(m.group(1))) for m in _KLASS_DIVISION_RUBRIK.finditer(kropp)
    ]

    granser = sorted(p for p, _ in artiklar + divisioner)

    def senaste_fore(poster: list[tuple[int, str]], pos: int) -> str | None:
        traff = None
        for p, v in poster:
            if p < pos:
                traff = v
            else:
                break
        return traff

    chunkar: list[EuChunk] = []
    for pos, beteckning in artiklar:
        block = _snitt(kropp, pos, granser)
        # Artikelns egen rubrik står direkt efter beteckningen, inom blocket.
        m_rub = _KLASS_UNDERRUBRIK.search(block)
        rubrik = _text(m_rub.group(1)) if m_rub else None
        rest = block
        # Ta bort beteckningsstycket och rubrikstycket ur brödtexten.
        m_bet = _KLASS_ARTIKEL.search(rest)
        if m_bet:
            rest = rest[: m_bet.start()] + rest[m_bet.end() :]
        if m_rub:
            m_rub2 = _KLASS_UNDERRUBRIK.search(rest)
            if m_rub2:
                rest = rest[: m_rub2.start()] + rest[m_rub2.end() :]

        text = _text(rest)
        if not text:
            continue

        kap_txt = senaste_fore(divisioner, pos)
        kap_nr = None
        if kap_txt:
            m = re.match(rf"^{_INDELNINGSORD}\s+([0-9IVXLC]+[a-zA-Z]?)\s*$", kap_txt, re.I)
            kap_nr = f"{kap_txt.split()[0].title()} {m.group(1)}" if m else kap_txt
        chunkar.append(EuChunk(
            beteckning=beteckning or "Artikel",
            blocktyp="artikel",
            text=text,
            kapitel_nr=kap_nr,
            kapitel_rubrik=senaste_fore(divisionsrubriker, pos),
            avsnitt=rubrik,
        ))

    return chunkar


def parsa(xhtml: str, celex: str) -> ParsadRattsakt:
    """Läser en rättsakt i EUR-Lex XHTML och returnerar dess stycken."""
    kropp_start = xhtml.find("<body")
    kropp = xhtml[kropp_start:] if kropp_start >= 0 else xhtml

    m_kons = _KONSOLIDERAD_TITEL.search(xhtml)
    konsolideringsdatum = ""
    if m_kons:
        d, mn, y = m_kons.group(3).split(".")
        konsolideringsdatum = f"{y}-{mn}-{d}"

    artiklar = [(m.start(), m.group(1)) for m in _ARTIKEL.finditer(kropp)]
    indelningar = [
        (m.start(), m.group(1))
        for m in _DIVISION.finditer(kropp)
        if not _ARDIVISION_TITEL.search(m.group(1))
    ]
    skal = [(m.start(), m.group(1)) for m in _SKAL.finditer(kropp)]
    bilagor = [(m.start(), m.group(1)) for m in _BILAGA.finditer(kropp)]

    # Alla positioner som avslutar ett stycke.
    granser = sorted(p for p, _ in artiklar + indelningar + skal + bilagor)

    # Indelningens rubrik läses ur avsnittet mellan dess början och nästa
    # gräns — där och bara där står den.
    indelningsinfo: list[tuple[int, str, str | None, str | None]] = []
    rubrik_per_id: dict[str, str] = {}
    for pos, iid in indelningar:
        nr, rubrik = _indelningsrubrik(_snitt(kropp, pos, granser))
        indelningsinfo.append((pos, iid, nr, rubrik))
        if rubrik:
            rubrik_per_id[iid] = rubrik

    def _med_foraldrar(iid: str, rubrik: str | None) -> str | None:
        """Rubriken med sina överordnade indelningar framför sig.

        Momsdirektivet numrerar hierarkiskt ("tis_XI.cpt_1.sct_2"). Utan
        förälderledet blir sökytan för avsnitt 2 bara "Avdrag" — utan att
        det framgår att det är avdelning XI som avses.
        """
        delar = iid.split(".")
        led = [rubrik_per_id[".".join(delar[: i + 1])]
               for i in range(len(delar) - 1)
               if ".".join(delar[: i + 1]) in rubrik_per_id]
        if rubrik:
            led.append(rubrik)
        return " — ".join(led) if led else None

    def kapitel_for(pos: int) -> tuple[str | None, str | None]:
        traff: tuple[str | None, str | None] = (None, None)
        for ipos, iid, nr, rubrik in indelningsinfo:
            if ipos < pos:
                traff = (nr, _med_foraldrar(iid, rubrik))
            else:
                break
        return traff

    chunkar: list[EuChunk] = []

    for pos, aid in artiklar:
        block = _snitt(kropp, pos, granser)

        # Artikelns egen rubrik ligger i <div class="eli-title" id="art_N.tit_1">.
        m_titel = _ELI_TITEL.search(block)
        rubrik = _text(m_titel.group(1)) if m_titel else None
        if m_titel:
            block_utan_titel = block[: m_titel.start()] + block[m_titel.end() :]
        else:
            block_utan_titel = block

        # Första stycket är artikelbeteckningen ("Artikel 9").
        m_forsta = _FORSTA_STYCKE.search(block_utan_titel)
        beteckning = _text(m_forsta.group(1)) if m_forsta else ""
        if m_forsta:
            brodtext = block_utan_titel[m_forsta.end() :]
        else:
            brodtext = block_utan_titel
        if not re.match(r"^Artikel\b", beteckning, re.I):
            # Mallen såg annorlunda ut än väntat. Härled ur ELI-id:t hellre än
            # att låta stycket bli obeteckningsbart — men behåll det som
            # hittades som del av texten.
            brodtext = block_utan_titel
            beteckning = "Artikel " + aid[len("art_") :]

        text = _text(brodtext)
        if not text:
            continue
        kap_nr, kap_rubrik = kapitel_for(pos)
        chunkar.append(EuChunk(
            beteckning=beteckning, blocktyp="artikel", text=text,
            kapitel_nr=kap_nr, kapitel_rubrik=kap_rubrik, avsnitt=rubrik,
        ))

    for pos, sid in skal:
        text = _text(_snitt(kropp, pos, granser))
        if not text:
            continue
        chunkar.append(EuChunk(
            beteckning=f"Skäl {sid[len('rct_'):]}", blocktyp="skal", text=text,
        ))

    for pos, bid in bilagor:
        text = _text(_snitt(kropp, pos, granser))
        if not text:
            continue
        chunkar.append(EuChunk(
            beteckning=f"Bilaga {bid[len('anx_'):]}", blocktyp="bilaga", text=text,
        ))

    # Reservväg: platt markup utan ELI-avdelningar. Prövas först när den
    # stabila vägen gett noll artiklar, aldrig i stället för den.
    if not artiklar:
        chunkar = _parsa_platt(kropp) + chunkar

    anmarkning = ""
    if not chunkar:
        anmarkning = (
            "Varken ELI-avdelningar (eli-subdivision / art_*) eller "
            "artikelrubriker (class=title-article-norm) hittades i svaret. "
            "Mallen kan ha ändrats — kontrollera mot källan innan dokumentet "
            "räknas som indexerat."
        )

    return ParsadRattsakt(
        celex=celex,
        konsolideringsdatum=konsolideringsdatum,
        chunkar=chunkar,
        parsningsanmarkning=anmarkning,
    )
