"""Paper-only R0 market-regime tests: strict signal-close-only breadth."""
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import research_entry_quality_v1 as eq


class EntryQualityCausalTests(unittest.TestCase):
    def bars(self, *, invert=False):
        days = pd.bdate_range("2024-01-02", periods=30)
        values = np.linspace(10., 15., 30)
        if invert:
            values = values[::-1]
        return pd.DataFrame({"p_close": values}, index=days)

    def test_breadth_does_not_see_future_prices(self):
        a, b = self.bars(), self.bars(invert=True)
        panel = {"A": a, "B": b}
        with patch.object(eq, "BREADTH_MIN_VALID_SYMBOLS", 2):
            old = eq.point_in_time_breadth(panel)
            new_a = a.copy()
            new_a.loc[new_a.index[25]:, "p_close"] *= 99
            later = eq.point_in_time_breadth({"A": new_a, "B": b})
        for date in a.index[:25]:
            if date in old:
                self.assertEqual(old[date], later[date])

    def test_breadth_majority_uses_signal_close_not_entry_open(self):
        d = pd.Timestamp("2024-01-05")
        c = {"pattern": "BREAKOUT", "signal_date": d}
        breadth = {d: {"valid": 1000, "above": 499, "fraction": .499},
                   pd.Timestamp("2024-01-08"): {"valid": 1000, "above": 950, "fraction": .95}}
        self.assertFalse(eq.policy_accepts("STRICT20_BREADTH_MAJORITY", c, breadth))
        breadth[d]["above"] = 501
        breadth[d]["fraction"] = .501
        self.assertTrue(eq.policy_accepts("STRICT20_BREADTH_MAJORITY", c, breadth))

    def test_patterns_do_not_use_future_return(self):
        d = pd.Timestamp("2024-01-05")
        b = {"pattern": "BREAKOUT", "signal_date": d}
        p = {"pattern": "PULLBACK", "signal_date": d}
        self.assertTrue(eq.policy_accepts("STRICT20_BREAKOUT_ONLY", b, {}))
        self.assertFalse(eq.policy_accepts("STRICT20_BREAKOUT_ONLY", p, {}))
        self.assertTrue(eq.policy_accepts("STRICT20_PULLBACK_ONLY", p, {}))
        self.assertFalse(eq.policy_accepts("STRICT20_PULLBACK_ONLY", b, {}))
        self.assertTrue(eq.policy_accepts("STRICT20_ALL", p, {}))
        self.assertFalse(eq.policy_accepts("STRICT20_BREAKOUT_AND_BREADTH", b, {}))

    def test_scheduled_candidate_order_is_stable(self):
        d = pd.Timestamp("2024-01-05")
        n = pd.Timestamp("2024-01-08")
        input_candidates = [{ "pattern":"BREAKOUT","symbol":"A","signal_date":d},
                            { "pattern":"PULLBACK","symbol":"B","signal_date":d}]
        selected = eq.make_filtered_schedules(
            {n: input_candidates}, {d: {"fraction":.6,"valid":800,"above":480}})
        self.assertEqual(selected["STRICT20_ALL"][n], input_candidates)
        self.assertEqual(selected["STRICT20_BREAKOUT_ONLY"][n], [input_candidates[0]])
        self.assertEqual(selected["STRICT20_PULLBACK_AND_BREADTH"][n], [input_candidates[1]])


if __name__ == "__main__":
    unittest.main()
