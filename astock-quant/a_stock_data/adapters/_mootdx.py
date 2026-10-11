"""mootdx adapter: daily bars, batch quotes, F10 finance, ex-dividend."""
from __future__ import annotations

from datetime import datetime

import pandas as pd


def _bare(ts_code: str) -> str:
    return ts_code.split(".")[0]


def _client() -> object:
    from mootdx.quotes import Quotes  # type: ignore[import]
    return Quotes.factory(market="std", multithread=False)


def _tag(df: pd.DataFrame, ts_code: str, source: str = "mootdx") -> pd.DataFrame:
    df = df.copy()
    df["ts_code"] = ts_code
    df["source"] = source
    df["fetched_at"] = datetime.now().isoformat(timespec="seconds")
    return df


def get_bars(ts_code: str, offset: int = 20) -> pd.DataFrame:
    """Daily K-line (frequency=9) for a single stock."""
    client = _client()
    df = client.bars(symbol=_bare(ts_code), frequency=9, offset=offset)
    return _tag(df, ts_code)


def get_quotes_batch(ts_codes: list[str]) -> pd.DataFrame:
    """Batch real-time quote + order book for up to ~100 stocks at once."""
    client = _client()
    symbols = [_bare(c) for c in ts_codes]
    df = client.quotes(symbol=symbols)
    df = df.copy()
    df["source"] = "mootdx"
    df["fetched_at"] = datetime.now().isoformat(timespec="seconds")
    return df


def get_finance(ts_code: str) -> pd.DataFrame:
    """F10 financial summary snapshot."""
    client = _client()
    df = client.finance(symbol=_bare(ts_code))
    return _tag(df, ts_code)


def get_xdxr(ts_code: str) -> pd.DataFrame:
    """Ex-dividend / ex-rights / capital change history."""
    client = _client()
    df = client.xdxr(symbol=_bare(ts_code))
    return _tag(df, ts_code)
