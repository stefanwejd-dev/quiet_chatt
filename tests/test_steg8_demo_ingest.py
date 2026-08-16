"""Tester för demoläge i ingesten (Steg 8).

Verifierar:
1. Att db märks med demo_index och att ar_demo_index() returnerar True.
2. Att GET /halsa redovisar demo_index korrekt.
3. Att demo-ingest för lagar ger exakt de 5 huvudlagarna (färre än hela sviten på 62).
4. Att urvalsfilen för katalogingest (kallor/demo_urval.yaml) existerar och är välformaterad.
"""
from pathlib import Path

from fastapi.testclient import TestClient

from quiet_oppen_data.api import app
from quiet_oppen_data.index.db import oppna_db, satt_meta, hamta_meta, ar_demo_index
from quiet_oppen_data.index.lag_ingest import DEMO_SFS
from quiet_oppen_data import lagregister


def test_demo_urval_fil_finns():
    """kallor/demo_urval.yaml ska finnas och innehålla sökningar."""
    urval_fil = Path(__file__).parent.parent / "kallor" / "demo_urval.yaml"
    assert urval_fil.exists(), "kallor/demo_urval.yaml saknas"
    
    import yaml
    with open(urval_fil, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    
    assert "sokningar" in data
    assert len(data["sokningar"]) >= 5


def test_db_meta_demo_markering(tmp_path: Path):
    """Märkning av demo-index i databasens _index_meta."""
    db_fil = tmp_path / "test_demo.sqlite"
    conn = oppna_db(db_fil)
    
    # Från början är den inte märkt som demo
    assert not ar_demo_index(conn)
    
    # Sätt märkning
    satt_meta(conn, "demo_index", "1")
    assert hamta_meta(conn, "demo_index") == "1"
    assert ar_demo_index(conn) is True
    conn.close()


def test_halsa_redovisar_demo_index(tmp_path: Path, monkeypatch):
    """GET /halsa ska inkludera 'demo_index' i svaret."""
    db_fil = tmp_path / "halsa_demo.sqlite"
    conn = oppna_db(db_fil)
    satt_meta(conn, "demo_index", "1")
    conn.close()

    import dataclasses
    from quiet_oppen_data import konfig
    gammal_las = konfig.las
    def mock_las():
        k = gammal_las()
        ny_index = dataclasses.replace(k.index, db=str(db_fil))
        return dataclasses.replace(k, index=ny_index)
    monkeypatch.setattr(konfig, "las", mock_las)

    client = TestClient(app)
    res = client.get("/halsa")
    assert res.status_code == 200
    data = res.json()
    assert "demo_index" in data
    assert data["demo_index"] is True


def test_demo_lag_urval_farre_an_fullt():
    """Demo-lagurvalet ska innehålla de 5 huvudlagarna och vara strikt mindre än fulla registret."""
    alla_lagar = lagregister.las()
    assert len(alla_lagar) >= 60, "Fulla registret ska ha 62 författningar"
    
    assert len(DEMO_SFS) == 5
    # Alla demo-lagar ska finnas i registret
    alla_sfs = {l.sfs for l in alla_lagar}
    for sfs in DEMO_SFS:
        assert sfs in alla_sfs
    
    # Verifiera att de fem är de avsedda
    assert "1999:1229" in DEMO_SFS  # IL
    assert "2023:200" in DEMO_SFS   # ML
    assert "2011:1244" in DEMO_SFS  # SFL
    assert "1999:1078" in DEMO_SFS  # BFL
    assert "1995:1554" in DEMO_SFS  # ÅRL
