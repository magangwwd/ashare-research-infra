"""Synthetic tests for trend/chop and recovery, including missing-reference censoring."""
import importlib.util
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

PATH = Path(__file__).resolve().parents[1] / "research" / "personality_trend_repair.py"
SPEC = importlib.util.spec_from_file_location("personality_trend_repair", PATH)
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def frame(returns):
    return pd.DataFrame([
        {"trade_date": pd.Timestamp("2026-01-01") + pd.Timedelta(days=i),
         "log_ret": np.log1p(r) if r is not None else np.nan}
        for i, r in enumerate(returns)])


class TrendRepairTests(unittest.TestCase):
    def test_monotonic_trend_efficiency_one(self):
        result = m.trend(frame([.01] * 22))
        self.assertAlmostEqual(result["windows"]["20"]["median_efficiency"], 1)
        self.assertEqual(result["sign_same"], 21)

    def test_alternating_chop(self):
        result = m.trend(frame([.01, -.01] * 11))
        self.assertLess(result["windows"]["20"]["median_efficiency"], .02)
        self.assertEqual(result["sign_same"], 0)

    def test_missing_reference_blocks_rolling_window(self):
        result = m.trend(frame([.01] * 10 + [None] + [.01] * 10))
        self.assertEqual(result["windows"]["20"]["n"], 0)

    def test_recovery_duration_and_depth(self):
        events = m.episodes(frame([0, -.1, .05, .06]))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["status"], "recovered")
        self.assertEqual(events[0]["sessions_peak_to_recover"], 3)
        self.assertAlmostEqual(events[0]["max_drawdown_pct"], -10)

    def test_unrecovered_retained(self):
        events = m.episodes(frame([0, -.2, .01]))
        self.assertEqual(events[0]["status"], "censored_sample_end")

    def test_missing_censors_without_fake_recovery(self):
        events = m.episodes(frame([0, -.2, None, .1, .1]))
        self.assertEqual(events[0]["status"], "censored_data_gap")
        self.assertEqual(len(events), 1)

    def test_zero_moves_do_not_claim_directional_persistence(self):
        result = m.trend(frame([0, 0, 0, 0, 0]))
        self.assertEqual(result["sign_pairs"], 0)
        self.assertIsNone(result["windows"]["5"]["median_efficiency"])


if __name__ == "__main__":
    unittest.main()
