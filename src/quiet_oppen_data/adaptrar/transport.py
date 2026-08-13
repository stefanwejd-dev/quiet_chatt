import httpx
from quiet_oppen_data.register import Kalla

def hamta_json(kalla: Kalla, method: str, url: str, **kwargs) -> dict | list:
    """Gemensam HTTP-klient. Byggs ut med kö och cache i Steg 5."""
    with httpx.Client() as client:
        res = client.request(method, url, **kwargs)
        res.raise_for_status()
        return res.json()
