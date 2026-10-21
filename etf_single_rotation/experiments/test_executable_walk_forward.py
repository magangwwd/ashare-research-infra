"""Regression proof that monthly rebalancing and fold switches are truly executed."""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from etf_single_rotation.experiments.simulate_self_financing import (
    executable_walk_forward, simulate,
)
from etf_single_rotation.backtest import MAX_DRAWDOWN_ACCEPTANCE


class ExecutableWalkForwardTests(unittest.TestCase):
    def test_legacy_acceptance_target_is_25_percent(self):
        self.assertAlmostEqual(MAX_DRAWDOWN_ACCEPTANCE, 0.25)

    def test_two_day_half_etf_half_cash_correct_compounding(self):
        dates = pd.bdate_range("2020-01-02", periods=3)
        # 50% ETF + 50% cash. ETF +10% then -10%; no daily rebalance.
        opens = pd.DataFrame({"510300.SH": [100.0, 100.0, 110.0]}, index=dates)
        closes = pd.DataFrame({"510300.SH": [100.0, 110.0, 99.0]}, index=dates)
        targets = pd.DataFrame({"510300.SH": [0.5, 0.5, 0.5]}, index=dates)
        ledger, orders = simulate(closes, opens, targets, "unit_test", fee_rate=0.0)
        self.assertAlmostEqual(float(ledger.equity.iloc[-1]), 0.995, places=12)
        self.assertEqual(len(orders), 1)
        self.assertAlmostEqual(float(ledger.cash.iloc[-1]), 0.5, places=12)
        self.assertAlmostEqual(float(ledger.shares.iloc[-1]), 0.005, places=12)

    def test_initial_fold_trade_and_cost_are_included(self):
        dates = pd.bdate_range("2015-01-01", "2020-01-31")
        prices = pd.DataFrame({"510300.SH": 100.0}, index=dates)
        targets = pd.DataFrame({"510300.SH": 0.50}, index=dates)
        curve = pd.Series(1.001 ** np.arange(len(dates)), index=dates)
        folds = (("2019-01-02", "2019-02-28"),)
        with tempfile.TemporaryDirectory() as output:
            report = executable_walk_forward(
                prices, prices, {"A": targets}, {"A": curve},
                folds=folds, candidates=("A",),
                output_dir=Path(output), fee_rate=0.001
            )
            self.assertEqual(report["fold_count"], 1)
            self.assertEqual(report["folds"][0]["selected_strategy"], "A")
            self.assertEqual(report["executed_trade_legs"], 1)
            self.assertGreater(report["charged_fee_slippage"], 0)
            self.assertLess(report["stitched_executable_holdout"]["total_return"], 0)
            trades = pd.read_csv(Path(output) / "executable_walk_forward_trades.csv")
            self.assertEqual(len(trades), 1)
            self.assertEqual(str(trades.date.iloc[0]), "2019-01-02")
        with tempfile.TemporaryDirectory() as output:
            no_cost = executable_walk_forward(
                prices, prices, {"A": targets}, {"A": curve},
                folds=folds, candidates=("A",),
                output_dir=Path(output), fee_rate=0
            )
            self.assertAlmostEqual(
                no_cost["stitched_executable_holdout"]["total_return"], 0,
                places=11
            )

    def test_model_switch_uses_real_sell_and_buy(self):
        dates = pd.bdate_range("2015-01-01", "2021-01-31")
        n = len(dates)
        prices = pd.DataFrame({"510300.SH": 100.0, "518880.SH": 100.0}, index=dates)
        targets = {
            "A": pd.DataFrame({"510300.SH": 1.0, "518880.SH": 0.0}, index=dates),
            "B": pd.DataFrame({"510300.SH": 0.0, "518880.SH": 1.0}, index=dates),
        }
        years = np.array([d.year for d in dates])
        # 2015-2018 A initially dominates; strong 2019 B performance should
        # make B the new training-only choice at 2020-01-01.
        a_daily = np.where(years <= 2018, 0.0015, 0.0)
        b_daily = np.where(years <= 2018, 0.0001,
                           np.where(years == 2019, 0.009, 0.0001))
        equities = {
            "A": pd.Series(np.cumprod(1 + a_daily), index=dates),
            "B": pd.Series(np.cumprod(1 + b_daily), index=dates),
        }
        with tempfile.TemporaryDirectory() as output:
            report = executable_walk_forward(
                prices, prices, targets, equities,
                folds=(("2019-01-01", "2019-12-31"),
                       ("2020-01-01", "2020-12-31")),
                candidates=("A", "B"), output_dir=Path(output), fee_rate=0.001
            )
            chosen = [r["selected_strategy"] for r in report["folds"]]
            self.assertEqual(chosen, ["A", "B"])
            trades = pd.read_csv(Path(output) / "executable_walk_forward_trades.csv")
            switch = trades.loc[trades.date == "2020-01-01"]
            self.assertEqual(set(switch.side), {"BUY", "SELL"})
            self.assertEqual(len(switch), 2)
            self.assertGreater(report["charged_fee_slippage"], 0)


if __name__ == "__main__":
    unittest.main()
