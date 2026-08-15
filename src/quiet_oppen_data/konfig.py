"""Konfigurationsläsning — config.toml + .env.

Laddas lat: anropa `las()` för att hämta den sammanslagna konfigurationen.
Modulimporten kastar ingenting om filerna saknas.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Projekt-roten är tre nivåer upp från den här filen:
# src/quiet_oppen_data/konfig.py → src/quiet_oppen_data → src → rot.
# Se motsvarande kommentar i register.py — QUIET_OPPEN_DATA_ROOT låter en
# inbäddande applikation (t.ex. sie-mcp) peka ut sin egen kopia av
# config.toml/.env i stället för att las() letar inne i paketets
# installationskatalog.
_PROJEKT_ROT = Path(os.environ.get("QUIET_OPPEN_DATA_ROOT") or Path(__file__).parent.parent.parent)
_KONFIG_FIL = _PROJEKT_ROT / "config.toml"


@dataclass(frozen=True)
class SiteKonfig:
    domain: str
    # Sant bakom en reverse proxy (Coolify/Traefik) som sätter X-Forwarded-For.
    # Falskt om appen exponeras direkt — då är headern klientstyrd och skulle
    # göra per-IP-kvoten verkningslös. Se api.py:_klient_ip.
    betrodd_proxy: bool = True


@dataclass(frozen=True)
class ModellKonfig:
    namn: str
    effort_hamtning: str
    effort_syntes: str
    max_verktygsvarv: int
    # Tak för thinking + svarstext per varv. Med adaptiv thinking räknas båda
    # mot samma tak, så ett snålt värde trunkerar mitt i ett resonemang.
    max_tokens_hamtning: int = 16000
    # Tak för fas B (syntes). Mekaniskt jämfört med fas A — schemat håller
    # utdata kort — men täcker ändå thinking + JSON-svarstext.
    max_tokens_syntes: int = 8000


@dataclass(frozen=True)
class KvotKonfig:
    fragor_per_ip_per_dygn: int
    fragor_totalt_per_dygn: int
    # Internt larm, inte betalningsspärr. Den faktiska garantin är att
    # Anthropic-kontot är förskottsbetalt utan auto-reload — se ARKITEKTUR.md §6a.
    kostnadstak_sek_per_manad: int = 1000


@dataclass(frozen=True)
class IndexKonfig:
    db: str
    embedding_modell: str
    embedding_dim: int


@dataclass(frozen=True)
class Konfig:
    site: SiteKonfig
    modell: ModellKonfig
    kvot: KvotKonfig
    index: IndexKonfig
    anthropic_api_key: str | None  # None om ANTHROPIC_API_KEY inte är satt i miljön


_cache: Konfig | None = None


def las(konfig_fil: Path | str | None = None) -> Konfig:
    """Läser config.toml och .env och returnerar ett fryst Konfig-objekt.

    Resultatet cachas — upprepade anrop ger samma objekt.

    Kastar:
        FileNotFoundError  om config.toml inte hittas
        KeyError           om ANTHROPIC_API_KEY saknas i miljön
        tomllib.TOMLDecodeError om filen är ogiltig TOML
    """
    global _cache
    if _cache is not None:
        return _cache

    # Ladda .env om den finns (tyst om den saknas)
    load_dotenv(_PROJEKT_ROT / ".env")

    sökväg = Path(konfig_fil) if konfig_fil else _KONFIG_FIL
    with open(sökväg, "rb") as f:
        data = tomllib.load(f)

    _cache = Konfig(
        site=SiteKonfig(**data["site"]),
        modell=ModellKonfig(**data["modell"]),
        kvot=KvotKonfig(**data["kvot"]),
        index=IndexKonfig(**data["index"]),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
    )
    return _cache


def nollstall() -> None:
    """Nollställ cachen — används i tester."""
    global _cache
    _cache = None
