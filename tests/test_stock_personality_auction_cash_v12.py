"""Offline synthetic regression tests for v1.2 same-open auction funding."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/stock-personality-analysis"))
from auction_cash_audit_v12 import same_open_cases, run, PRIORITY
from dividend_cash_v11 import simulate

class AuctionCashV12Tests(unittest.TestCase):
    def setUp(self):
        self.daily = {
            d: {"open": 10., "high": 10., "low": 10., "close": 10., "pre_close": 10.}
            for d in ("2024-01-02", "2024-01-03", "2024-01-04",
                      "2024-01-05", "2024-01-08")
        }
        self.trades = [
            {"signal_date": "2024-01-02", "entry_date": "2024-01-03",
             "exit_date": "2024-01-04", "entry_open": 10., "exit_open": 10.,
             "signal": "D_breakout"},
            {"signal_date": "2024-01-03", "entry_date": "2024-01-04",
             "exit_date": "2024-01-05", "entry_open": 10., "exit_open": 10.,
             "signal": "B_breakdown"}
        ]

    def test_detect_flip_and_unfunded_lot(self):
        baseline = simulate(self.trades, self.daily, [])
        cases = same_open_cases(self.trades, baseline, self.daily)
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["date"], "2024-01-04")
        self.assertFalse(cases[0]["can_pre_fund_one_lot"])
        self.assertGreater(cases[0]["one_lot_shortfall_yuan"], 0)

    def test_no_flip(self):
        trades = self.trades[:1]
        self.assertEqual(same_open_cases(trades, simulate(trades, self.daily, []), self.daily), [])

    def test_skip_reentry_retains_prior_exit(self):
        result = run({"priority": PRIORITY, "selected_trades": self.trades},
                     self.daily, [], enforce_frozen=False)
        self.assertEqual(result["excluded_entries"], 1)
        self.assertEqual(result["baseline_gross"]["trades_executed"], 2)
        self.assertEqual(result["skip_same_open_gross"]["trades_executed"], 1)

    def test_frozen_schedule_guard(self):
        with self.assertRaises(ValueError):
            run({"priority": PRIORITY, "selected_trades": self.trades}, self.daily, [])

    def test_fundable_lot_when_pre_cash_exists(self):
        base = {"daily_equity": [
            {"date": "2024-01-03", "cash": 3000.},
            {"date": "2024-01-04", "cash": 100.}
        ]}
        self.assertTrue(same_open_cases(self.trades, base, self.daily)[0]["can_pre_fund_one_lot"])

if __name__ == "__main__":
    unittest.main()
