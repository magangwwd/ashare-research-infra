"""AStockDataClient: unified read-only access layer for A-stock data.

Phase-1 scope: research script use only.
Not imported by training pipeline, daily update flow, or report generation.

Usage:
    from a_stock_data import AStockDataClient

    client = AStockDataClient()
    bars   = client.get_daily_bars("000001.SZ", offset=20)
    quotes = client.get_realtime_quote(["000001.SZ", "600000.SH"])
    hist   = client.get_hist("000001.SZ", "2026-04-01", "2026-05-05")
    news   = client.get_stock_news("600000.SH")
    reports = client.get_research_reports("000001.SZ")
    items, blocked = client.get_announcements("000001.SZ")
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from . import _cache, _rate
from .adapters import _akshare, _cninfo, _iwencai, _mootdx


class AStockDataClient:
    """Read-only unified client for A-stock data sources.

    All inputs use Tushare ts_code format: '000001.SZ', '600000.SH'.
    All DataFrames include 'source' and 'fetched_at' columns.
    """

    def __init__(
        self,
        cache_ttl_hours: float = 24.0,
        research_max_rows: int = 100,
    ) -> None:
        self._ttl = cache_ttl_hours
        self._research_max = research_max_rows

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    def get_daily_bars(
        self,
        ts_code: str,
        offset: int = 20,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """Recent daily K-line via mootdx (fallback: akshare hist tail).

        offset: number of trading days to return (mootdx native parameter).
        Cached for cache_ttl_hours; use force_refresh=True to bypass.
        """
        key = f"bars_{ts_code}_{offset}"
        if not force_refresh and _cache.is_fresh(key, self._ttl):
            cached = _cache.read(key)
            if cached is not None:
                return cached

        _rate.wait("mootdx")
        try:
            df = _mootdx.get_bars(ts_code, offset=offset)
        except Exception:
            _rate.wait("akshare")
            from datetime import datetime
            end = datetime.today().strftime("%Y%m%d")
            df = _akshare.get_hist(ts_code, "20000101", end)
            if offset and len(df) > offset:
                df = df.tail(offset).reset_index(drop=True)

        _cache.write(key, df)
        return df

    def get_realtime_quote(self, ts_codes: list[str]) -> pd.DataFrame:
        """Batch real-time quote + order book via mootdx. Not cached."""
        _rate.wait("mootdx")
        return _mootdx.get_quotes_batch(ts_codes)

    def get_hist(
        self,
        ts_code: str,
        start: str,
        end: str,
        adjust: str = "",
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """Historical daily K-line via akshare.  start/end: 'YYYY-MM-DD'.

        Cached for cache_ttl_hours.  adjust: '' | 'qfq' | 'hfq'.
        """
        key = f"hist_{ts_code}_{start}_{end}_{adjust}"
        if not force_refresh and _cache.is_fresh(key, self._ttl):
            cached = _cache.read(key)
            if cached is not None:
                return cached

        _rate.wait("akshare")
        df = _akshare.get_hist(ts_code, start, end, adjust=adjust)
        _cache.write(key, df)
        return df

    # ------------------------------------------------------------------
    # Fundamentals
    # ------------------------------------------------------------------

    def get_finance_snapshot(
        self,
        ts_code: str,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """F10 financial summary via mootdx.  Cached for 7 × ttl (weekly)."""
        key = f"finance_{ts_code}"
        if not force_refresh and _cache.is_fresh(key, self._ttl * 7):
            cached = _cache.read(key)
            if cached is not None:
                return cached

        _rate.wait("mootdx")
        df = _mootdx.get_finance(ts_code)
        _cache.write(key, df)
        return df

    def get_xdxr(
        self,
        ts_code: str,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """Ex-dividend / ex-rights history via mootdx.  Cached weekly."""
        key = f"xdxr_{ts_code}"
        if not force_refresh and _cache.is_fresh(key, self._ttl * 7):
            cached = _cache.read(key)
            if cached is not None:
                return cached

        _rate.wait("mootdx")
        df = _mootdx.get_xdxr(ts_code)
        _cache.write(key, df)
        return df

    # ------------------------------------------------------------------
    # Research
    # ------------------------------------------------------------------

    def get_research_reports(
        self,
        ts_code: str,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        """Research report metadata via akshare (East Money).

        Capped at research_max_rows (default 100) to prevent runaway
        pagination — heavy stocks can have 250+ reports and take 80s+.
        Rows are newest-first.  Cached for cache_ttl_hours.
        """
        key = f"research_{ts_code}_{self._research_max}"
        if not force_refresh and _cache.is_fresh(key, self._ttl):
            cached = _cache.read(key)
            if cached is not None:
                return cached

        _rate.wait("akshare")
        df = _akshare.get_research(ts_code, max_rows=self._research_max)
        _cache.write(key, df)
        return df

    # ------------------------------------------------------------------
    # News
    # ------------------------------------------------------------------

    def get_stock_news(self, ts_code: str) -> pd.DataFrame:
        """Individual stock news via akshare.  Not cached (fresh each call)."""
        _rate.wait("akshare")
        return _akshare.get_news(ts_code)

    def get_global_news(self, source: str = "cls") -> pd.DataFrame:
        """Global financial news wire.

        source: 'cls' (财联社, default) | 'em' (East Money)
        Not cached.
        """
        _rate.wait("akshare")
        if source == "em":
            return _akshare.get_global_news_em()
        return _akshare.get_global_news_cls()

    # ------------------------------------------------------------------
    # Announcements
    # ------------------------------------------------------------------

    def get_announcements(
        self,
        ts_code: str,
        page_size: int = 30,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Public announcements via cninfo (single-trigger; do not batch-poll).

        Returns:
            items      list of announcement dicts
            is_blocked True when cninfo returned HTTP 200 but empty list.

        Callers MUST check is_blocked before treating empty items as "no
        announcements exist".  Not cached — announcement lists change frequently
        and cninfo's soft-block behavior makes stale cache harmful.
        """
        _rate.wait("cninfo")
        return _cninfo.get_announcements(ts_code, page_size=page_size)

    def reset_cninfo_session(self) -> None:
        """Force rebuild of the cninfo HTTP session on the next call."""
        _cninfo.reset_session()

    # ------------------------------------------------------------------
    # Research semantic search
    # ------------------------------------------------------------------

    def search_research(self, query: str) -> pd.DataFrame:
        """Natural-language stock screen via iwencai.

        Requires IWENCAI_COOKIE env var (browser session cookie).
        Raises RuntimeError if the cookie is absent or has expired.
        Not cached — query results change daily.

        Examples:
            client.search_research("近一月机构评级上调的银行股")
            client.search_research("近三个月研报覆盖数最多的白酒股")
        """
        _rate.wait("akshare")   # iwencai shares the akshare rate slot (both ~1s)
        return _iwencai.search(query)
