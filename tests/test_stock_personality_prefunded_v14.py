"""v1.4 offline tests: pre-auction funding, fixed lots, dividends, and no overlap."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/stock-personality-analysis"))
from prefunded_single_stock_v14 import replay, size, PRIORITY


def sample():
    dates = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"]
    daily = {d: {"open": 10., "high": 10.2, "low": 9.8, "close": 10., "pre_close": 10.}
             for d in dates}
    trades = [
        {"signal_date": dates[0], "entry_date": dates[1], "exit_date": dates[2],
         "entry_open": 10., "exit_open": 10., "signal": PRIORITY[0],
         "target_minus_peer_gross_pp": 0.},
        {"signal_date": dates[1], "entry_date": dates[2], "exit_date": dates[3],
         "entry_open": 10., "exit_open": 10., "signal": PRIORITY[2],
         "target_minus_peer_gross_pp": 0.}
    ]
    return daily, trades


class PrefundedV14Tests(unittest.TestCase):
    def test_board_lot_with_minimum_commission(self):
        shares, spent = size(10., budget=2000.)
        self.assertEqual(shares, 100)
        self.assertAlmostEqual(spent, 1005.5)
        self.assertLessEqual(spent, 2000.)

    def test_sale_proceeds_cannot_fund_same_open_buy(self):
        daily, trades = sample()
        with self.assertRaisesRegex(ValueError, "pre-auction cash insufficient"):
            replay(trades, daily, [], initial=2000., budget=1100.)
        r = replay(trades, daily, [], initial=2100., budget=1100.)
        self.assertEqual(r["trades_executed"], 2)
        self.assertEqual(r["same_open_flip_count"], 1)

    def test_ex_date_dividend_cannot_fund_opening_order(self):
        daily, trades = sample()
        events = [{"record_date": "2026-01-06", "ex_date": "2026-01-07",
                   "cash_per_share_yuan": 10.}]
        with self.assertRaisesRegex(ValueError, "pre-auction cash insufficient"):
            replay(trades, daily, events, initial=2000., budget=1100.)
        r = replay(trades, daily, events, initial=2100., budget=1100.)
        self.assertEqual(r["gross_dividends_yuan"], 1000.)

    def test_exact_minimum_initial_cash_bound(self):
        daily, trades = sample()
        r = replay(trades, daily, [], initial=10000., budget=1100.)
        minimum = r["minimum_initial_cash_required_yuan"]
        self.assertEqual(replay(trades, daily, [], initial=minimum + .01,
                                budget=1100.)["trades_executed"], 2)
        with self.assertRaisesRegex(ValueError, "pre-auction cash insufficient"):
            replay(trades, daily, [], initial=minimum - .01, budget=1100.)

    def test_duplicate_and_price_mismatch_rejected(self):
        daily, trades = sample()
        with self.assertRaisesRegex(ValueError, "invalid frozen trade chronology"):
            replay([trades[0], trades[0]], daily, [], initial=10000., budget=1100.)
        altered = [dict(trades[0], entry_open=11.)]
        with self.assertRaisesRegex(ValueError, "trade price mismatch"):
            replay(altered, daily, [], initial=10000., budget=1100.)


if __name__ == "__main__":
    unittest.main()
