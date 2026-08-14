"""Lagparser för konsoliderad svensk lagtext från Riksdagens öppna data.

Hanterar parsning av:
- Dokumenthuvud och konsolideringspunkt (t.o.m. SFS)
- Kapitelrubriker (inklusive numrering med bokstäver: 6 a kap., 39 a kap. samt flerradiga rubriker)
- Paragrafrubriker / mellanrubriker
- Paragraftexter (inklusive punktlistor, stycken och numrering med bokstäver: 1 a §)
- Ändringsmarkeringar (Lag (ÅÅÅÅ:NNN) / SFS ÅÅÅÅ:NNN)
- Avskiljande av övergångsbestämmelser
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ParsadChunk:
    """En strukturerad lagparagraf (chunk) redo för indexering."""

    chunk_id: str
    sfs: str
    dok_id: str
    kapitel_nr: str | None
    kapitel_rubrik: str | None
    paragraf_nr: str
    paragraf_rubrik: str | None
    paragraf_text: str
    andringsnotis: str | None
    full_text: str


def extrahera_tom_sfs(text: str) -> str:
    """Extraherar konsolideringspunkten ur dokumenthuvudet.

    Exempel:
        "Inkomstskattelag (1999:1229) t.o.m. SFS 2026:1393"
        -> "t.o.m. SFS 2026:1393"
    """
    m = re.search(r"t\.o\.m\.\s+SFS\s+[\d:]+", text, re.IGNORECASE)
    if m:
        return m.group(0).strip()

    m2 = re.search(r"Ändrad:\s*(.*)", text)
    if m2:
        val = m2.group(1).strip()
        if val:
            return val

    return "ursprunglig lydelse"


def _normalisera_nummer(nr_str: str) -> str:
    """Normaliserar kapitel- och paragrafnummer, t.ex. '6 a' -> '6 a'."""
    return re.sub(r"\s+", " ", nr_str.strip())


def parsa_lagtext(text: str, sfs: str, namn: str, kortnamn: str) -> list[ParsadChunk]:
    """Parsar råtext från Riksdagen till en lista med ParsadChunk-objekt.

    Args:
        text: Råtexten (konsoliderad text från Riksdagen).
        sfs: SFS-nummer, t.ex. "1999:1229".
        namn: Lagens officiella namn, t.ex. "Inkomstskattelag".
        kortnamn: Lagens vedertagna kortnamn, t.ex. "IL".

    Returns:
        Lista med ParsadChunk-objekt med unika chunk_id:n.
    """
    dok_id = f"sfs-{sfs.replace(':', '-')}"
    lines = text.splitlines()

    # Hitta var själva lagtexten börjar (efter huvudet)
    start_idx = 0
    for i, line in enumerate(lines):
        line_s = line.strip()
        if re.match(r"^(?:AVD\.|\d+(?:\s*[a-z])?\s+kap\.|\d+(?:\s*[a-z])?\s+§)", line_s):
            start_idx = i
            break

    chunks: list[ParsadChunk] = []
    nuvarande_kap_nr: str | None = None
    nuvarande_kap_rubrik: str | None = None
    senaste_rubrik: list[str] = []

    nuvarande_par_nr: str | None = None
    nuvarande_par_rubrik: str | None = None
    nuvarande_par_rader: list[str] = []
    used_chunk_ids: dict[str, int] = {}

    def spara_paragraf() -> None:
        nonlocal nuvarande_par_nr, nuvarande_par_rubrik, nuvarande_par_rader
        nonlocal nuvarande_kap_nr, nuvarande_kap_rubrik
        if not nuvarande_par_nr or not nuvarande_par_rader:
            return

        # Sök efter rubrik som eventuellt hamnat i slutet av nuvarande_par_rader
        par_text_rader: list[str] = []
        trailing_rubrik: list[str] = []

        stycken: list[list[str]] = []
        aktuellt_stycke: list[str] = []
        for r in nuvarande_par_rader:
            if not r.strip():
                if aktuellt_stycke:
                    stycken.append(aktuellt_stycke)
                    aktuellt_stycke = []
            else:
                aktuellt_stycke.append(r.strip())
        if aktuellt_stycke:
            stycken.append(aktuellt_stycke)

        if len(stycken) > 1:
            sista = stycken[-1]
            sista_str = " ".join(sista)
            if (
                len(sista_str) < 100
                and not re.search(r"[.:;,]$", sista_str)
                and not re.match(r"^(?:Lag|SFS)\s*\(", sista_str)
                and not re.match(r"^\d+\.", sista_str)
                and not re.match(r"^/\s*Upphör", sista_str)
                and not re.match(r"^/\s*Träder", sista_str)
            ):
                trailing_rubrik = sista
                stycken = stycken[:-1]

        for st in stycken:
            par_text_rader.append(" ".join(st))

        par_text = "\n\n".join(par_text_rader).strip()

        # Extrahera eventuell ändringsnotis i slutet
        andring = None
        m_andring = re.search(r"(Lag\s+\(\d{4}:\d+\)|SFS\s+\d{4}:\d+)\.?\s*$", par_text)
        if m_andring:
            andring = m_andring.group(1).rstrip(".")

        kap_tag = nuvarande_kap_nr.replace(" ", "") if nuvarande_kap_nr else "0"
        par_tag = nuvarande_par_nr.replace(" ", "")
        bas_chunk_id = f"{sfs}#{kap_tag}:{par_tag}"

        # Säkerställ unikt chunk_id
        if bas_chunk_id in used_chunk_ids:
            used_chunk_ids[bas_chunk_id] += 1
            chunk_id = f"{bas_chunk_id}_{used_chunk_ids[bas_chunk_id]}"
        else:
            used_chunk_ids[bas_chunk_id] = 1
            chunk_id = bas_chunk_id

        full_parts = [f"{namn} ({sfs}, {kortnamn})"]
        if nuvarande_kap_nr:
            kr = nuvarande_kap_rubrik or ""
            full_parts.append(f"{nuvarande_kap_nr} kap. {kr}".strip())
        if nuvarande_par_rubrik:
            full_parts.append(nuvarande_par_rubrik)
        full_parts.append(f"{nuvarande_par_nr} § {par_text}")

        chunks.append(
            ParsadChunk(
                chunk_id=chunk_id,
                sfs=sfs,
                dok_id=dok_id,
                kapitel_nr=nuvarande_kap_nr,
                kapitel_rubrik=nuvarande_kap_rubrik,
                paragraf_nr=nuvarande_par_nr,
                paragraf_rubrik=nuvarande_par_rubrik,
                paragraf_text=par_text,
                andringsnotis=andring,
                full_text="\n".join(full_parts),
            )
        )

        nuvarande_par_nr = None
        nuvarande_par_rubrik = None
        nuvarande_par_rader = []
        if trailing_rubrik:
            senaste_rubrik.extend(trailing_rubrik)

    i = start_idx
    while i < len(lines):
        line = lines[i]
        line_s = line.strip()

        # Kolla om övergångsbestämmelser börjar
        if re.match(r"^(?:Övergångsbestämmelser|/\s*Träder i kraft)", line_s, re.IGNORECASE):
            spara_paragraf()
            break

        # Kolla kapitelrubrik (t.ex. 1 kap., 6 a kap., 39 a kap.)
        m_kap = re.match(r"^(\d+(?:\s*[a-z])?)\s+kap\.(\s*[-–—]?\s*(.*))?$", line_s, re.IGNORECASE)
        if m_kap and (i == 0 or not lines[i - 1].strip() or re.match(r"^AVD\.", lines[i - 1].strip())):
            kap_rest = (m_kap.group(2) or "").strip(" -–—")
            if (
                not kap_rest
                and i + 1 < len(lines)
                and lines[i + 1].strip()
                and not re.match(r"^\d+", lines[i + 1].strip())
            ):
                i += 1
                kap_rest = lines[i].strip()

            while (
                i + 1 < len(lines)
                and lines[i + 1].strip()
                and not re.match(r"^(?:AVD\.|\d+(?:\s*[a-z])?\s*kap\.|\d+(?:\s*[a-z])?\s*§)", lines[i + 1].strip())
                and not lines[i].strip().endswith(".")
            ):
                if len(lines[i + 1].strip()) < 90 and not lines[i + 1].strip().endswith(":"):
                    i += 1
                    kap_rest += " " + lines[i].strip()
                else:
                    break

            spara_paragraf()
            nuvarande_kap_nr = _normalisera_nummer(m_kap.group(1))
            nuvarande_kap_rubrik = kap_rest or None
            senaste_rubrik = []
            i += 1
            continue

        # Kolla paragrafstart (t.ex. 1 §, 1 a §, 12 b §)
        m_par = re.match(r"^(\d+(?:\s*[a-z])?)\s+§\s*(.*)$", line_s, re.IGNORECASE)
        if m_par and (i == 0 or not lines[i - 1].strip() or senaste_rubrik):
            spara_paragraf()
            nuvarande_par_nr = _normalisera_nummer(m_par.group(1))
            nuvarande_par_rubrik = " ".join(senaste_rubrik).strip() if senaste_rubrik else None
            senaste_rubrik = []
            rest_text = m_par.group(2).strip()
            if rest_text:
                nuvarande_par_rader = [rest_text]
            else:
                nuvarande_par_rader = []
            i += 1
            continue

        if nuvarande_par_nr is not None:
            nuvarande_par_rader.append(line_s)
        else:
            if line_s and not re.match(r"^AVD\.", line_s):
                senaste_rubrik.append(line_s)
        i += 1

    spara_paragraf()
    return chunks
