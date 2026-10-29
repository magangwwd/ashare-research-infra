"""Synthetic invariants for the real-data descriptive profile."""
import importlib.util
from pathlib import Path
import unittest
import pandas as pd

PATH = Path(__file__).resolve().parents[1]/"research"/"personality_profile_daily.py"
SPEC = importlib.util.spec_from_file_location("personality_profile_daily", PATH)
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def sample():
    return pd.DataFrame([
        dict(ts_code=c, trade_date=f"2026-01-{i+5:02d}",
             open=10.0, high=10.5, low=9.5, close=10.0 + change, pre_close=10.0)
        for c in [m.TARGET, *m.PEERS]
        for i, change in enumerate([.1, -.2, .3])])


class ProfileTests(unittest.TestCase):
    def test_missing_reference_is_excluded_without_raw_close_fill(self):
        d = sample()
        d.loc[0,"pre_close"] = float("nan")
        result, _ = m.analyze(d)
        self.assertEqual(result["stocks"][m.TARGET]["all"]["return_n"], 2)
        self.assertEqual(result["target_vs_equal_weight_four_peer_proxy"]["all"]["n"], 2)

    def test_ex_dividend_reference_does_not_create_mechanical_gap(self):
        row = sample().iloc[:1].copy()
        row.loc[:,["open","high","low","close","pre_close"]] = [9,9.5,8.5,9,9]
        self.assertAlmostEqual(m.prepare(row).gap.iloc[0], 0)

    def test_known_perfect_peer_co_movement(self):
        result, _ = m.analyze(sample())
        self.assertAlmostEqual(result["target_vs_equal_weight_four_peer_proxy"]["all"]["correlation"], 1)
        self.assertAlmostEqual(result["target_vs_equal_weight_four_peer_proxy"]["all"]["beta"], 1)

    def test_bad_inputs_and_missing_peers_are_rejected(self):
        for d in (pd.concat([sample(), sample().iloc[:1]]),
                  sample().iloc[1:], sample()[sample().ts_code != m.PEERS[0]]):
            with self.assertRaises(ValueError):
                m.analyze(d)
        d = sample()
        d.loc[0,"high"] = 1
        with self.assertRaises(ValueError):
            m.analyze(d)

    def test_touch_counts_and_empty_condition(self):
        d = sample().iloc[:1].copy()
        d.loc[:,["open","high","low","close"]] = [10.2,10.5,9.9,10.3]
        v = m.metrics(m.prepare(d))
        self.assertEqual(v["gap_up_ge_1pct_reference_touched"]["count"], 1)
        self.assertIsNone(v["gap_down_le_minus1pct_reference_touched"]["rate_pct"])

    def test_zero_range_is_a_valid_flat_reference_return(self):
        d = sample().iloc[:1].copy()
        d.loc[:,["open","high","low","close"]] = [10,10,10,10]
        x = m.prepare(d)
        self.assertEqual(x["range"].iloc[0], 0)
        self.assertEqual(x.ret.iloc[0], 0)

    def test_exact_thresholds_are_inclusive_despite_float_rounding(self):
        d = sample().iloc[:1].copy()
        d.loc[:,["open","high","low","close"]] = [10.1,10.4,9.9,10.3]
        v = m.metrics(m.prepare(d))
        self.assertEqual(v["abs_return_ge_3pct_count"], 1)
        self.assertEqual(v["gap_up_ge_1pct_intraday_up"]["n"], 1)


if __name__ == "__main__":
    unittest.main()

