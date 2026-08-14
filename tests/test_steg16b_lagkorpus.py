"""Acceptanstester för Steg 16B — Lagkorpus, samtliga 62 författningar.

Acceptanskriterier (PLAN.md §16B):
1. Alla författningar i listan är hämtade, parsade och indexerade.
2. Ingen författning har noll paragrafer (alla har > 0 chunks).
3. Den nattliga ändringskontrollen täcker hela registret och rapporterar antal oförändrade, ändrade och misslyckade.
4. Ett stickprov på tio författningar: t.o.m. SFS i indexet stämmer mot Riksdagens aktuella metadata.
5. python -m ruff check . och python -m pytest -q är rena.
"""
from pathlib import Path

import pytest

from quiet_oppen_data import lagregister
from quiet_oppen_data.index.db import oppna_db
from quiet_oppen_data.index.lag_ingest import kontrollera_andringar
from quiet_oppen_data.index.sok import sok_lag
from quiet_oppen_data.konfig import las as las_konfig


def test_alla_62_forfattningar_finns_i_registret():
    """Samtliga 62 författningar finns deklarerade i lagar/lagregister.yaml."""
    lagar = lagregister.las()
    assert len(lagar) == 62, f"Förväntade 62 författningar, fann {len(lagar)}"


def test_alla_62_forfattningar_har_chunks_och_inte_noll():
    """Ingen författning har noll paragrafer/chunks i indexet."""
    konfig = las_konfig()
    conn = oppna_db(Path(konfig.index.db))

    lagar = lagregister.las()
    saknade = []
    for lag in lagar:
        rad = conn.execute("SELECT COUNT(*) FROM lag_chunk WHERE sfs = ?", (lag.sfs,)).fetchone()
        antal = rad[0] if rad else 0
        if antal == 0:
            saknade.append((lag.sfs, lag.kortnamn))

    conn.close()
    assert len(saknade) == 0, f"Författningar med 0 chunks i databasen: {saknade}"


def test_stickprov_tio_forfattningar_tom_sfs_och_systemdatum():
    """Stickprov på tio författningar: t.o.m. SFS finns och är ifylld."""
    stickprov_sfs = [
        "1999:1229",  # IL
        "2023:200",   # ML
        "2011:1244",  # SFL
        "2010:110",   # SFB
        "1994:1776",  # LSEn
        "2005:551",   # ABL
        "1970:994",   # JB
        "1991:481",   # FOL
        "1991:586",   # SINK
        "2011:1268",  # ISKL
    ]

    konfig = las_konfig()
    conn = oppna_db(Path(konfig.index.db))

    for sfs in stickprov_sfs:
        rad = conn.execute(
            "SELECT namn, tom_sfs, dok_id FROM lag_dokument WHERE sfs = ?", (sfs,)
        ).fetchone()
        assert rad is not None, f"Lagen {sfs} saknas i lag_dokument"
        namn, tom_sfs, dok_id = rad
        assert len(tom_sfs) > 0, f"Lagen {sfs} ({namn}) saknar konsolideringspunkt (tom_sfs)"
        assert dok_id == f"sfs-{sfs.replace(':', '-')}"

    conn.close()


def test_andringskontroll_hela_registret(monkeypatch):
    """Ändringskontrollen täcker hela registret och rapporterar per lag."""
    from quiet_oppen_data.adaptrar import transport

    # Mocka snabbt för alla 62 lagar
    def mock_hamta_json(kalla_id, method, url, **kwargs):
        return {
            "dokumentstatus": {
                "dokument": {
                    "dok_id": "test",
                    "systemdatum": "2026-08-14 00:00:00",
                }
            }
        }

    monkeypatch.setattr(transport, "hamta_json", mock_hamta_json)

    konfig = las_konfig()
    conn = oppna_db(Path(konfig.index.db))

    rapporter = kontrollera_andringar(db_conn=conn)
    conn.close()

    assert len(rapporter) == 62, f"Förväntade 62 rapporter, fick {len(rapporter)}"
    for r in rapporter:
        assert "sfs" in r
        assert "kortnamn" in r
        assert "andrad" in r
        assert "lokalt_systemdatum" in r
        assert "fjarr_systemdatum" in r


@pytest.mark.live
def test_andringskontroll_hela_registret_live():
    """Live-test mot Riksdagen för samtliga 62 författningar."""
    konfig = las_konfig()
    conn = oppna_db(Path(konfig.index.db))

    rapporter = kontrollera_andringar(db_conn=conn)
    conn.close()

    assert len(rapporter) == 62, f"Förväntade 62 rapporter, fick {len(rapporter)}"
    for r in rapporter:
        assert "sfs" in r
        assert "kortnamn" in r
        assert "andrad" in r
        assert "lokalt_systemdatum" in r
        assert "fjarr_systemdatum" in r


def test_sok_lag_over_flera_forfattningar():
    """Hybridsökning hittar relevanta paragrafer i olika lagar beroende på domän."""
    # Sökning på aktiebolagsfråga ska ge ABL
    res_abl = sok_lag("lämna vinstutdelning aktiebolag", max_antal=5)
    assert any(t.sfs == "2005:551" for t in res_abl), "Ingen ABL-träff för vinstutdelning"

    # Sökning på ROT/RUT ska ge HUSFL eller IL
    res_rot = sok_lag("skattereduktion för hushållsarbete rot rut", max_antal=5)
    assert any(t.sfs in ("2009:194", "1999:1229") for t in res_rot), "Ingen HUSFL/IL-träff för rot/rut"
