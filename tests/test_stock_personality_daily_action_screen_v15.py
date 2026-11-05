"""Synthetic regression tests for the v1.5 read-only action screen."""
import importlib.util
from pathlib import Path
import unittest

MODULE = Path(__file__).resolve().parents[1] / "skills" / "stock-personality-analysis" / "daily_action_screen_v15.py"
spec = importlib.util.spec_from_file_location("daily_action_screen_v15", MODULE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def bar(day, close, pre_close, ticker="600031.SH"):
    return {"ts_code": ticker, "trade_date": day, "close": str(close), "pre_close": str(pre_close)}


class DailyActionScreenTests(unittest.TestCase):
    def test_cash_event_matched_and_record_date_entitlement(self):
        d = [bar("2025-10-14", 23.04, 22.90), bar("2025-10-15", 23.46, 22.73)]
        ev = [{"record_date": "2025-10-14", "ex_date": "2025-10-15", "cash_per_share_yuan": 0.31}]
        tr = [{"signal": "old", "entry_date": "2025-10-01", "exit_date": "2025-10-15", "shares": "1700", "pnl_ex_dividend_yuan": "1"},
              {"signal": "new", "entry_date": "2025-10-15", "exit_date": "2025-10-20", "shares": "1700", "pnl_ex_dividend_yuan": "-2"}]
        x = mod.screen(d, ev, tr)
        self.assertTrue(x["target_discontinuities"][0]["matches_cash_amount"])
        self.assertEqual([a["signal"] for a in x["target_discontinuities"][0]["record_date_entitled_trades"]], ["old"])
        self.assertEqual(x["trade_pnl_by_entry_year"]["2025"]["pnl_ex_dividend_yuan"], -1)

    def test_missing_is_flagged_not_filled(self):
        x = mod.screen([bar("2026-09-29", 17.06, 17), bar("2026-09-30", 17.49, "")], [], [])
        self.assertEqual(x["pre_close_nulls"], [{"ts_code": "600031.SH", "date": "2026-09-30"}])
        self.assertEqual(x["target_discontinuities"], [])

    def test_unexplained_discontinuity_not_assumed_dividend(self):
        x = mod.screen([bar("2024-01-01", 10, 9.8), bar("2024-01-02", 10.2, 9.6)], [], [])
        self.assertFalse(x["target_discontinuities"][0]["listed_cash_event"])

    def test_duplicate_detection(self):
        x = mod.screen([bar("2024-01-01", 10, 9), bar("2024-01-01", 10, 9)], [], [])
        self.assertEqual(len(x["duplicate_keys"]), 1)
