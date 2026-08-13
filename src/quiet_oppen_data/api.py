"""HTTP-API — FastAPI-applikation (steg 13).

STUB — implementeras i Steg 13.
"""

from fastapi import FastAPI

app = FastAPI(title="Quiet Öppen Data", version="0.1.0")


@app.get("/halsa")
async def halsa():
    """Hälsokontroll — returnerar 200 om appen startar."""
    return {"status": "ok"}
