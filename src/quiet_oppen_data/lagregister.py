"""Lagregister — läser lagar/lagregister.yaml till typade objekt.

Invarianter:
    * Kastar ValueError om en post saknar fältet 'sfs'.
    * Laglistan hårdkodas inte i Python-kod — allt läses ur lagregister.yaml.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# Se motsvarande kommentar i register.py — samma QUIET_OPPEN_DATA_ROOT-
# mekanism, av samma skäl (paketet installerat i ett annat projekt).
_PROJEKT_ROT = Path(os.environ.get("QUIET_OPPEN_DATA_ROOT") or Path(__file__).parent.parent.parent)
_LAGREGISTER_FIL = _PROJEKT_ROT / "lagar" / "lagregister.yaml"


@dataclass(frozen=True)
class LagPost:
    """En författning i lagregistret."""

    sfs: str
    namn: str
    kortnamn: str
    beskrivning: str = ""

    @property
    def dok_id(self) -> str:
        """Härleder Riksdagens dok_id från SFS-numret (t.ex. 1999:1229 -> sfs-1999-1229)."""
        return f"sfs-{self.sfs.replace(':', '-')}"

    @property
    def lank_manniska(self) -> str:
        """Klickbar länk hos Riksdagen."""
        return f"https://www.riksdagen.se/sv/dokument-och-lagar/dokument/_{self.dok_id}/"

    @property
    def lank_maskin(self) -> str:
        """Exakt API-anrop/resurs hos Riksdagen."""
        return f"https://data.riksdagen.se/dokument/{self.dok_id}"


def las(registersokväg: Path | str | None = None) -> list[LagPost]:
    """Läser lagregistret och returnerar en lista med LagPost-objekt.

    Args:
        registersokväg: Sökväg till YAML-filen. Standard: lagar/lagregister.yaml.

    Returns:
        Lista med LagPost-objekt.

    Raises:
        ValueError: om en post saknar 'sfs', 'namn' eller 'kortnamn'.
        FileNotFoundError: om filen saknas.
    """
    sökväg = Path(registersokväg) if registersokväg else _LAGREGISTER_FIL

    with open(sökväg, encoding="utf-8") as f:
        dokument = yaml.safe_load(f)

    if dokument is None:
        radata: list[dict[str, Any]] = []
    elif isinstance(dokument, dict):
        radata = dokument.get("lagar") or []
    elif isinstance(dokument, list):
        radata = dokument
    else:
        raise ValueError(f"Lagregistret ska vara en YAML-mappning med nyckeln 'lagar', fick: {type(dokument)}")

    resultat: list[LagPost] = []
    for i, post in enumerate(radata):
        if not isinstance(post, dict):
            raise ValueError(f"Post {i} är inte ett YAML-objekt: {post!r}")
        if "sfs" not in post:
            raise ValueError(f"Post {i} saknar obligatoriskt fält 'sfs': {post!r}")
        if "namn" not in post:
            raise ValueError(f"Post {i} saknar obligatoriskt fält 'namn': {post!r}")
        if "kortnamn" not in post:
            raise ValueError(f"Post {i} saknar obligatoriskt fält 'kortnamn': {post!r}")

        resultat.append(
            LagPost(
                sfs=str(post["sfs"]).strip(),
                namn=str(post["namn"]).strip(),
                kortnamn=str(post["kortnamn"]).strip(),
                beskrivning=str(post.get("beskrivning", "")).strip(),
            )
        )

    return resultat


def hamta(sfs_eller_kortnamn: str, registersokväg: Path | str | None = None) -> LagPost | None:
    """Hämtar en lag på SFS-nummer, dok_id eller kortnamn."""
    sökterm = sfs_eller_kortnamn.strip().lower()
    for post in las(registersokväg):
        if (
            post.sfs.lower() == sökterm
            or post.kortnamn.lower() == sökterm
            or post.dok_id.lower() == sökterm
            or post.namn.lower() == sökterm
        ):
            return post
    return None
