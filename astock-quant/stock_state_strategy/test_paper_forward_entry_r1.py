"""No-network, no-future sample tests of frozen R1 paper candidate ledger."""
import unittest
from unittest.mock import patch
from collections import defaultdict

import numpy as np
import pandas as pd

import research_entry_quality_v1 as eq
from paper_forward_entry_r1 import validate_forward_manifest, make_snapshot, POLICY_NAME


def fixture():
    days = pd.bdate_range("2025-01-02", periods=270)
    # Historical market close known at signal date; rising MA20 and quiet
    # bullish body near MA20, a qualifying PULLBACK, 2ATR/price approx 3%.
    close = 10 + .03 * np.arange(len(days))
    chunks = []
    for sym in ("600000.SH", "000001.SZ"):
        df = pd.DataFrame({
            "vendor_symbol": sym, "trade_date": days,
            "open": close-.05, "high": close+.10, "low": close-.20,
            "close": close, "volume_shares": 1000000.,
            "turnover_cny": 120000000., "adjustment_factor":1.,
            "p_open":close-.05, "p_high":close+.10,"p_low":close-.20,
            "p_close": close,
        })
        chunks.append(df)
    return pd.concat(chunks, ignore_index=True), days[-1]


class PassRisk:
    def entry_reason(self, *args):
        return None


class ProspectiveR1Tests(unittest.TestCase):
    def test_r1_starts_after_inspected_history(self):
        verified = {"as_of_date":"2026-10-12",
                    "validation":{"primary_keys_unique":True}}
        with self.assertRaises(RuntimeError):
            validate_forward_manifest(verified, pd.Timestamp("2026-10-09"),
                                      pd.Timestamp("2026-10-12"))
        validate_forward_manifest(verified, pd.Timestamp("2026-10-12"),
                                  pd.Timestamp("2026-10-12"))
        with self.assertRaises(RuntimeError):
            validate_forward_manifest(verified, pd.Timestamp("2026-10-12"),
                                      pd.Timestamp("2026-10-11"))

    def test_requires_exact_source_date_and_integrity(self):
        with self.assertRaises(RuntimeError):
            validate_forward_manifest(
                {"as_of_date":"2026-10-09","validation":{"primary_keys_unique":True}},
                pd.Timestamp("2026-10-12"),pd.Timestamp("2026-10-12"))
        with self.assertRaises(RuntimeError):
            validate_forward_manifest(
                {"as_of_date":"2026-10-12","validation":{}},
                pd.Timestamp("2026-10-12"),pd.Timestamp("2026-10-12"))

    def test_breadth_and_pattern_are_close_only(self):
        panel, day = fixture()
        with patch.object(eq, "BREADTH_MIN_VALID_SYMBOLS", 2):
            # Imported function reads eq's module-level threshold.
            from paper_forward_entry_r1 import point_in_time_breadth
            self.assertIs(point_in_time_breadth, eq.point_in_time_breadth)
            r = make_snapshot(panel, PassRisk(), day)
        self.assertTrue(r["market_breadth_pass"])
        self.assertEqual(r["valid_symbols"], 2)
        self.assertEqual(r["raw_pullback_patterns"], 2)
        self.assertEqual(len(r["candidates"]), 2)
        for c in r["candidates"]:
            self.assertFalse(c["entry_authorized"])
            self.assertEqual(c["next_open_status"], "UNKNOWN_PENDING_NEW_TRADING_SESSION")
            self.assertEqual(c["signal_date"], str(day.date()))

    def test_cannot_use_future_close_even_if_present_in_input(self):
        panel, day = fixture()
        later = panel.copy()
        for sym in later.vendor_symbol.unique():
            row = later[later.vendor_symbol.eq(sym)].iloc[-1:].copy()
            row["trade_date"] = day + pd.Timedelta(days=1)
            later = pd.concat([later, row], ignore_index=True)
        with self.assertRaises(RuntimeError):
            make_snapshot(later, PassRisk(), day)

    def test_low_breadth_is_no_signal_not_a_buy(self):
        panel, day = fixture()
        # All eligible symbols are below their trailing 20-day average;
        # exactly half would pass the >=50% majority rule.
        for sym in panel.vendor_symbol.unique():
            m = panel.vendor_symbol.eq(sym)
            panel.loc[m, "p_close"] = np.linspace(20., 10., int(m.sum()))
        with patch.object(eq,"BREADTH_MIN_VALID_SYMBOLS",2):
            result=make_snapshot(panel,PassRisk(),day)
        self.assertEqual(len(result["candidates"]), 0)
        self.assertIsNone(result["raw_pullback_patterns"])
        self.assertTrue(result["fraction"] < .5 or result["fraction"] == .5)

    def test_r1_is_distinct_and_nontrading(self):
        self.assertIn("R1", POLICY_NAME)
        self.assertIn("PULLBACK", POLICY_NAME)


if __name__=="__main__":
    unittest.main()
