"""No-data offline tests for opt-in realistic-lot, fee and split-proxy stress.

All historical thresholds and frozen production V1 remain untouched.
"""
import unittest
import pandas as pd

from research_mainboard_daily_pit_v0 import (
    run_portfolio, paper_commission, paper_cent_price, INITIAL_CASH,
)


def fixture(*, split=False, bad_factor=False):
    days = pd.to_datetime(["2024-01-02", "2024-01-03"])
    factor = [1., 2. if split else 1.]
    if bad_factor:
        factor[0] = float("nan")
    raw_close = [10., 5. if split else 10.]
    raw_open = [10., 5. if split else 10.]
    raw_high = [10.2, 5.1 if split else 10.5]
    raw_low = [9.8, 4.9 if split else 9.4]
    x = pd.DataFrame({
        "open": raw_open, "high": raw_high, "low": raw_low,
        "close": raw_close, "adjustment_factor": factor,
    }, index=days)
    for col in ("open", "high", "low", "close"):
        x["p_" + col] = x[col] * x["adjustment_factor"]
    x["volume_shares"] = 1_000_000
    cand = {"symbol": "600000.SH", "signal_date": pd.Timestamp("2023-12-29"),
            "pattern": "BREAKOUT", "strength": 3.0, "signal_close": 10.,
            "atr": 0.2}
    return days, {"600000.SH": x}, {days[0]: [cand]}


def simulate(*, split=False, bad_factor=False, slippage=0.001, mode="lot100_minfee"):
    days, prices, scheduled = fixture(split=split, bad_factor=bad_factor)
    return run_portfolio(
        list(days), prices, scheduled, max_stop_pct=0.04,
        max_gap_up=0.01, risk_budget=0.0025, max_weight=0.10,
        execution_model=mode, execution_slippage=slippage,
    )


class LotExecutionInvariants(unittest.TestCase):
    def test_broker_fee_floor_and_tick_rounding(self):
        self.assertEqual(paper_commission(1_000, 0.0013), 5.)
        self.assertEqual(paper_commission(10_000, 0.0013), 13.)
        self.assertEqual(paper_commission(10_000, 0.0018), 18.)
        self.assertEqual(paper_cent_price(10.001, buy=True), 10.01)
        self.assertEqual(paper_cent_price(10.009, buy=False), 10.00)
        self.assertEqual(paper_cent_price(10.010, buy=True), 10.01)

    def test_entry_is_whole_100_share_lots_and_cash_is_conserved(self):
        trades, nav, counters, pending = simulate()
        self.assertEqual(len(trades), 1)
        trade = trades.iloc[0]
        self.assertEqual(trade["entry_raw_shares"] % 100, 0)
        self.assertGreaterEqual(trade["entry_raw_shares"], 100)
        self.assertEqual(trade["entry_date"], "2024-01-02")
        self.assertEqual(trade["exit_date"], "2024-01-03")
        self.assertLessEqual(trade["actual_entry_weight"] * trade["risk_pct"], .0025 + 1e-8)
        self.assertLessEqual(trade["actual_entry_weight"], .10 + 1e-8)
        self.assertTrue((nav["cash"] >= -1e-7).all())
        self.assertEqual(counters["entered"], 1)
        self.assertEqual(pending, 0)

    def test_previous_baseline_remains_identical_when_not_opted_in(self):
        days, prices, signals = fixture()
        a = run_portfolio(list(days), prices, signals, max_stop_pct=.04,
                          max_gap_up=.01, risk_budget=.0025, max_weight=.1)
        b = run_portfolio(list(days), prices, signals, max_stop_pct=.04,
                          max_gap_up=.01, risk_budget=.0025, max_weight=.1,
                          execution_model="legacy")
        pd.testing.assert_frame_equal(a[0], b[0])
        pd.testing.assert_frame_equal(a[1], b[1])
        self.assertEqual(a[2:], b[2:])

    def test_adjustment_factor_split_proxy_preserves_marked_nav(self):
        _, nav, counts, pending = simulate(split=True)
        self.assertEqual(counts["entered"], 1)
        self.assertEqual(pending, 1)
        self.assertAlmostEqual(nav["nav"].iloc[0], nav["nav"].iloc[1], places=5)

    def test_increased_slippage_does_not_improve_forced_loss(self):
        slow, _, _, _ = simulate(slippage=.003)
        fast, _, _, _ = simulate(slippage=.001)
        self.assertEqual(len(slow), 1)
        self.assertEqual(len(fast), 1)
        self.assertLessEqual(float(slow.iloc[0]["net_return"]), float(fast.iloc[0]["net_return"]))

    def test_bad_factor_and_bad_execution_settings_fail_closed(self):
        _, nav, counts, pending = simulate(bad_factor=True)
        self.assertEqual(counts.get("entered", 0), 0)
        self.assertEqual(pending, 0)
        self.assertAlmostEqual(float(nav.iloc[-1]["nav"]), INITIAL_CASH)
        with self.assertRaises(ValueError):
            simulate(slippage=0.01)
        with self.assertRaises(ValueError):
            simulate(mode="guaranteed_fill")

    def test_risk_model_is_still_t_plus_one(self):
        trades, _, _, _ = simulate()
        self.assertNotEqual(trades.iloc[0]["entry_date"], trades.iloc[0]["exit_date"])


if __name__ == "__main__":
    unittest.main()
