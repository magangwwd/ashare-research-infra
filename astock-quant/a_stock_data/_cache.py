"""Disk cache for AStockDataClient.

Layout:  a_stock_data/_cache_store/{prefix}/{safe_key}.csv
Key TTL checked by file mtime; no background eviction needed for research use.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

import pandas as pd

_CACHE_ROOT = Path(__file__).resolve().parent / "_cache_store"


def _path(key: str) -> Path:
    # first 2 chars of md5 as bucket prefix to avoid flat-dir clutter
    digest = hashlib.md5(key.encode()).hexdigest()
    safe = key[:60].replace("/", "_").replace(":", "_").replace(" ", "_")
    return _CACHE_ROOT / digest[:2] / f"{safe}.csv"


def is_fresh(key: str, max_age_hours: float) -> bool:
    p = _path(key)
    if not p.exists():
        return False
    age_hours = (datetime.now().timestamp() - p.stat().st_mtime) / 3600
    return age_hours < max_age_hours


def read(key: str) -> pd.DataFrame | None:
    p = _path(key)
    if not p.exists():
        return None
    try:
        return pd.read_csv(p, encoding="utf-8-sig", low_memory=False)
    except Exception:
        return None


def write(key: str, df: pd.DataFrame) -> None:
    p = _path(key)
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(p, index=False, encoding="utf-8-sig")
