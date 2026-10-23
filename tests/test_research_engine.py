from datetime import date

import pandas as pd
import pytest

from research_engine.backtest import BacktestConfig, run_backtest
from research_engine.factors import add_engineering_factors
from research_engine.prices import add_adjusted_prices
from research_engine.readiness import readiness_report
from research_engine.signals import rank_long_only
from research_engine.universe import filter_historical_universe
from research_engine.walkforward import walk_forward_splits


def _market_rows():
    return pd.DataFrame([
        {"security_id": "A", "trade_date": date(2026, 1, 2), "open": 10., "high": 11., "low": 9., "close": 10., "pre_close": 9.5, "adj_factor": 1., "vol": 100.},
        {"security_id": "A", "trade_date": date(2026, 1, 5), "open": 20., "high": 23., "low": 19., "close": 22., "pre_close": 10., "adj_factor": 1., "vol": 200.},
        {"security_id": "A", "trade_date": date(2026, 1, 6), "open": 22., "high": 24., "low": 21., "close": 23., "pre_close": 22., "adj_factor": 1., "vol": 150.},
    ])


def test_adjusted_price_does_not_use_future_endpoint_normalization():
    frame = _market_rows()
    first = add_adjusted_prices(frame)
    changed = frame.copy()
    changed.loc[changed["trade_date"] == date(2026, 1, 6), "adj_factor"] = 99.0
    second = add_adjusted_prices(changed)
    earlier = first["trade_date"] < date(2026, 1, 6)
    assert first.loc[earlier, "adj_close"].tolist() == second.loc[earlier, "adj_close"].tolist()


def test_historical_universe_keeps_future_delisted_stock_before_delisting():
    market = pd.DataFrame([
        {"security_id": "A", "trade_date": date(2025, 12, 30)},
        {"security_id": "A", "trade_date": date(2026, 1, 6)},
    ])
    master = pd.DataFrame([{
        "security_id": "A",
        "exchange": "SSE",
        "board": "MAIN",
        "security_type": "A_SHARE",
        "list_date": date(2020, 1, 1),
        "delist_date": date(2026, 1, 5),
        "listing_status": "D",
    }])
    result = filter_historical_universe(market, master)
    assert result["trade_date"].tolist() == [date(2025, 12, 30)]


def test_engineering_factor_is_backward_looking():
    dates = pd.bdate_range("2026-01-01", periods=25).date
    frame = pd.DataFrame({
        "security_id": ["A"] * len(dates),
        "trade_date": dates,
        "adj_close": [100 + i for i in range(len(dates))],
        "vol": [1000 + i for i in range(len(dates))],
    })
    base = add_engineering_factors(frame, momentum_windows=(5,), volatility_window=5, volume_window=5)
    changed = frame.copy()
    changed.loc[changed.index[-1], "adj_close"] = 9999.0
    later = add_engineering_factors(changed, momentum_windows=(5,), volatility_window=5, volume_window=5)
    cols = ["momentum_5d", "volatility_5d", "volume_ratio_5d"]
    pd.testing.assert_frame_equal(
        base.loc[base.index[:-1], cols].reset_index(drop=True),
        later.loc[later.index[:-1], cols].reset_index(drop=True),
    )


def test_signal_generated_at_close_executes_next_session_open():
    market = add_adjusted_prices(_market_rows())
    targets = pd.DataFrame([{
        "signal_date": date(2026, 1, 2),
        "security_id": "A",
        "target_weight": 1.0,
    }])
    result = run_backtest(market, targets, BacktestConfig(initial_cash=1_000_000))
    assert result.trades["trade_date"].tolist() == [date(2026, 1, 5)]
    assert result.trades["price"].iloc[0] == 20.0
    first = result.nav.loc[result.nav["trade_date"] == date(2026, 1, 2)].iloc[0]
    second = result.nav.loc[result.nav["trade_date"] == date(2026, 1, 5)].iloc[0]
    assert first["nav"] == pytest.approx(1_000_000)
    assert second["nav"] == pytest.approx(1_100_000)


def test_costs_reduce_nav_and_sell_tax_only_applies_to_sales():
    market = add_adjusted_prices(_market_rows())
    targets = pd.DataFrame([
        {"signal_date": date(2026, 1, 2), "security_id": "A", "target_weight": 1.0},
        {"signal_date": date(2026, 1, 5), "security_id": "A", "target_weight": 0.0},
    ])
    result = run_backtest(
        market,
        targets,
        BacktestConfig(initial_cash=1_000_000, commission_bps=10, slippage_bps=5, sell_tax_bps=10),
    )
    assert set(result.trades["side"]) == {"BUY", "SELL"}
    sell = result.trades.loc[result.trades["side"] == "SELL"].iloc[0]
    buy = result.trades.loc[result.trades["side"] == "BUY"].iloc[0]
    assert sell["cost"] / sell["notional"] == pytest.approx(0.0025)
    assert buy["cost"] / buy["notional"] == pytest.approx(0.0015)


def test_rank_long_only_is_deterministic_and_equal_weighted():
    frame = pd.DataFrame([
        {"trade_date": date(2026, 1, 2), "security_id": "B", "score": 2.0},
        {"trade_date": date(2026, 1, 2), "security_id": "A", "score": 3.0},
        {"trade_date": date(2026, 1, 2), "security_id": "C", "score": 1.0},
    ])
    result = rank_long_only(frame, "score", top_n=2)
    assert result["security_id"].tolist() == ["A", "B"]
    assert result["target_weight"].tolist() == [0.5, 0.5]


def test_walk_forward_is_chronological_and_nonoverlapping_inside_each_split():
    dates = list(pd.bdate_range("2025-01-01", periods=20).date)
    splits = walk_forward_splits(
        dates,
        train_sessions=8,
        validation_sessions=4,
        test_sessions=4,
        step_sessions=4,
    )
    assert len(splits) == 2
    for split in splits:
        assert max(split.train) < min(split.validation)
        assert max(split.validation) < min(split.test)
        assert not (set(split.train) & set(split.validation))
        assert not (set(split.validation) & set(split.test))


class _FakeResearchData:
    acceptance = {"as_of": "2026-01-08"}

    def __init__(self):
        self.common = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 8)]

    def load(self, name):
        dates = self.common if name != "adj_factor" else [
            date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 8)
        ]
        return pd.DataFrame({"trade_date": dates})

    def calendar(self):
        days = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7), date(2026, 1, 8)]
        return pd.DataFrame([
            {"exchange": exchange, "calendar_date": day, "is_open": 1}
            for day in days for exchange in ("SSE", "SZSE")
        ])


def test_readiness_requires_recent_consecutive_common_core_sessions():
    report = readiness_report(_FakeResearchData())
    # 2026-01-07 is missing from all core files, so only the latest date counts.
    assert report["latest_common_core_session"] == "2026-01-08"
    assert report["recent_consecutive_core_sessions"] == 1
    assert report["levels"]["engineering"]["ready"] is False


def _two_stock_market(extra=None):
    rows = []
    for sec, opens in {"A": [10., 10., 10., 10.], "B": [10., 10., 10., 10.]}.items():
        for day, price in zip(pd.bdate_range("2026-01-05", periods=4).date, opens):
            row = {"security_id": sec, "trade_date": day, "open": price, "high": price,
                   "low": price, "close": price, "pre_close": price, "adj_factor": 1., "vol": 100.}
            row.update((extra or {}).get((sec, day), {}))
            rows.append(row)
    return pd.DataFrame(rows)


def test_delisted_holding_is_written_off_instead_of_carried_forever():
    days = list(pd.bdate_range("2026-01-05", periods=4).date)
    market = add_adjusted_prices(_two_stock_market())
    # A stops quoting after the second session, as a delisted stock would.
    market = market.loc[~((market["security_id"] == "A") & (market["trade_date"] > days[1]))]
    targets = pd.DataFrame([{"signal_date": days[0], "security_id": "A", "target_weight": 1.0}])
    delistings = pd.DataFrame([{"security_id": "A", "delist_date": days[2]}])

    carried = run_backtest(market, targets)
    written_off = run_backtest(market, targets, delistings=delistings)

    assert carried.nav["nav"].iloc[-1] == pytest.approx(1_000_000)
    assert written_off.nav["nav"].iloc[-1] == pytest.approx(0.0)
    delist = written_off.trades.loc[written_off.trades["side"] == "DELIST"]
    assert delist["trade_date"].tolist() == [days[2]]


def test_delisting_recovery_ratio_credits_part_of_last_mark():
    days = list(pd.bdate_range("2026-01-05", periods=4).date)
    market = add_adjusted_prices(_two_stock_market())
    market = market.loc[~((market["security_id"] == "A") & (market["trade_date"] > days[1]))]
    targets = pd.DataFrame([{"signal_date": days[0], "security_id": "A", "target_weight": 1.0}])
    delistings = pd.DataFrame([{"security_id": "A", "delist_date": pd.Timestamp(days[2])}])
    result = run_backtest(market, targets, BacktestConfig(delisting_recovery_ratio=0.25), delistings)
    assert result.nav["nav"].iloc[-1] == pytest.approx(250_000)


def test_delisting_not_applied_while_security_still_quotes():
    days = list(pd.bdate_range("2026-01-05", periods=4).date)
    market = add_adjusted_prices(_two_stock_market())
    targets = pd.DataFrame([{"signal_date": days[0], "security_id": "A", "target_weight": 1.0}])
    delistings = pd.DataFrame([{"security_id": "A", "delist_date": days[1]}])
    result = run_backtest(market, targets, delistings=delistings)
    assert "DELIST" not in set(result.trades["side"])
    assert result.nav["nav"].iloc[-1] == pytest.approx(1_000_000)


def test_limit_up_open_blocks_buy_and_limit_down_open_blocks_sell():
    days = list(pd.bdate_range("2026-01-05", periods=4).date)
    limits = {}
    for sec in ("A", "B"):
        for day in days:
            limits[(sec, day)] = {"up_limit": 11.0, "down_limit": 9.0}
    limits[("A", days[1])] = {"up_limit": 10.0, "down_limit": 9.0}   # A opens at its up limit
    limits[("B", days[2])] = {"up_limit": 11.0, "down_limit": 10.0}  # B opens at its down limit
    market = add_adjusted_prices(_two_stock_market(limits))
    targets = pd.DataFrame([
        {"signal_date": days[0], "security_id": "A", "target_weight": 0.5},
        {"signal_date": days[0], "security_id": "B", "target_weight": 0.5},
        {"signal_date": days[1], "security_id": "A", "target_weight": 0.5},
        {"signal_date": days[1], "security_id": "B", "target_weight": 0.0},
    ])

    blocked = run_backtest(market, targets)
    bought = blocked.trades.loc[blocked.trades["side"] == "BUY"]
    sold = blocked.trades.loc[blocked.trades["side"] == "SELL"]
    # A opens at its up limit on days[1], so its first buy slips to days[2].
    assert bought.loc[bought["security_id"] == "A", "trade_date"].tolist() == [days[2]]
    assert bought.loc[bought["security_id"] == "B", "trade_date"].tolist() == [days[1]]
    # B's sell is attempted on days[2], when it opens at its down limit.
    assert sold.empty

    free = run_backtest(market, targets, BacktestConfig(enforce_price_limits=False))
    assert free.trades.loc[free.trades["side"] == "BUY", "security_id"].tolist() == ["A", "B"]
    assert free.trades.loc[free.trades["side"] == "SELL", "security_id"].tolist() == ["B"]


def test_missing_limit_columns_keep_previous_behavior():
    market = add_adjusted_prices(_two_stock_market())
    targets = pd.DataFrame([{"signal_date": date(2026, 1, 5), "security_id": "A", "target_weight": 1.0}])
    assert not run_backtest(market, targets).trades.empty


def test_backtest_rejects_nan_inf_and_duplicate_targets():
    market = add_adjusted_prices(_market_rows())
    day = date(2026, 1, 2)
    for bad in (float("nan"), float("inf")):
        targets = pd.DataFrame([{"signal_date": day, "security_id": "A", "target_weight": bad}])
        with pytest.raises(ValueError, match="finite"):
            run_backtest(market, targets)
    duplicated = pd.DataFrame([
        {"signal_date": day, "security_id": "A", "target_weight": 0.3},
        {"signal_date": day, "security_id": "A", "target_weight": 0.4},
    ])
    with pytest.raises(ValueError, match="duplicate"):
        run_backtest(market, duplicated)


def test_backtest_config_rejects_invalid_delisting_recovery():
    with pytest.raises(ValueError):
        BacktestConfig(delisting_recovery_ratio=1.5).validate()


class _TablesData:
    """CleanResearchData.market() against in-memory tables (no parquet needed)."""

    def __new__(cls, tables):
        from research_engine.data import CleanResearchData

        class Data(CleanResearchData):
            def __init__(self):
                self._tables = tables

            def load(self, name):
                return self._tables.get(name, pd.DataFrame())

        return Data()


def _core_tables(day=date(2026, 1, 5)):
    keys = {"security_id": ["A", "B"], "trade_date": [day, day]}
    return {
        "daily": pd.DataFrame({**keys, "open": [10., 10.], "close": [10., 10.]}),
        "adj_factor": pd.DataFrame({**keys, "adj_factor": [1., 1.]}),
        "daily_basic": pd.DataFrame({**keys, "turnover_rate": [1., 2.]}),
    }


def test_market_include_limits_left_joins_stk_limit_and_keeps_unmatched_rows():
    tables = _core_tables()
    tables["stk_limit"] = pd.DataFrame({
        "security_id": ["A"], "trade_date": [date(2026, 1, 5)],
        "up_limit": [11.0], "down_limit": [9.0],
    })
    frame = _TablesData(tables).market(include_limits=True)
    assert len(frame) == 2
    row_a = frame.loc[frame["security_id"] == "A"].iloc[0]
    row_b = frame.loc[frame["security_id"] == "B"].iloc[0]
    assert (row_a["up_limit"], row_a["down_limit"]) == (11.0, 9.0)
    assert pd.isna(row_b["up_limit"]) and pd.isna(row_b["down_limit"])


def test_market_include_limits_without_stk_limit_data_gives_nan_limits():
    frame = _TablesData(_core_tables()).market(include_limits=True)
    assert frame["up_limit"].isna().all() and frame["down_limit"].isna().all()
    assert "up_limit" not in _TablesData(_core_tables()).market().columns
