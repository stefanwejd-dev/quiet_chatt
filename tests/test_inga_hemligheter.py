"""Skydd mot att hemligheter hamnar i versionshanteringen.

Bakgrund: 2026-08-14 committades ett riktigt MATNING_NYCKEL-värde i
.env.example. Repot saknar fjärrepo, så nyckeln lämnade aldrig maskinen, och
historiken skrevs om — men filen är avsedd som mall och det är lätt hänt att
någon fyller i den och glömmer.
"""

from pathlib import Path

import pytest

ROT = Path(__file__).resolve().parent.parent

# Variabler som ALDRIG får ha ett ifyllt värde i mallfilen.
_MALLVARIABLER = ("ANTHROPIC_API_KEY", "MATNING_NYCKEL",
                  "BOLAGSVERKET_CLIENT_ID", "BOLAGSVERKET_CLIENT_SECRET")

# Platshållare som är tillåtna som "värde".
_PLATSHALLARE = ("", "sk-ant-...", "...", "<fyll i>", "ändra-mig")


def test_env_example_innehaller_inga_riktiga_varden():
    fil = ROT / ".env.example"
    if not fil.exists():
        pytest.skip(".env.example saknas")

    for radnr, rad in enumerate(fil.read_text(encoding="utf-8").splitlines(), 1):
        rad = rad.strip()
        if rad.startswith("#") or "=" not in rad:
            continue
        namn, _, varde = rad.partition("=")
        if namn.strip() in _MALLVARIABLER and varde.strip() not in _PLATSHALLARE:
            pytest.fail(
                f".env.example rad {radnr}: {namn.strip()} har ett ifyllt värde. "
                "Mallfilen ska bara innehålla platshållare — riktiga nycklar hör "
                "hemma i .env eller i den hemliga mappen utanför repot."
            )


def test_env_ar_ignorerad():
    """.env måste vara gitignorerad — annars är mallfilen meningslös."""
    ignore = (ROT / ".gitignore")
    assert ignore.exists(), ".gitignore saknas"
    rader = {r.strip() for r in ignore.read_text(encoding="utf-8").splitlines()}
    assert ".env" in rader, ".env måste stå i .gitignore"
