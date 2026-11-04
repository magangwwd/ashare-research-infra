"""Minimal deterministic regressions before running the live-data ETF experiments."""
import unittest
import numpy as np
import pandas as pd
from etf_single_rotation.experiments.run_strategy_experiments import (
    build_monthly_targets, metrics, out_of_sample_metrics, run_one
)


class RegressionTests(unittest.TestCase):
    def test_monthly_cash_signal_clears_old_position(self):
        dates = pd.bdate_range("2020-01-01", periods=520)
        rising = 100 * np.power(1.006, np.arange(240))
        falling = rising[-1] * np.power(0.988, np.arange(1, 281))
        prices = pd.DataFrame({"510300.SH": np.r_[rising, falling]}, index=dates)
        targets, signals = build_monthly_targets(prices, "trend_filter")
        seen_invested = False
        checked = False
        for signal in signals:
            if signal["target"] != "CASH":
                seen_invested = True
            if seen_invested and signal["target"] == "CASH":
                d = pd.Timestamp(signal["signal_date"])
                later = dates[dates > d]
                if len(later):
                    self.assertAlmostEqual(float(targets.loc[later[0]].sum()), 0.0)
                    checked = True
                    break
        self.assertTrue(checked, "Synthetic prices must produce invested -> cash transition")

    def test_cap75_applies_to_gold_as_well(self):
        dates = pd.bdate_range("2019-01-01", periods=470)
        t = np.arange(len(dates))
        stocks = 100 * np.power(1.004, np.minimum(t, 230)) * np.power(0.991, np.maximum(0, t - 230))
        gold = 100 * np.power(1.0018, t)
        prices = pd.DataFrame({"510300.SH": stocks, "518880.SH": gold}, index=dates)
        targets, signals = build_monthly_targets(prices, "regime_switch_cap75")
        self.assertTrue(any(s["target"] == "518880.SH" for s in signals))
        self.assertLessEqual(float(targets.max().max()), 0.750000001)

    def test_holdout_uses_actual_net_equity(self):
        dates = pd.bdate_range("2020-01-01", periods=820)
        t = np.arange(len(dates))
        prices = pd.DataFrame({"510300.SH": 100 * np.power(1.001, t)}, index=dates)
        _, full_equity, _, _ = run_one(prices, "trend_filter")
        summary = out_of_sample_metrics(prices, "trend_filter", "2022-01-01")
        period = full_equity.loc[full_equity.index >= "2022-01-01"]
        expected = float(period.iloc[-1] / period.iloc[0] - 1.0)
        self.assertAlmostEqual(summary["total_return"], expected, places=10)
        self.assertEqual(summary["status"], "OOS_PRELIMINARY")

    def test_drawdown_limit_is_25_percent(self):
        dates = pd.to_datetime(["2020-01-01", "2021-01-01", "2022-01-01"])
        w = pd.DataFrame({"510300.SH": [1.0, 1.0, 1.0]}, index=dates)
        eq_ok = pd.Series([1.0, 0.76, 1.10], index=dates)
        eq_fail = pd.Series([1.0, 0.74, 1.10], index=dates)
        r = pd.Series([0.0, -0.24, 1.10/0.76-1], index=dates)
        self.assertEqual(metrics(eq_ok, r, w, "test")["acceptance"], "PRELIMINARY_ONLY")
        self.assertEqual(metrics(eq_fail, r, w, "test")["acceptance"], "FAIL_DRAWDOWN")


if __name__ == "__main__":
    unittest.main()
