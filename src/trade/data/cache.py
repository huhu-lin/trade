"""On-disk cache + per-source rate limiting.

Free APIs are rate-limited and sometimes flaky, so every fetch goes through here:
  - RateLimiter enforces a minimum interval between calls to the same source.
  - parquet_cache stores DataFrames keyed by a stable hash, with a TTL so we don't
    re-hit the API for data that changes at most daily.

The cache only ever returns data that was actually fetched; a miss returns None and
the caller fetches fresh. We never synthesise rows.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

import pandas as pd

from trade.settings import CACHE_DIR


class RateLimiter:
    """Thread-safe minimum-interval limiter.

    SEC allows ~10 req/s; FinMind ~1500/hr. We keep a conservative interval per source
    rather than a full token bucket — for weekly batch screening this is ample.
    """

    def __init__(self, min_interval_seconds: float) -> None:
        self._min_interval = min_interval_seconds
        self._last_call = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_call
            sleep_for = self._min_interval - elapsed
            if sleep_for > 0:
                time.sleep(sleep_for)
            self._last_call = time.monotonic()


def _key_hash(source: str, dataset: str, params: dict) -> str:
    blob = json.dumps({"source": source, "dataset": dataset, "params": params}, sort_keys=True)
    digest = hashlib.sha1(blob.encode()).hexdigest()[:16]
    return digest


def _cache_path(source: str, dataset: str, params: dict) -> Path:
    return CACHE_DIR / source / f"{dataset}_{_key_hash(source, dataset, params)}.parquet"


def cache_get(source: str, dataset: str, params: dict, ttl_hours: float) -> pd.DataFrame | None:
    path = _cache_path(source, dataset, params)
    if not path.exists():
        return None
    age_hours = (time.time() - path.stat().st_mtime) / 3600
    if age_hours > ttl_hours:
        return None
    try:
        return pd.read_parquet(path)
    except Exception:
        return None


def cache_put(source: str, dataset: str, params: dict, df: pd.DataFrame) -> None:
    path = _cache_path(source, dataset, params)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
