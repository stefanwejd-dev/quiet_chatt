from quiet_oppen_data.adaptrar.bas import Adapter
from quiet_oppen_data.adaptrar.riksbanken import RiksbankenAdapter
from quiet_oppen_data.adaptrar.vies import ViesAdapter
from quiet_oppen_data.adaptrar.pxweb import PxWebAdapter
from quiet_oppen_data.adaptrar.ted import TedAdapter
from quiet_oppen_data.adaptrar.riksdagen import RiksdagenAdapter
from quiet_oppen_data.adaptrar.kolada import KoladaAdapter
from quiet_oppen_data.adaptrar.dataportal import DataportalAdapter
from quiet_oppen_data.adaptrar.rowstore import RowStoreAdapter
from quiet_oppen_data.adaptrar.json_rest import JsonRestAdapter
from quiet_oppen_data.adaptrar.lagtext import LagtextAdapter
from quiet_oppen_data.adaptrar.skatteverket_vagledning import SkatteverketVagledningAdapter
from quiet_oppen_data.adaptrar.skatteverket_rattsligaregler import (
    SkatteverketRattsligaReglerAdapter,
)
from quiet_oppen_data.adaptrar.bolagsverket import BolagsverketAdapter

__all__ = [
    "Adapter",
    "RiksbankenAdapter",
    "ViesAdapter",
    "PxWebAdapter",
    "TedAdapter",
    "RiksdagenAdapter",
    "KoladaAdapter",
    "DataportalAdapter",
    "RowStoreAdapter",
    "JsonRestAdapter",
    "LagtextAdapter",
    "SkatteverketVagledningAdapter",
    "SkatteverketRattsligaReglerAdapter",
    "BolagsverketAdapter",
]

