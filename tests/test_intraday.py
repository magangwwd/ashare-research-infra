from datetime import date

import pandas as pd
import pytest

from data_pipeline.intraday import aggregate_daily, validate_bars
from data_pipeline.manifest import atomic_json, read_json, read_session
from data_pipeline.run_task01 import Pipeline


def test_15min_units_aggregate_back_to_daily_contract(provider):
    raw = provider.pro_bar(
        ts_code="600000.SH",
        start_date="2026-10-02 09:00:00",
        end_date="2026-10-02 16:00:00",
        freq="15min",
    )
    clean = validate_bars(raw, expected_day=date(2026, 10, 2))
    daily = aggregate_daily(clean, date(2026, 10, 2))
    row = daily.iloc[0]
    assert row["open"] == pytest.approx(10.0)
    assert row["high"] == pytest.approx(11.0)
    assert row["low"] == pytest.approx(9.0)
    assert row["close"] == pytest.approx(10.5)
    # minute vol is shares and amount is CNY; daily uses lots and thousand CNY.
    assert row["vol"] == pytest.approx(1000.0)
    assert row["amount"] == pytest.approx(1050.0)
    assert pd.isna(row["pre_close"])


def test_mainboard_minutes_do_not_replace_full_market_daily(tmp_path, config, client, provider, as_of):
    config["intraday"].update(
        enabled=True,
        recent_requests_per_run=100,
        history_requests_per_run=0,
        daily_validation_sample=0,
        min_security_coverage=0.95,
    )
    pipeline = Pipeline(tmp_path, config, client, as_of)
    pipeline.bootstrap()
    pipeline.intraday_recent()

    state = read_json(tmp_path / "metadata/manifest/intraday.json")
    assert state["activated"] is False
    assert state["coverage"] == pytest.approx(1.0)
    assert state["scope"] == "SSE_SZSE_MAIN_A_SHARE"
    assert state["daily_source"] == "tushare_daily"
    latest = pipeline.sessions[-1]
    assert read_session(pipeline.stage, "daily", latest).empty
    pipeline.critical_recent()
    daily = read_session(pipeline.stage, "daily", latest)
    assert len(daily) == len(provider.stocks)
    assert daily["close"].eq(10.5).all()


def test_mainboard_minutes_do_not_block_official_daily_download(tmp_path, config, client, provider, as_of):
    config["intraday"].update(
        enabled=True,
        recent_requests_per_run=100,
        history_requests_per_run=0,
        daily_validation_sample=0,
    )
    pipeline = Pipeline(tmp_path, config, client, as_of)
    pipeline.bootstrap()
    pipeline.intraday_recent()
    before = len(provider.calls)
    latest = pipeline.sessions[-1]
    pipeline.critical_recent(sample=False)
    new_calls = provider.calls[before:]

    # Restricting minute data to mainboard cannot substitute it for the
    # Critical all-A-share daily source.
    assert any(
        endpoint == "daily"
        and params.get("trade_date") == latest.strftime("%Y%m%d")
        and "ts_code" not in params
        for endpoint, params in new_calls
    )


def test_intraday_history_is_bounded_to_2020_or_later(tmp_path, config, client, provider, as_of):
    config["intraday"].update(enabled=True, history_start="20200101")
    pipeline = Pipeline(tmp_path, config, client, as_of)
    pipeline.bootstrap()
    requests = list(pipeline.intraday_history_requests())
    assert requests
    assert all(p["start_date"] >= "2020-01-01 09:00:00" for p in requests)
    assert all(p["freq"] == "15min" for p in requests)


def test_minute_permission_failure_does_not_activate_source(tmp_path, config, provider, as_of):
    from data_pipeline.download import TushareClient

    config["intraday"]["enabled"] = True
    provider.fail.add("stk_mins")
    client = TushareClient(config, provider, sleeper=lambda _: None)
    pipeline = Pipeline(tmp_path, config, client, as_of)
    pipeline.bootstrap()
    pipeline.intraday_recent()
    state = read_json(tmp_path / "metadata/manifest/intraday.json")
    assert state["activated"] is not True
    assert state["available"] is False


def test_intraday_history_records_pending_and_completion(tmp_path, config, client, provider, as_of, monkeypatch):
    config["intraday"].update(enabled=True, history_requests_per_run=2)
    pipeline = Pipeline(tmp_path, config, client, as_of)
    pipeline.bootstrap()
    pipeline.capabilities.setdefault("interfaces", {})["stk_mins"] = {"status": "AVAILABLE"}
    requests = [
        {"ts_code": "600000.SH", "freq": "15min", "start_date": "2026-01-01 09:00:00", "end_date": "2026-12-31 16:00:00"},
        {"ts_code": "600001.SH", "freq": "15min", "start_date": "2026-01-01 09:00:00", "end_date": "2026-12-31 16:00:00"},
        {"ts_code": "600002.SH", "freq": "15min", "start_date": "2026-01-01 09:00:00", "end_date": "2026-12-31 16:00:00"},
    ]
    done = set()
    monkeypatch.setattr(pipeline, "intraday_history_requests", lambda: iter(requests))
    monkeypatch.setattr(pipeline, "complete", lambda name, params: params["ts_code"] in done)

    def fake_fetch(params, expected_day=None):
        done.add(params["ts_code"])
        return 100
    monkeypatch.setattr(pipeline, "fetch_minutes", fake_fetch)

    pipeline.intraday_history()
    state = read_json(tmp_path / "metadata/manifest/intraday.json")
    assert state["history_request_universe"] == 3
    assert state["history_complete_requests"] == 2
    assert state["history_pending_requests"] == 1
    assert state["history_complete"] is False

    pipeline.intraday_history()
    state = read_json(tmp_path / "metadata/manifest/intraday.json")
    assert state["history_complete_requests"] == 3
    assert state["history_pending_requests"] == 0
    assert state["history_complete_ratio"] == pytest.approx(1.0)
    assert state["history_complete"] is True
    assert state["history_finished_at"]

def test_minute_permission_loss_keeps_full_market_daily_available(tmp_path, config, client, provider, as_of):
    config["intraday"].update(enabled=True, recent_requests_per_run=100, history_requests_per_run=0)
    pipeline = Pipeline(tmp_path, config, client, as_of)
    pipeline.bootstrap()
    latest = pipeline.sessions[-1]
    atomic_json(pipeline.store.metadata / "intraday.json", {
        "activated": True, "activation_date": str(latest)})
    provider.fail.add("stk_mins")
    pipeline.intraday_recent()
    status = read_json(pipeline.store.metadata / "intraday.json")
    assert status["minute_source_healthy"] is False
    assert status["daily_source"] == "tushare_daily"
    pipeline.critical_recent()
    assert len(read_session(pipeline.stage, "daily", latest)) == len(provider.stocks)
    assert len(read_session(pipeline.stage, "daily_basic", latest)) == len(provider.stocks)
    assert not (pipeline.store.metadata / "daily_source_fallback.json").exists()


def test_failed_official_daily_does_not_pass_minute_fallback(tmp_path, config, client, provider, as_of):
    from data_pipeline.validate import ValidationError

    config["intraday"].update(enabled=True, recent_requests_per_run=100,
                              history_requests_per_run=0, daily_validation_sample=0)
    pipeline = Pipeline(tmp_path, config, client, as_of)
    pipeline.bootstrap()
    atomic_json(pipeline.store.metadata / "intraday.json", {
        "activated": True, "activation_date": str(pipeline.sessions[-1])})
    provider.fail.update({"stk_mins", "daily"})
    pipeline.intraday_recent()
    with pytest.raises(ValidationError, match="daily"):
        pipeline.critical_recent()
    assert not read_session(pipeline.stage, "daily", pipeline.sessions[-1]).shape[0]


def test_mainboard_feed_skips_non_mainboard_stocks(tmp_path, config, client, provider, as_of):
    # Mixed real exchange/board metadata; never decide board from code prefixes.
    extra = [
        ("300001.SZ", "创业板", "SZSE"),
        ("688001.SH", "科创板", "SSE"),
        ("830001.BJ", "北交所", "BSE"),
        ("000001.SZ", "主板", "SZSE"),
    ]
    for code, market, exchange in extra:
        row = provider.stocks.iloc[0].copy()
        row["ts_code"] = code
        row["symbol"] = code.split(".")[0]
        row["market"] = market
        row["exchange"] = exchange
        row["name"] = market + code
        provider.stocks = pd.concat([provider.stocks, pd.DataFrame([row])], ignore_index=True)

    config["intraday"].update(enabled=True, recent_requests_per_run=100,
                              history_requests_per_run=0, daily_validation_sample=0)
    pipeline = Pipeline(tmp_path, config, client, as_of)
    pipeline.bootstrap()
    pipeline.intraday_recent()
    recent_calls = [p["ts_code"] for name, p in provider.calls if name == "stk_mins"]
    assert "000001.SZ" in recent_calls  # SZSE mainboard must be included
    assert "600000.SH" in recent_calls  # SSE mainboard must be included
    assert "300001.SZ" not in recent_calls
    assert "688001.SH" not in recent_calls
    assert "830001.BJ" not in recent_calls
    state = read_json(tmp_path / "metadata/manifest/intraday.json")
    assert state["expected_securities"] == len(provider.stocks) - 3
    assert state["coverage"] == pytest.approx(1.0)
    assert state["activated"] is False
    assert state["daily_source"] == "tushare_daily"

    history_codes = {p["ts_code"] for p in pipeline.intraday_history_requests()}
    assert "000001.SZ" in history_codes
    assert "300001.SZ" not in history_codes
    assert "688001.SH" not in history_codes
    assert "830001.BJ" not in history_codes


def test_mainboard_historical_delisted_is_included_only_in_its_listing_window(tmp_path, config, client, provider, as_of):
    row = provider.stocks.iloc[0].copy()
    row["ts_code"] = "600999.SH"
    row["symbol"] = "600999"
    row["list_status"] = "D"
    row["list_date"] = "20170101"
    row["delist_date"] = "20250101"
    provider.stocks = pd.concat([provider.stocks, pd.DataFrame([row])], ignore_index=True)
    pipeline = Pipeline(tmp_path, config, client, as_of)
    pipeline.bootstrap()
    from data_pipeline.clean import intraday_mainboard_codes
    assert "600999.SH" not in intraday_mainboard_codes(pipeline.master, as_of)
    assert "600999.SH" in intraday_mainboard_codes(pipeline.master, date(2024, 10, 2))
    assert "600999.SH" not in intraday_mainboard_codes(pipeline.master, date(2025, 10, 2))
    history = [p for p in pipeline.intraday_history_requests() if p["ts_code"] == "600999.SH"]
    assert history
    assert all(p["start_date"] >= "2020-01-01 09:00:00" for p in history)
    assert all(p["start_date"] < "2026-01-01 09:00:00" for p in history)
