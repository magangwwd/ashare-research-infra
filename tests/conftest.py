from copy import deepcopy
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from data_pipeline.download import TushareClient
from data_pipeline.run_task01 import load_config


@pytest.fixture
def config():
    result = deepcopy(load_config(Path(__file__).parents[1] / "config/config.yaml"))
    result["download"].update(history_start="20260901", history_requests_per_run=0,
                              repair_requests_per_run=0, audit_requests_per_run=0,
                              lookback_trading_days=2, daily_basic_lookback_trading_days=3,
                              required_recent_requests_per_run=0, optional_recent_requests_per_run=0)
    result["validation"]["min_daily_rows"] = 1
    result["datasets"].update(required=False, optional=False)
    return result


class FakeTushare:
    """Recorded-shape API substitute; never claims live permission verification."""
    def __init__(self, count=20, missing=None, fail=None):
        self.count, self.missing, self.fail = count, missing or {}, fail or set()
        self.calls = []
        self.stocks = pd.DataFrame([{
            "ts_code": f"{600000+i:06d}.SH", "symbol": f"{600000+i:06d}", "name": f"Stock {i}",
            "area": "上海", "industry": "银行", "market": "主板", "exchange": "SSE",
            "list_status": "L", "list_date": "20000101", "delist_date": None,
        } for i in range(count)])

    def query(self, endpoint, **params):
        self.calls.append((endpoint, dict(params)))
        if endpoint in self.fail:
            raise RuntimeError("permission denied")
        if endpoint == "stock_basic":
            frame = self.stocks.loc[self.stocks["list_status"] == params.get("list_status", "L")]
            if "ts_code" in params:
                frame = frame.loc[frame["ts_code"] == params["ts_code"]]
        elif endpoint == "trade_cal":
            start, end = pd.Timestamp(params["start_date"]), pd.Timestamp(params["end_date"])
            frame = pd.DataFrame([{"exchange": params.get("exchange", "SSE"), "cal_date": d.strftime("%Y%m%d"),
                                   "is_open": int(d.weekday() < 5), "pretrade_date": (d-pd.offsets.BDay()).strftime("%Y%m%d")}
                                  for d in pd.date_range(start, end)])
        elif endpoint in ("daily", "daily_basic", "adj_factor"):
            codes = list(self.stocks["ts_code"])
            if "ts_code" in params:
                codes = [params["ts_code"]]
            codes = [c for c in codes if c not in self.missing.get(endpoint, set())]
            frame = pd.DataFrame([{"ts_code": c, "trade_date": params["trade_date"],
                                   "open": 10., "high": 11., "low": 9., "close": 10.5, "pre_close": 10.,
                                   "vol": 1000., "amount": 1050., "adj_factor": 2.,
                                   "total_share": 10000., "total_mv": 105000.} for c in codes])
        else:
            raise RuntimeError("permission denied")
        offset, limit = params.get("offset", 0), params.get("limit", len(frame))
        return frame.iloc[offset:offset+limit].reset_index(drop=True)


@pytest.fixture
def provider():
    return FakeTushare()


@pytest.fixture
def client(config, provider):
    return TushareClient(config, provider, sleeper=lambda _: None)


@pytest.fixture
def as_of():
    return date(2026, 10, 2)
