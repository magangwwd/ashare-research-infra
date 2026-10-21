"""Accounting invariants for self-financing single-ETF execution."""
import unittest

import numpy as np
import pandas as pd

from etf_single_rotation.experiments.simulate_self_financing import (
    execute_at_open, performance, simulate,
)


class SelfFinancingTests(unittest.TestCase):
    def test_partial_position_drifts_without_daily_rebalance(self):
        dates = pd.bdate_range("2020-01-02", periods=4)
        opens = pd.DataFrame({"510300.SH": [100, 100, 110, 110]}, index=dates)
        closes = pd.DataFrame({"510300.SH": [100, 110, 110, 105]}, index=dates)
        target = pd.DataFrame({"510300.SH": [0.5] * 4}, index=dates)
        ledger, trades = simulate(closes, opens, target, "unit_test")
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades.side.iloc[0], "BUY")
        self.assertTrue(np.allclose(ledger.shares.iloc[0], ledger.shares.iloc[1:]))
        self.assertTrue(np.allclose(ledger.cash.iloc[0], ledger.cash.iloc[1:]))
        self.assertGreater(float(ledger.close_exposure.iloc[1]), 0.50)
        self.assertLess(float(ledger.close_exposure.iloc[3]), 0.51)

    def test_signal_effective_next_open(self):
        dates = pd.bdate_range("2020-01-02", periods=3)
        opens = pd.DataFrame({"510300.SH": [100, 102, 103]}, index=dates)
        closes = pd.DataFrame({"510300.SH": [101, 103, 105]}, index=dates)
        target = pd.DataFrame({"510300.SH": [0.0, 1.0, 1.0]}, index=dates)
        ledger, trades = simulate(closes, opens, target, "unit_test")
        self.assertEqual(ledger.held_symbol.iloc[0], "CASH")
        self.assertEqual(ledger.held_symbol.iloc[1], "510300.SH")
        self.assertEqual(len(trades), 1)
        self.assertEqual(pd.Timestamp(trades.date.iloc[0]), dates[1])
        self.assertEqual(float(trades.price.iloc[0]), 102.0)

    def test_open_cash_conservation_and_no_leverage(self):
        opens = pd.Series({"510300.SH": 100.0})
        cash, shares, held, trades, blocked = execute_at_open(
            cash=1.0, shares=0.0, held=None,
            target="510300.SH", target_weight=1.0,
            opens=opens, date=pd.Timestamp("2020-01-02"),
        )
        self.assertIsNone(blocked)
        self.assertGreaterEqual(cash, 0)
        self.assertGreaterEqual(shares, 0)
        self.assertAlmostEqual(cash + shares * 100.0 + trades[0]["fee_slippage"], 1.0, places=12)

    def test_missing_open_blocks_and_retries(self):
        dates = pd.bdate_range("2020-01-02", periods=3)
        opens = pd.DataFrame({"510300.SH": [np.nan, 101.0, 101.0]}, index=dates)
        closes = pd.DataFrame({"510300.SH": [100.0, 102.0, 103.0]}, index=dates)
        target = pd.DataFrame({"510300.SH": [0.8, 0.8, 0.8]}, index=dates)
        ledger, trades = simulate(closes, opens, target, "unit_test")
        self.assertEqual(ledger.held_symbol.iloc[0], "CASH")
        self.assertTrue(bool(ledger.blocked_reason.iloc[0]))
        self.assertEqual(ledger.held_symbol.iloc[1], "510300.SH")
        self.assertEqual(len(trades), 1)
        self.assertEqual(float(trades.price.iloc[0]), 101.0)

    def test_same_etf_rebalance_only_incremental(self):
        opens = pd.Series({"510300.SH": 100.0})
        cash, shares, held, first, blocked = execute_at_open(
            1.0, 0.0, None, "510300.SH", 0.5, opens, pd.Timestamp("2020-01-02")
        )
        cash, new_shares, held, second, blocked = execute_at_open(
            cash, shares, held, "510300.SH", 0.75, opens, pd.Timestamp("2020-01-03")
        )
        self.assertEqual(len(second), 1)
        self.assertEqual(second[0]["side"], "BUY")
        self.assertLess(float(second[0]["notional"]), float(first[0]["notional"]))
        self.assertGreater(new_shares, shares)

    def test_fee_stress_reduces_return(self):
        dates = pd.bdate_range("2020-01-02", periods=4)
        opens = pd.DataFrame({"510300.SH": [100, 102, 105, 110]}, index=dates)
        closes = pd.DataFrame({"510300.SH": [101, 104, 107, 111]}, index=dates)
        target = pd.DataFrame({"510300.SH": [1.0] * 4}, index=dates)
        base, _ = simulate(closes, opens, target, "unit_test", fee_rate=0.001)
        stressed, _ = simulate(closes, opens, target, "unit_test", fee_rate=0.01)
        self.assertGreater(base.equity.iloc[-1], stressed.equity.iloc[-1])


if __name__ == "__main__":
    unittest.main()
