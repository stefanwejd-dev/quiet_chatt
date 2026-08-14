"""Acceptanstester för Steg 17 — Skatteverkets statistikdatamängder.

Elva nya UUID:n läggs till i skatteverket_rowstore (kallor/kallregister.yaml).
Kravet som gör steget värt något: en siffra får aldrig presenteras utan att
visa vilket år den avser OCH om uppdateringsdatumet är avläst ur svaret eller
bara källregistrets påstående (ARKITEKTUR.md §5 regel 8).

Varje nätverksberoende test tar isolerad_cache-fixturen (se conftest.py).
VCR-kassetter spelas in i tests/kassetter/ vid första körning (record_mode="once").
"""

import vcr

from quiet_oppen_data.adaptrar.rowstore import RowStoreAdapter
from quiet_oppen_data.modeller import Fragplan
from quiet_oppen_data.register import hamta as hamta_kalla

VCR_CONFIG = {
    "cassette_library_dir": "tests/kassetter",
    "record_mode": "once",
    "match_on": ["method", "scheme", "host", "port", "path", "query"],
}

_MOMS_UUID = "f2f815f5-8d12-4d22-9a95-b6fda1a58e42"


def test_alla_elva_uuid_finns_i_registret():
    dataset = hamta_kalla("skatteverket_rowstore").dataset
    uuiden = {d["uuid"] for d in dataset}
    forvantade = {
        "f2f815f5-8d12-4d22-9a95-b6fda1a58e42",
        "7691bcf3-79be-46fb-a252-8442a8f6415e",
        "61a28d49-38ca-4686-9a6a-6a9ae4e66d1c",
        "a1866379-6bff-4010-b482-37ce112eeebd",
        "56173b69-5c31-4c32-92b1-8560ee5f492d",
        "a57c7163-aef9-4716-91e3-df126db01285",
        "f57fb128-34ac-4f7e-b37f-f4e43f31a4b7",
        "c2f577e7-f4d7-4e41-a6f0-d3364f32e3b7",
        "61a59c73-c31f-4c1e-a1d6-23fb018ffcd3",
        "8546f1b7-7024-48ff-80e8-eed278b93eed",
        "8ef49703-f7c2-4055-8903-a3dab876b2e7",
    }
    saknas = forvantade - uuiden
    assert not saknas, f"UUID:n saknas i registret: {saknas}"


@vcr.use_cassette(**VCR_CONFIG)
def test_momsdeklarationer_svarar_200_med_rader(isolerad_cache):
    utkast = RowStoreAdapter("skatteverket_rowstore").hamta(
        Fragplan(fraga="", extra={"uuid": _MOMS_UUID, "limit": 3})
    )
    assert utkast, "momsdeklarationer ska ge rader"


@vcr.use_cassette(**VCR_CONFIG)
def test_momsdeklarationer_period_ur_raden(isolerad_cache):
    """Frågan 'hur många momsdeklarationer lämnas per år?' måste kunna
    besvaras med en Faktapost som visar vilket år uppgiften avser."""
    utkast = RowStoreAdapter("skatteverket_rowstore").hamta(
        Fragplan(fraga="", extra={"uuid": _MOMS_UUID, "limit": 1})
    )
    assert utkast
    post = utkast[0]
    assert post.period, "raden bär en periodkolumn (period=ÅÅÅÅMM) och ska fyllas i"
    assert post.lank_manniska and post.lank_maskin


@vcr.use_cassette(**VCR_CONFIG)
def test_uppdateringsdatum_ar_avlast_ur_raden_inte_pastatt(isolerad_cache):
    """Momsdeklarationernas rader bär sin egen uppdateringsdatum-kolumn.

    Den ska hamna i dimensioner under nyckeln 'uppdateringsdatum' (avläst) —
    INTE under 'uppdaterad_enligt_kallregister' (påstått), eftersom den här
    datamängden faktiskt lämnar datumet i svaret.
    """
    utkast = RowStoreAdapter("skatteverket_rowstore").hamta(
        Fragplan(fraga="", extra={"uuid": _MOMS_UUID, "limit": 1})
    )
    assert utkast
    post = utkast[0]
    assert "uppdateringsdatum" in post.dimensioner
    assert "uppdaterad_enligt_kallregister" not in post.dimensioner
    assert "uppdateringsdatum:" not in post.varde, (
        "uppdateringskolumnen ska inte upprepas i värdesträngen"
    )


@vcr.use_cassette(**VCR_CONFIG)
def test_inkomstdeklarationer_period_ur_inkomstar(isolerad_cache):
    """Denna datamängd saknar en egen 'period'-kolumn — 'inkomstar' är den
    enda periodkolumnen och måste därför bli Faktautkast.period."""
    utkast = RowStoreAdapter("skatteverket_rowstore").hamta(
        Fragplan(fraga="", extra={
            "uuid": "7691bcf3-79be-46fb-a252-8442a8f6415e", "limit": 1})
    )
    assert utkast
    assert utkast[0].period, "inkomstar ska tolkas som period"


@vcr.use_cassette(**VCR_CONFIG)
def test_etiketten_namnger_statistikdatamangden(isolerad_cache):
    utkast = RowStoreAdapter("skatteverket_rowstore").hamta(
        Fragplan(fraga="", extra={"uuid": _MOMS_UUID, "limit": 1})
    )
    assert utkast
    assert "Antal momsdeklarationer" in utkast[0].etikett
    assert _MOMS_UUID not in utkast[0].etikett
