"""iwencai / pywencai adapter: natural-language research semantic search.

Requires:
  - pywencai>=0.13.1 (already in requirements-smoke.txt)
  - IWENCAI_COOKIE env var (browser session cookie from www.iwencai.com)

The cookie is a session credential that expires; callers should catch
RuntimeError("IWENCAI_COOKIE not set") and degrade gracefully.
"""
from __future__ import annotations

import os
from datetime import datetime

import pandas as pd


def _cookie() -> str:
    val = os.environ.get("IWENCAI_COOKIE", "")
    if not val:
        raise RuntimeError(
            "IWENCAI_COOKIE is not set. "
            "Log in to www.iwencai.com, copy the 'cookie:' request header value, "
            "then set: $env:IWENCAI_COOKIE='<value>'"
        )
    return val


def search(query: str) -> pd.DataFrame:
    """Natural-language stock screen via iwencai.

    Examples:
        search("近一月机构评级上调的银行股")
        search("近三个月研报覆盖数最多的白酒股")

    Returns a DataFrame whose columns depend on the query phrasing.
    Always includes '股票代码' and '股票简称' when results exist.
    Raises RuntimeError if IWENCAI_COOKIE is not set or has expired.
    """
    import pywencai  # type: ignore[import]

    cookie = _cookie()
    df = pywencai.get(query=query, cookie=cookie)
    if not isinstance(df, pd.DataFrame):
        df = pd.DataFrame(df) if df else pd.DataFrame()
    df = df.copy()
    df["query"] = query
    df["source"] = "iwencai"
    df["fetched_at"] = datetime.now().isoformat(timespec="seconds")
    return df
