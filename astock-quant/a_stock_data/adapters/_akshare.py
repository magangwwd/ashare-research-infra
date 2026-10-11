"""akshare adapter: historical K-line, research reports, news, global news."""
from __future__ import annotations

from datetime import datetime

import pandas as pd


def _bare(ts_code: str) -> str:
    return ts_code.split(".")[0]


def _tag(df: pd.DataFrame, ts_code: str | None, source: str) -> pd.DataFrame:
    df = df.copy()
    if ts_code:
        df["ts_code"] = ts_code
    df["source"] = source
    df["fetched_at"] = datetime.now().isoformat(timespec="seconds")
    return df


def get_hist(ts_code: str, start: str, end: str, adjust: str = "") -> pd.DataFrame:
    """Historical daily K-line.  start/end: 'YYYY-MM-DD' or 'YYYYMMDD'."""
    import akshare as ak  # type: ignore[import]

    start_fmt = start.replace("-", "")
    end_fmt = end.replace("-", "")
    df = ak.stock_zh_a_hist(
        symbol=_bare(ts_code),
        period="daily",
        start_date=start_fmt,
        end_date=end_fmt,
        adjust=adjust,
    )
    return _tag(df, ts_code, "akshare")


def get_research(ts_code: str, max_rows: int = 100) -> pd.DataFrame:
    """Research report metadata (East Money).

    max_rows caps pagination: heavy stocks can have 250+ reports and take 80s+.
    Rows are returned newest-first by the API.
    """
    import akshare as ak  # type: ignore[import]

    df = ak.stock_research_report_em(symbol=_bare(ts_code))
    if len(df) > max_rows:
        df = df.head(max_rows)
    return _tag(df, ts_code, "akshare")


def get_news(ts_code: str) -> pd.DataFrame:
    """Individual stock news (East Money)."""
    import akshare as ak  # type: ignore[import]

    df = ak.stock_news_em(symbol=_bare(ts_code))
    return _tag(df, ts_code, "akshare")


def get_global_news_cls() -> pd.DataFrame:
    """Caixin/CLS financial wire (财联社电报)."""
    import akshare as ak  # type: ignore[import]

    df = ak.stock_info_global_cls(symbol="全部")
    return _tag(df, None, "akshare_cls")


def get_global_news_em() -> pd.DataFrame:
    """East Money global financial news."""
    import akshare as ak  # type: ignore[import]

    df = ak.stock_info_global_em()
    return _tag(df, None, "akshare_em")
