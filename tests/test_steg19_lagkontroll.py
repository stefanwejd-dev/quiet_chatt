"""Acceptanstester för Steg 19 — nattlig färskhetskontroll av lagkorpuset.

ARKITEKTUR.md §5 regel 8 kräver att lagkopians färskhet kontrolleras nattligt
genom att jämföra `systemdatum`. Testerna verifierar tre saker:

  1. Bara författningar vars systemdatum FAKTISKT ändrats (bekräftat av
     Riksdagen) ingesteras om.
  2. Ett totalt avbrott mot Riksdagen (alla huvudanrop misslyckas) lämnar
     indexet orört och rapporteras med status="fel" — inte tyst som
     "inget ändrat".
  3. `las_lagkorpus_alder` beräknar ålder per författning och flaggar dem
     som ligger efter.
"""
from __future__ import annotations

from quiet_oppen_data.index.db import oppna_db
from quiet_oppen_data.index.lag_ingest import las_lagkorpus_alder, nattlig_lagkontroll

_LAGREGISTER_ANTAL = 62  # se lagar/lagregister.yaml

_MINIMAL_LAGTEXT = """Testlag (2020:1) t.o.m. SFS 2020:1

1 kap. Inledande bestämmelser

1 §   Denna lag innehåller bestämmelser för test.
      Ett andra stycke för sammanhangets skull.

"""


def _seed(conn, sfs, systemdatum, hamtad="2020-01-01T00:00:00+00:00", kortnamn="IL"):
    conn.execute(
        """
        INSERT INTO lag_dokument
        (sfs, dok_id, namn, kortnamn, tom_sfs, systemdatum, hamtad, lank_manniska, lank_maskin, ratext)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (sfs, f"sfs-{sfs.replace(':', '-')}", "Testlag", kortnamn,
         "t.o.m. SFS 2020:1", systemdatum, hamtad,
         "https://www.riksdagen.se/x", "https://data.riksdagen.se/x", "gammal text"),
    )
    conn.commit()


def test_bara_andrade_forfattningar_ingesteras_om(monkeypatch, tmp_path):
    """En författning med samma systemdatum ska INTE ingesteras om."""
    from quiet_oppen_data.adaptrar import transport
    from quiet_oppen_data import lagregister

    # Seeda hela registret lokalt med sitt NUVARANDE (oförändrade) systemdatum,
    # utom en författning som ligger efter.
    conn = oppna_db(tmp_path / "index.sqlite")
    alla = lagregister.las()
    for lag in alla:
        _seed(conn, lag.sfs, systemdatum="2020-01-01 00:00:00", kortnamn=lag.kortnamn)

    andrad_sfs = alla[0].sfs

    def mock_hamta_json(kalla_id, method, url, **kwargs):
        # Alla får samma (oförändrade) systemdatum, utom en.
        for lag in alla:
            if f"/{lag.dok_id}.json" in url:
                systemdatum = (
                    "2026-08-14 12:00:00" if lag.sfs == andrad_sfs else "2020-01-01 00:00:00"
                )
                return {"dokumentstatus": {"dokument": {
                    "dok_id": lag.dok_id, "systemdatum": systemdatum,
                }}}
        raise AssertionError(f"okänd url {url}")

    def mock_hamta_text(kalla_id, method, url, **kwargs):
        return _MINIMAL_LAGTEXT

    monkeypatch.setattr(transport, "hamta_json", mock_hamta_json)
    monkeypatch.setattr(transport, "hamta_text", mock_hamta_text)

    resultat = nattlig_lagkontroll(generera_vektorer=False, db_conn=conn)

    assert resultat["kontrollerade"] == len(alla)
    assert resultat["andrade"] == 1
    assert resultat["sfs_omingesterade"] == [andrad_sfs] or resultat["omingesterade"] == 1
    assert resultat["ingest_fel"] == 0
    assert resultat["status"] == "ok"

    # Den ändrade författningen har nu det nya systemdatumet i indexet …
    rad = conn.execute(
        "SELECT systemdatum FROM lag_dokument WHERE sfs = ?", (andrad_sfs,)
    ).fetchone()
    assert rad[0] == "2026-08-14 12:00:00"

    # … medan en oförändrad författning INTE rördes (samma gamla ratext kvar).
    ovald_sfs = alla[1].sfs
    rad2 = conn.execute(
        "SELECT ratext FROM lag_dokument WHERE sfs = ?", (ovald_sfs,)
    ).fetchone()
    assert rad2[0] == "gammal text"

    conn.close()


def test_totalt_avbrott_lamnar_index_orort_och_rapporterar_fel(monkeypatch, tmp_path):
    """Om ALLA huvudanrop mot Riksdagen misslyckas ska körningen avslutas med
    status='fel' — inte tyst rapportera 'inget har ändrats'."""
    from quiet_oppen_data.adaptrar import transport
    from quiet_oppen_data import lagregister

    conn = oppna_db(tmp_path / "index.sqlite")
    alla = lagregister.las()
    for lag in alla:
        _seed(conn, lag.sfs, systemdatum="2020-01-01 00:00:00", kortnamn=lag.kortnamn)

    def alltid_fel(kalla_id, method, url, **kwargs):
        raise ConnectionError("Riksdagen svarar inte")

    monkeypatch.setattr(transport, "hamta_json", alltid_fel)

    resultat = nattlig_lagkontroll(generera_vektorer=False, db_conn=conn)

    assert resultat["status"] == "fel"
    assert resultat["andrade"] == 0, "inget fick tolkas som bekräftat ändrat"
    assert resultat["fel_vid_kontroll"] == len(alla)

    # Indexet ska vara helt orört.
    for lag in alla:
        rad = conn.execute(
            "SELECT systemdatum, ratext FROM lag_dokument WHERE sfs = ?", (lag.sfs,)
        ).fetchone()
        assert rad[0] == "2020-01-01 00:00:00"
        assert rad[1] == "gammal text"

    conn.close()


def test_misslyckad_omingest_rapporteras_som_fel_utan_att_krascha(monkeypatch, tmp_path):
    """En författning som bekräftas ändrad men vars omhämtning misslyckas ska
    synas som ingest_fel, inte tysta bort hela körningen."""
    from quiet_oppen_data.adaptrar import transport
    from quiet_oppen_data import lagregister

    conn = oppna_db(tmp_path / "index.sqlite")
    alla = lagregister.las()
    for lag in alla:
        _seed(conn, lag.sfs, systemdatum="2020-01-01 00:00:00", kortnamn=lag.kortnamn)

    andrad_sfs = alla[0].sfs

    def mock_hamta_json(kalla_id, method, url, **kwargs):
        for lag in alla:
            if f"/{lag.dok_id}.json" in url:
                systemdatum = (
                    "2026-08-14 12:00:00" if lag.sfs == andrad_sfs else "2020-01-01 00:00:00"
                )
                return {"dokumentstatus": {"dokument": {
                    "dok_id": lag.dok_id, "systemdatum": systemdatum,
                }}}
        raise AssertionError(f"okänd url {url}")

    def mock_hamta_text(kalla_id, method, url, **kwargs):
        return ""  # tom text -> hamta_och_indexera_lag höjer ValueError

    monkeypatch.setattr(transport, "hamta_json", mock_hamta_json)
    monkeypatch.setattr(transport, "hamta_text", mock_hamta_text)

    resultat = nattlig_lagkontroll(
        generera_vektorer=False, db_conn=conn,
    )

    assert resultat["andrade"] == 1
    assert resultat["ingest_fel"] == 1
    assert resultat["status"] == "fel"
    assert resultat["sfs_ingest_fel"] == [andrad_sfs]

    # Den gamla kopian ska finnas kvar orörd trots det misslyckade försöket.
    rad = conn.execute(
        "SELECT systemdatum FROM lag_dokument WHERE sfs = ?", (andrad_sfs,)
    ).fetchone()
    assert rad[0] == "2020-01-01 00:00:00"

    conn.close()


def test_lagkorpus_alder_flaggar_gammal_kopia(tmp_path):
    from datetime import UTC, datetime, timedelta

    conn = oppna_db(tmp_path / "index.sqlite")
    ny = (datetime.now(UTC) - timedelta(hours=6)).isoformat()
    gammal = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    _seed(conn, "1999:1229", systemdatum="x", hamtad=ny, kortnamn="IL")
    _seed(conn, "2023:200", systemdatum="x", hamtad=gammal, kortnamn="ML")

    alder = las_lagkorpus_alder(db_conn=conn, troskel_dygn=2)
    conn.close()

    per_sfs = {r["sfs"]: r for r in alder}
    assert per_sfs["1999:1229"]["ligger_efter"] is False
    assert per_sfs["2023:200"]["ligger_efter"] is True
    assert per_sfs["2023:200"]["dygn_sedan_hamtning"] > 4


def test_lagkorpus_alder_flaggar_saknad_forfattning(tmp_path):
    """En författning i lagregistret som aldrig ingesterats ska alltid
    räknas som ligger_efter."""
    conn = oppna_db(tmp_path / "index.sqlite")
    alder = las_lagkorpus_alder(db_conn=conn)
    conn.close()

    assert len(alder) == _LAGREGISTER_ANTAL
    assert all(r["ligger_efter"] for r in alder)
    assert all(r["hamtad"] is None for r in alder)
