import hashlib
import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, UTC
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from quiet_oppen_data.konfig import las
from quiet_oppen_data.register import Kalla, Sparrad, hamta
from quiet_oppen_data.index.db import oppna_db

logger = logging.getLogger(__name__)

# Explicit timeout. httpx default är 5 s, vilket är för snålt för SCB:s tyngre
# uttag och TED:s söksvar. connect hålls kort, read längre.
TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0)


class SparradKalla(Exception):
    """Kastas om man försöker anropa en spärrad källa."""
    pass


class EjAktiveradKalla(Exception):
    """Kastas om man försöker anropa en källa med aktiverad: false."""
    pass

class TokenBucket:
    def __init__(self, capacity: int, fill_rate: float):
        self.capacity = float(capacity)
        self.fill_rate = fill_rate
        self.tokens = self.capacity
        self.last_update = time.monotonic()
        self.lock = threading.Lock()

    def consume(self, tokens: int = 1) -> None:
        # Låset hålls bara medan tokenräkningen uppdateras. Väntetiden beräknas
        # innanför låset men sovs utanför — annars blockerar en trådande väntan
        # även trådar som har tokens kvar, och kön blir seriell i stället för
        # takthållande.
        with self.lock:
            now = time.monotonic()
            elapsed = now - self.last_update
            self.tokens = min(self.capacity, self.tokens + elapsed * self.fill_rate)
            self.last_update = now
            if self.tokens < tokens:
                sleep_time = (tokens - self.tokens) / self.fill_rate
                # Reservera skulden direkt så parallella anrop inte alla sover
                # lika länge på samma underskott.
                self.tokens -= tokens
            else:
                self.tokens -= tokens
                sleep_time = 0.0

        if sleep_time > 0:
            time.sleep(sleep_time)

_buckets: dict[str, TokenBucket] = {}
_buckets_lock = threading.Lock()

def _get_bucket(kalla: Kalla) -> TokenBucket:
    with _buckets_lock:
        if kalla.id not in _buckets:
            takt = kalla.takt
            anrop = takt.get("anrop", 10)
            per_sekunder = takt.get("per_sekunder", 1)
            fill_rate = anrop / per_sekunder if per_sekunder else 10.0
            _buckets[kalla.id] = TokenBucket(capacity=anrop, fill_rate=fill_rate)
        return _buckets[kalla.id]

def _get_cache_db() -> sqlite3.Connection:
    db_path = Path(las().index.db).parent / "cache.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS http_cache (
            key TEXT PRIMARY KEY,
            data TEXT,
            expires_at REAL
        )
    ''')
    # Per-källa hälsostatistik för GET /halsa (steg 13) — senaste lyckade
    # (icke-cachade) anrop och cache-träffkvot. Bor i samma databas som
    # cachen den beskriver, så isolerad_cache-fixturen isolerar båda.
    conn.execute('''
        CREATE TABLE IF NOT EXISTS kalla_halsa (
            kalla_id TEXT PRIMARY KEY,
            senaste_lyckad REAL,
            cache_traffar INTEGER NOT NULL DEFAULT 0,
            cache_missar INTEGER NOT NULL DEFAULT 0
        )
    ''')
    conn.commit()
    return conn


def _uppdatera_halsa(conn: sqlite3.Connection, kalla_id: str, cache_traff: bool) -> None:
    """Bokför ett anrop mot en källa — cache-träff eller ett nytt lyckat nätanrop."""
    if cache_traff:
        conn.execute(
            "INSERT INTO kalla_halsa (kalla_id, cache_traffar) VALUES (?, 1) "
            "ON CONFLICT(kalla_id) DO UPDATE SET cache_traffar = cache_traffar + 1",
            (kalla_id,),
        )
    else:
        now = time.time()
        conn.execute(
            "INSERT INTO kalla_halsa (kalla_id, senaste_lyckad, cache_missar) "
            "VALUES (?, ?, 1) "
            "ON CONFLICT(kalla_id) DO UPDATE SET senaste_lyckad = ?, "
            "cache_missar = cache_missar + 1",
            (kalla_id, now, now),
        )
    conn.commit()


def halsostatistik() -> dict[str, dict[str, Any]]:
    """Läser per-källa-statistik för GET /halsa: senaste lyckade anrop och
    cache-träffkvot. Källor utan trafik saknas i returvärdet — anroparen
    (api.py) slår ihop det med registrets fullständiga källista."""
    conn = _get_cache_db()
    try:
        rader = conn.execute(
            "SELECT kalla_id, senaste_lyckad, cache_traffar, cache_missar FROM kalla_halsa"
        ).fetchall()
    finally:
        conn.close()

    resultat: dict[str, dict[str, Any]] = {}
    for kalla_id, senaste_lyckad, traffar, missar in rader:
        total = traffar + missar
        resultat[kalla_id] = {
            "senaste_lyckade_anrop": (
                datetime.fromtimestamp(senaste_lyckad, tz=UTC).isoformat()
                if senaste_lyckad else None
            ),
            "cache_traffar": traffar,
            "cache_missar": missar,
            "cache_traffkvot": round(traffar / total, 4) if total else None,
        }
    return resultat

def _ar_tillaten_vard(url: str) -> bool:
    """Sant om värdnamnet i url förekommer i katalogindexet.

    Det här är säkerhetskontrollen för de generiska adaptrarna: de får bara
    anropa värdar som faktiskt finns i dataportalens katalog, aldrig en URL som
    modellen konstruerat själv (ARKITEKTUR.md §3.3, kallregister _generisk_json).
    """
    host = urlparse(url).netloc
    if not host:
        return False

    db_path = Path(las().index.db)
    query = """
    SELECT 1 FROM distribution
    WHERE access_url LIKE ? OR access_url LIKE ? OR access_url LIKE ?
    LIMIT 1
    """
    monster = (f"%://{host}/%", f"%://{host}", f"%://{host}:%")

    conn = oppna_db(db_path)
    try:
        return conn.execute(query, monster).fetchone() is not None
    finally:
        conn.close()

# Tillfälliga fel: for many requests, samt serverfel.
_OMFORSOK_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_OMFORSOK = 3


def _anropa_med_omforsok(method: str, url: str, return_json: bool, **kwargs) -> Any:
    """Utför HTTP-anropet och gör om vid tillfälliga fel (statuskoder och transportfel).

    Respekterar Retry-After när servern skickar den; annars exponentiell
    backoff. Permanenta fel (4xx utom 429) kastas direkt — de blir inte bättre
    av att göras om.
    """
    sista: Exception | None = None
    for forsok in range(_MAX_OMFORSOK):
        try:
            with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
                res = client.request(method, url, **kwargs)

            if res.status_code not in _OMFORSOK_STATUS:
                res.raise_for_status()
                if return_json:
                    return res.json()
                # Om texten innehåller \ufffd (felaktigt angiven charset), försök med cp1252
                if "\ufffd" in res.text:
                    try:
                        return res.content.decode("cp1252")
                    except Exception:
                        pass
                return res.text

            sista = httpx.HTTPStatusError(
                f"{res.status_code} från {url}", request=res.request, response=res
            )
        except (httpx.TransportError, OSError) as exc:
            sista = exc
            logger.info(
                "Nätverksfel %s mot %s — försök %d/%d",
                exc, url, forsok + 1, _MAX_OMFORSOK,
            )

        if forsok == _MAX_OMFORSOK - 1:
            break

        retry_after = getattr(sista, "response", None)
        retry_val = retry_after.headers.get("Retry-After") if retry_after else None
        try:
            vanta = float(retry_val) if retry_val else 2.0 ** forsok
        except ValueError:
            vanta = 2.0 ** forsok
        vanta = min(vanta, 30.0)
        logger.info(
            "Tillfälligt fel mot %s — väntar %.1f s före försök %d/%d",
            url, vanta, forsok + 2, _MAX_OMFORSOK,
        )
        time.sleep(vanta)

    assert sista is not None
    raise sista



def _hamta_generisk(kalla_id: str, method: str, url: str, return_json: bool, **kwargs) -> Any:
    # 1. Kontrollera blockering (Sparrad)
    k = hamta(kalla_id)
    if isinstance(k, Sparrad):
        raise SparradKalla(f"Källan {kalla_id} är spärrad: {k.skal}")
    if not isinstance(k, Kalla):
        raise ValueError(f"Okänd eller ogiltig källa: {kalla_id}")

    # 2. Avstängda källor får inte anropas.
    #    En källa med aktiverad: false har inte bekräftad sökväg eller
    #    svarsformat (ARKITEKTUR.md §0). Utan den här kontrollen kunde
    #    t.ex. bolagsverket_hvd anropas mot en gissad endpoint.
    if not k.aktiverad:
        raise EjAktiveradKalla(
            f"Källan {kalla_id} är inte aktiverad i registret. "
            f"Verifiera den och sätt aktiverad: true innan den anropas."
        )

    # 3. Generiska adaptrar får bara nå värdar som finns i katalogindexet.
    #    Gäller alla generiska, inte bara _generisk_json — risken är identisk
    #    oavsett protokoll när URL:en kommer från modellen.
    if k.generisk and not _ar_tillaten_vard(url):
        raise ValueError(f"Värden i {url} är inte tillåten för generiska anrop.")

    # 3. Cache nyckel
    params = kwargs.get("params")
    json_body = kwargs.get("json")
    
    cache_parts = {
        "method": method.upper(),
        "url": url,
        "params": params,
        "json": json_body,
        "return_json": return_json
    }
    key_str = json.dumps(cache_parts, sort_keys=True)
    cache_key = hashlib.sha256(key_str.encode("utf-8")).hexdigest()
    
    now = time.time()
    conn = _get_cache_db()
    try:
        row = conn.execute(
            "SELECT data, expires_at FROM http_cache WHERE key = ?", (cache_key,)
        ).fetchone()
        if row:
            data, expires_at = row
            if now < expires_at:
                _uppdatera_halsa(conn, kalla_id, cache_traff=True)
                return json.loads(data) if return_json else data
            conn.execute("DELETE FROM http_cache WHERE key = ?", (cache_key,))
            conn.commit()

        # 5. Token bucket
        _get_bucket(k).consume(1)

        # 6. Utför HTTP-anropet, med omförsök på tillfälliga fel.
        #    Utan det blir ett 429 till "källan hade inget att säga", och boten
        #    svarar att den inte hittade något trots att uppgiften finns.
        res_data = _anropa_med_omforsok(method, url, return_json, **kwargs)

        # 7. Spara i cache
        conn.execute(
            "REPLACE INTO http_cache (key, data, expires_at) VALUES (?, ?, ?)",
            (
                cache_key,
                json.dumps(res_data) if return_json else res_data,
                now + k.cache_ttl,
            ),
        )
        _uppdatera_halsa(conn, kalla_id, cache_traff=False)
        conn.commit()
    finally:
        conn.close()

    return res_data

def hamta_json(kalla_id: str, method: str, url: str, **kwargs) -> Any:
    """Gemensam HTTP-klient med blocklista, cache och kö. Returnerar JSON."""
    return _hamta_generisk(kalla_id, method, url, True, **kwargs)

def hamta_text(kalla_id: str, method: str, url: str, **kwargs) -> str:
    """Gemensam HTTP-klient. Returnerar råtext."""
    return _hamta_generisk(kalla_id, method, url, False, **kwargs)


