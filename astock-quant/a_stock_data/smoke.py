"""Quick smoke test for AStockDataClient.

Run from repo root with the data-source venv:
    .venv-data-source/Scripts/python -m a_stock_data.smoke

Tests one stock per interface, prints pass/fail, exits 1 on any error.
Does NOT test cninfo by default (unreliable in batch).
"""
from __future__ import annotations

import sys
import time
from datetime import datetime

from a_stock_data import AStockDataClient

_TS = "000001.SZ"
_TS_SH = "600000.SH"


def _check(label: str, fn: object, *args: object, **kwargs: object) -> bool:
    t0 = time.perf_counter()
    try:
        result = fn(*args, **kwargs)  # type: ignore[operator]
        rows = len(result) if hasattr(result, "__len__") else "?"
        ms = int((time.perf_counter() - t0) * 1000)
        print(f"  OK  {label:40s} rows={rows:>5}  {ms:>6}ms")
        return True
    except Exception as exc:
        ms = int((time.perf_counter() - t0) * 1000)
        print(f"  !!  {label:40s} ERROR: {exc!s:.80}  {ms:>6}ms")
        return False


def main() -> int:
    client = AStockDataClient(cache_ttl_hours=24, research_max_rows=50)
    print(f"smoke: {datetime.now().isoformat(timespec='seconds')}\n")

    results = [
        _check("get_daily_bars (mootdx→cache)",   client.get_daily_bars,       _TS, offset=10),
        _check("get_daily_bars (cache hit)",        client.get_daily_bars,       _TS, offset=10),
        _check("get_realtime_quote (batch)",        client.get_realtime_quote,   [_TS, _TS_SH]),
        _check("get_hist (akshare→cache)",          client.get_hist,             _TS, "2026-04-01", "2026-05-05"),
        _check("get_hist (cache hit)",              client.get_hist,             _TS, "2026-04-01", "2026-05-05"),
        _check("get_finance_snapshot (mootdx F10)", client.get_finance_snapshot, _TS),
        _check("get_xdxr (mootdx)",                 client.get_xdxr,             _TS_SH),
        _check("get_research_reports (akshare)",    client.get_research_reports, _TS),
        _check("get_stock_news (akshare)",          client.get_stock_news,       _TS_SH),
        _check("get_global_news cls",               client.get_global_news,      "cls"),
    ]

    passed = sum(results)
    total = len(results)
    print(f"\n{passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
