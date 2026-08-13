"""Källregister — läser kallor/kallregister.yaml till typade objekt.

Tre typer returneras:
    Kalla        — anropbar datakälla (verifierad eller ej, aktiv eller ej)
    Sparrad      — blockerad källa (blockerad: true i YAML)
    EjAnvandbar  — källa som dokumenterats som oanvändbar (anvandbar: false)

Invarianter:
    * Kastar ValueError om en post saknar fältet 'id'.
    * En blockerad källa returneras som Sparrad — aldrig som Kalla.
    * Källkoden hårdkodar inga URL:er. Allt läses ur registret.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Union

import yaml

# Projekt-roten är tre nivåer upp: src/quiet_oppen_data/register.py → … → rot
_PROJEKT_ROT = Path(__file__).parent.parent.parent
_REGISTER_FIL = _PROJEKT_ROT / "kallor" / "kallregister.yaml"

# YAML-nycklar som inte mappas till dataklasserna (dokumentation, anteckningar).
# Dessa ignoreras tyst vid parsning.
_IGNORERADE_NYCKLAR = frozenset({
    "autentisering",
    "verifierat_anrop",
    "tacker",
    "notering",
    "varning",
    "hinder",
    "dokumentation",
    "sprakhantering",
    "metod",
    "roll",
    "version",
    "uppdaterad",
    "kommentar",
})


@dataclass(frozen=True)
class Kalla:
    """En anropbar datakälla — verifierad eller ej, aktiv eller ej."""

    id: str
    adapter: str
    takt: dict
    cache_ttl: int
    myndighet: str | None = None
    bas_url: str | None = None
    verifierad: bool = False
    aktiverad: bool = True
    generisk: bool = False
    licens: str = "okänd"
    attribution: str | None = None
    manniskolank_mall: str | None = None
    faltval: list | None = None        # kurerat fältunderval (TED)
    maxceller: int | None = None       # celltak (PxWeb)


@dataclass(frozen=True)
class Sparrad:
    """Källa blockerad på beställarens instruktion (blockerad: true)."""

    id: str
    myndighet: str
    skal: str


@dataclass(frozen=True)
class EjAnvandbar:
    """Källa som dokumenterats som oanvändbar för chattboten (anvandbar: false)."""

    id: str
    skal: str | None = None


# Union-typ för en post ur registret
KallaPost = Union[Kalla, Sparrad, EjAnvandbar]


def _tolka_verifierad(v: str | bool | None) -> bool:
    """Omvandlar YAML-strängen 'ja'/'nej' till bool."""
    if isinstance(v, bool):
        return v
    return str(v).lower() == "ja"


def las(registersokväg: Path | str | None = None) -> list[KallaPost]:
    """Läser källregistret och returnerar en lista med typade objekt.

    Args:
        registersokväg: Sökväg till YAML-filen. Standardvärde: kallor/kallregister.yaml
                        relativt projektets rot.

    Returns:
        Lista med Kalla, Sparrad och EjAnvandbar-objekt — en per YAML-post.

    Raises:
        ValueError:      om en YAML-post saknar fältet 'id'.
        FileNotFoundError: om registerfilen inte hittas.
    """
    sökväg = Path(registersokväg) if registersokväg else _REGISTER_FIL

    with open(sökväg, encoding="utf-8") as f:
        dokument = yaml.safe_load(f)

    # kallregister.yaml är vanlig YAML: en toppnivåmappning med metadata
    # ('version', 'uppdaterad') och källorna under nyckeln 'kallor'.
    if dokument is None:
        radata: list[dict] = []
    elif isinstance(dokument, dict):
        radata = dokument.get("kallor") or []
        if not isinstance(radata, list):
            raise ValueError(
                f"Nyckeln 'kallor' i källregistret ska vara en lista, "
                f"fick: {type(radata)}"
            )
    elif isinstance(dokument, list):
        # Tolererar det äldre formatet (ren lista på rotnivå).
        radata = dokument
    else:
        raise ValueError(
            f"Källregistret förväntas vara en YAML-mappning med nyckeln 'kallor', "
            f"fick: {type(dokument)}"
        )

    resultat: list[KallaPost] = []
    for i, post in enumerate(radata):
        if not isinstance(post, dict):
            raise ValueError(f"Post {i} är inte ett YAML-objekt: {post!r}")

        if "id" not in post:
            raise ValueError(
                f"Post {i} saknar obligatoriskt fält 'id'. Fält som finns: {list(post.keys())}"
            )

        if post.get("blockerad") is True:
            resultat.append(
                Sparrad(
                    id=post["id"],
                    myndighet=post.get("myndighet", ""),
                    skal=post.get("skal", ""),
                )
            )
        elif post.get("anvandbar") is False:
            resultat.append(
                EjAnvandbar(
                    id=post["id"],
                    skal=post.get("skal"),
                )
            )
        else:
            resultat.append(
                Kalla(
                    id=post["id"],
                    myndighet=post.get("myndighet"),
                    adapter=post.get("adapter", ""),
                    bas_url=post.get("bas_url"),
                    verifierad=_tolka_verifierad(post.get("verifierad", False)),
                    aktiverad=bool(post.get("aktiverad", True)),
                    generisk=bool(post.get("generisk", False)),
                    licens=post.get("licens", "okänd"),
                    attribution=post.get("attribution"),
                    takt=dict(post.get("takt") or {}),
                    cache_ttl=int(post.get("cache_ttl", 3600)),
                    manniskolank_mall=post.get("manniskolank_mall"),
                    faltval=post.get("faltval"),
                    maxceller=post.get("maxceller"),
                )
            )

    return resultat


def hamta(kalla_id: str, registersokväg: Path | str | None = None) -> KallaPost | None:
    """Hämtar en specifik källa ur registret. Returnerar None om den inte finns."""
    for post in las(registersokväg):
        if post.id == kalla_id:
            return post
    return None


def bara_aktiva(registersokväg: Path | str | None = None) -> list[Kalla]:
    """Returnerar bara Kalla-objekt som är aktiva och verifierade."""
    return [
        p for p in las(registersokväg)
        if isinstance(p, Kalla) and p.aktiverad and p.verifierad
    ]
