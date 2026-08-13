"""Loggning — strukturerad loggning per fråga (steg 15).

STUB — implementeras i Steg 15.
"""

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

logger = logging.getLogger("quiet_oppen_data")
