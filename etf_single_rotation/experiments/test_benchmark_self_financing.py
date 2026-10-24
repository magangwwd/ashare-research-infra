"""Synthetic, no-network tests of benchmark accounting and aligned holdout."""
from __future__ import annotations

import unittest
import pandas as pd

from etf_single_rotation.experiments.benchmark_self_financing import compare


class ComparableBenchmarkTests(unittest.TestCase):
    def test_benchmark_starts_at_holdout_and_pays_open_fee(self):
        dates = pd.date_range("2025-01-06", periods=5, freq="B")
        closes = pd.DataFrame({
            "510300.SH": [10, 10, 11, 12, 12],
            "518880.SH": [20, 20, 19, 19, 19],
        }, index=dates)
        opens = closes.copy()
        table, curves = compare(
            closes, opens, "2025-01-07", "2025-01-09", fee_rate=0.001
        )
        self.assertEqual(set(table.benchmark), {
            "cash_zero_interest", "etf_buy_hold_510300.SH", "etf_buy_hold_518880.SH"
        })
        self.assertTrue((table.executed_trade_legs == [0, 1, 1]).all())
        self.assertEqual(len(curves), 9)
        equity_index = curves.loc[curves.benchmark == "etf_buy_hold_510300.SH"]
        # A 10->12 price increase minus the one-time entry trading cost.
        self.assertAlmostEqual(
            float(equity_index.net_equity.iloc[-1]), 1.2 / 1.001, places=9
        )
        cash = table.loc[table.benchmark == "cash_zero_interest"].iloc[0]
        self.assertEqual(cash.cagr, 0.0)

    def test_first_session_missing_open_defers_buy_without_imputation(self):
        dates = pd.date_range("2025-01-06", periods=5, freq="B")
        closes = pd.DataFrame({"510300.SH": [10, 10, 10, 11, 11]}, index=dates)
        opens = closes.copy()
        opens.loc[dates[1], "510300.SH"] = float("nan")
        table, _ = compare(
            closes, opens, "2025-01-07", "2025-01-09",
            symbols=("510300.SH",),
        )
        benchmark = table.loc[table.benchmark == "etf_buy_hold_510300.SH"].iloc[0]
        self.assertEqual(benchmark.executed_trade_legs, 1)
        self.assertGreaterEqual(benchmark.blocked_sessions, 1)

    def test_rejects_benchmark_without_prior_day(self):
        dates = pd.date_range("2025-01-06", periods=3, freq="B")
        closes = pd.DataFrame({"510300.SH": [10, 10, 12]}, index=dates)
        with self.assertRaisesRegex(ValueError, "preceding day"):
            compare(closes, closes, str(dates[0].date()), str(dates[2].date()),
                    symbols=("510300.SH",))


if __name__ == "__main__":
    unittest.main()
