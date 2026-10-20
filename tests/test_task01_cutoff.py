"""Date-boundary regressions for delayed Task 01 launches."""
from datetime import datetime, date
from zoneinfo import ZoneInfo

import pandas as pd
from data_pipeline.run_task01 import resolved_as_of
from data_pipeline.validate import validation_as_of
from data_pipeline.download import TushareClient


def shanghai(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=ZoneInfo("Asia/Shanghai"))


def test_delayed_scheduled_run_selects_yesterday():
    # 23:50 on Oct 12 is delivered at 06:36 on Oct 13.
    assert resolved_as_of(shanghai("2026-10-13T06:36:00")) == date(2026, 10, 12)


def test_intraday_never_selects_unclosed_session():
    assert resolved_as_of(shanghai("2026-10-13T14:59:00")) == date(2026, 10, 12)
    assert resolved_as_of(shanghai("2026-10-13T17:59:59")) == date(2026, 10, 12)
    assert resolved_as_of(shanghai("2026-10-13T18:00:00")) == date(2026, 10, 13)
    assert resolved_as_of(shanghai("2026-10-13T23:50:00")) == date(2026, 10, 13)


def test_production_validation_uses_same_completed_session_cutoff():
    # Regression: scheduled Task 01 begins on Oct 20 at 06:59 and ingests Oct 19;
    # acceptance must not demand the not-yet-finished Oct 20 session.
    for timestamp in ("2026-10-20T06:59:00", "2026-10-20T07:31:00",
                      "2026-10-20T17:59:59", "2026-10-20T18:00:00"):
        now = shanghai(timestamp)
        assert validation_as_of(now) == resolved_as_of(now)
    assert validation_as_of(shanghai("2026-10-20T07:31:00")) == date(2026, 10, 19)
    assert validation_as_of(shanghai("2026-10-20T18:00:00")) == date(2026, 10, 20)


def test_timezone_and_invalid_cutoff_guard():
    from pytest import raises
    with raises(ValueError, match="timezone-aware"):
        resolved_as_of(datetime(2026, 10, 13, 6, 36))
    with raises(ValueError, match="16..23"):
        resolved_as_of(shanghai("2026-10-13T12:00:00"), 15)


def test_capability_audit_uses_actual_open_trade_calendar(config, tmp_path):
    requested = []

    class Probe:
        def query(self, endpoint, **params):
            if endpoint == "trade_cal":
                return pd.DataFrame({"cal_date": ["20261009", "20261010", "20261011", "20261012"],
                                     "is_open": [1, 0, 0, 1]})
            if endpoint in ("daily", "adj_factor", "daily_basic"):
                requested.append((endpoint, params.get("trade_date")))
                return pd.DataFrame({"ts_code": ["000001.SZ"]})
            return pd.DataFrame({"ts_code": ["000001.SZ"]})

    client = TushareClient(config, Probe(), sleeper=lambda _: None)
    result = client.audit(tmp_path, "20261012")
    assert result["probe_trade_date"] == "20261012"
    assert requested == [(name, "20261012") for name in ("daily", "adj_factor", "daily_basic")]


def test_calendar_closure_skips_weekend_in_critical_probes(config, tmp_path):
    requested = []

    class Probe:
        def query(self, endpoint, **params):
            if endpoint == "trade_cal":
                return pd.DataFrame({"cal_date": ["20261009", "20261010", "20261011"],
                                     "is_open": [1, 0, 0]})
            if endpoint in ("daily", "adj_factor", "daily_basic"):
                requested.append(params["trade_date"])
            return pd.DataFrame({"ts_code": ["000001.SZ"]})

    result = TushareClient(config, Probe(), sleeper=lambda _: None).audit(tmp_path, "20261011")
    assert result["probe_trade_date"] == "20261009"
    assert requested == ["20261009"] * 3
