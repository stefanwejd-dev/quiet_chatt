import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from quiet_oppen_data.konfig import las
from quiet_oppen_data.register import Kalla, Sparrad, hamta
from quiet_oppen_data.index.db import oppna_db

class SparradKalla(Exception):
    """Kastas om man försöker anropa en spärrad källa."""
    pass

class TokenBucket:
    def __init__(self, capacity: int, fill_rate: float):
        self.capacity = float(capacity)
        self.fill_rate = fill_rate
        self.tokens = self.capacity
        self.last_update = time.monotonic()
        self.lock = threading.Lock()

    def consume(self, tokens: int = 1) -> None:
        with self.lock:
            now = time.monotonic()
            elapsed = now - self.last_update
            self.tokens = min(self.capacity, self.tokens + elapsed * self.fill_rate)
            self.last_update = now
            if self.tokens < tokens:
                sleep_time = (tokens - self.tokens) / self.fill_rate
                time.sleep(sleep_time)
                self.tokens = 0
                self.last_update = time.monotonic()
            else:
                self.tokens -= tokens

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
    conn.commit()
    return conn

def _ar_tillaten_vard(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc
    
    db_path = Path(las().index.db)
    conn = oppna_db(db_path)
    
    query = """
    SELECT 1 FROM distribution 
    WHERE access_url LIKE ? OR access_url LIKE ? OR access_url LIKE ?
    LIMIT 1
    """
    p1 = f"%://{host}/%"
    p2 = f"%://{host}"
    p3 = f"%://{host}:%"
    
    if conn.execute(query, (p1, p2, p3)).fetchone():
        return True
    return False

def hamta_json(kalla_id: str, method: str, url: str, **kwargs) -> Any:
    """Gemensam HTTP-klient med blocklista, cache och kö."""
    # 1. Kontrollera blockering (Sparrad)
    k = hamta(kalla_id)
    if isinstance(k, Sparrad):
        raise SparradKalla(f"Källan {kalla_id} är spärrad: {k.skal}")
    if not isinstance(k, Kalla):
        raise ValueError(f"Okänd eller ogiltig källa: {kalla_id}")

    # 2. Kontrollera _generisk_json tillåten värd
    if k.id == "_generisk_json":
        if not _ar_tillaten_vard(url):
            raise ValueError(f"Värden i {url} är inte tillåten för generiska anrop.")

    # 3. Cache nyckel (normaliserad metod, url, body/params)
    params = kwargs.get("params")
    json_body = kwargs.get("json")
    
    cache_parts = {
        "method": method.upper(),
        "url": url,
        "params": params,
        "json": json_body
    }
    key_str = json.dumps(cache_parts, sort_keys=True)
    cache_key = hashlib.sha256(key_str.encode("utf-8")).hexdigest()
    
    conn = _get_cache_db()
    
    now = time.time()
    row = conn.execute("SELECT data, expires_at FROM http_cache WHERE key = ?", (cache_key,)).fetchone()
    if row:
        data, expires_at = row
        if now < expires_at:
            return json.loads(data)
        else:
            conn.execute("DELETE FROM http_cache WHERE key = ?", (cache_key,))
            conn.commit()

    # 4. Token bucket (Rate limiting)
    bucket = _get_bucket(k)
    bucket.consume(1)

    # 5. Utför HTTP-anropet
    with httpx.Client() as client:
        res = client.request(method, url, **kwargs)
        res.raise_for_status()
        res_data = res.json()
        
    # 6. Spara i cache
    ttl = k.cache_ttl
    expires_at = now + ttl
    conn.execute(
        "REPLACE INTO http_cache (key, data, expires_at) VALUES (?, ?, ?)",
        (cache_key, json.dumps(res_data), expires_at)
    )
    conn.commit()
    
    return res_data
