"""Pure offline R1 five-stock watch tests; no network, no trades."""
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import paper_forward_watchlist_r1 as r1


def panel_fixture(*, falling=False):
    day = pd.Timestamp("2026-10-12")
    days = pd.bdate_range(end=day, periods=270)
    close = 10.0 + 0.009 * np.arange(len(days))
    if falling:
        close = close[::-1]
    stocks = []
    for symbol in ("600351.SH", "002107.SZ"):
        frame = pd.DataFrame({
            "vendor_symbol": symbol,
            "trade_date": days,
            "open": close - 0.07, "high": close + 0.13,
            "low": close - 0.16, "close": close,
            "volume_shares": np.full(len(days), 800_000.),
            "turnover_cny": np.full(len(days), 125_000_000.),
            "adjustment_factor": np.ones(len(days)),
            "is_quality_valid": True,
        })
        for col in ("open", "high", "low", "close"):
            frame["p_" + col] = frame[col]
        stocks.append(frame)
    return pd.concat(stocks, ignore_index=True), day


class GoodPIT:
    def __init__(self, block=None):
        self.block = block
        self.limits = {("600351.SH", pd.Timestamp("2026-10-12")): (15, 10)}
    def entry_reason(self, sym, day, bar):
        return self.block


class FiveStockScreenTests(unittest.TestCase):
    def test_exact_watchlist_symbols(self):
        self.assertEqual(set(r1.WATCHLIST), {
            "600351.SH", "002107.SZ", "600641.SH",
            "002568.SZ", "002296.SZ",
        })

    def test_manifest_missing_quality_or_pre_lock_fails_closed(self):
        with self.assertRaises(RuntimeError):
            r1.validate_manifest({"as_of_date":"2026-10-09",
                                  "validation":{"primary_keys_unique":True,"ohlc_valid":True}},
                                 pd.Timestamp("2026-10-09"))
        with self.assertRaises(RuntimeError):
            r1.validate_manifest({"as_of_date":"2026-10-12",
                                  "validation":{"primary_keys_unique":True,"ohlc_valid":False}},
                                 pd.Timestamp("2026-10-12"))
        with self.assertRaises(RuntimeError):
            r1.validate_manifest({"as_of_date":"2026-10-13",
                                  "validation":{"primary_keys_unique":True,"ohlc_valid":True}},
                                 pd.Timestamp("2026-10-12"))

    def test_breadth_is_close_only(self):
        panel, day = panel_fixture()
        with patch.dict(r1.POLICY, {"breadth_minimum_valid":2}):
            first = r1.market_breadth(panel, day)
            modified = panel.copy()
            future = panel.iloc[-1:].copy()
            future["trade_date"] = day + pd.Timedelta(days=1)
            future["p_close"] *= 100
            later = pd.concat([modified, future], ignore_index=True)
            second = r1.market_breadth(later, day)
        self.assertEqual(first, second)
        self.assertTrue(first["passed"])
        self.assertEqual(first["eligible_symbols"], 2)

    def test_r1_pullback_candidates_are_unexecuted_paper_only(self):
        panel, day = panel_fixture()
        with patch.dict(r1.POLICY, {"breadth_minimum_valid":2}):
            report = r1.screen_five(panel, GoodPIT(), day)
        self.assertTrue(report["breadth"]["passed"])
        self.assertEqual(len(report["checked"]), 5)
        self.assertEqual(set(x["symbol"] for x in report["candidates"]),
                         {"600351.SH", "002107.SZ"})
        for entry in report["candidates"]:
            self.assertEqual(entry["pattern"], "PULLBACK")
            self.assertFalse(entry["execution_authorized"])
            self.assertFalse(entry["next_open_verified"])
            self.assertLessEqual(entry["estimated_2atr_fraction"], .04)
        self.assertTrue(all(x["status"] == "DATA_MISSING_FAIL_CLOSED"
                            for x in report["checked"] if x["symbol"] == "600641.SH"))

    def test_pit_block_never_becomes_a_buy(self):
        panel, day = panel_fixture()
        with patch.dict(r1.POLICY, {"breadth_minimum_valid":2}):
            report = r1.screen_five(panel, GoodPIT("ST"), day)
        self.assertFalse(report["candidates"])
        self.assertEqual(report["checked"][0]["status"], "PIT_ST")

    def test_market_breadth_failed_means_no_candidates(self):
        panel, day = panel_fixture(falling=True)
        with patch.dict(r1.POLICY, {"breadth_minimum_valid":2}):
            report = r1.screen_five(panel, GoodPIT(), day)
        self.assertFalse(report["breadth"]["passed"])
        self.assertEqual(report["candidates"], [])

    def test_insufficient_breadth_denominator_refuses_to_infer(self):
        panel, day = panel_fixture()
        with self.assertRaises(RuntimeError):
            r1.market_breadth(panel, day)

    def test_input_future_dates_do_not_create_same_day_signal(self):
        panel, day = panel_fixture()
        future = panel.loc[panel.vendor_symbol.eq("600351.SH")].iloc[-1:].copy()
        future["trade_date"] = day + pd.Timedelta(days=1)
        panel = pd.concat([panel, future], ignore_index=True)
        with patch.dict(r1.POLICY, {"breadth_minimum_valid":2}):
            # Latest bar for changed symbol is no longer the requested close.
            screen = r1.screen_five(panel, GoodPIT(), day)
        s = next(x for x in screen["checked"] if x["symbol"] == "600351.SH")
        self.assertEqual(s["status"], "DATA_MISSING_FAIL_CLOSED")


if __name__ == "__main__":
    unittest.main()
