"""Delade fixturer.

Den viktigaste är `isolerad_cache`. Transportlagret cachar HTTP-svar i
data/cache.sqlite, vilket är rätt i drift men fel i test: cachen överlever mellan
körningar och kortsluter varje test som tror sig göra ett anrop. Utan isolering
mäter testerna diskens tillstånd i stället för koden — och VCR spelar aldrig in
någon kassett, eftersom ingen HTTP-trafik uppstår.
"""

import dataclasses

import pytest


@pytest.fixture
def isolerad_cache(tmp_path, monkeypatch):
    """Ger testet en egen, tom HTTP-cache.

    transport._get_cache_db() lägger cache.sqlite bredvid konfig.index.db, så vi
    pekar om index.db till tmp_path och tar cachen med den.

    Fixturen är medvetet INTE autouse: test_generisk_json_avvisar_okand_vard och
    dess syskon läser katalogindexet via samma sökväg och ska köra mot det
    riktiga indexet.
    """
    import quiet_oppen_data.adaptrar.transport as transport

    riktig = transport.las()
    testkonfig = dataclasses.replace(
        riktig,
        index=dataclasses.replace(riktig.index, db=str(tmp_path / "index.sqlite")),
    )
    monkeypatch.setattr(transport, "las", lambda: testkonfig)
    transport._buckets.clear()
    yield tmp_path
    transport._buckets.clear()
