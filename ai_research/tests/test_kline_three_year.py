"""Pure synthetic invariants for the non-trading three-year candlestick study."""
import unittest
import numpy as np
import pandas as pd
from ai_research.kline_three_year import (START, END, HORIZONS,
                                         TARGET, indicators, numbers, render)


def fake_frame():
    ix=pd.bdate_range("2023-05-02","2026-10-16")
    x=np.arange(len(ix))
    close=10+0.005*x+0.3*np.sin(x/7)
    volume=np.where(x%17==0,1900000,900000).astype(float)
    return pd.DataFrame({
        "open":close-0.05,"high":close+0.15,"low":close-0.25,
        "close":close,"volume_shares":volume,
        "adjustment_factor":np.ones(len(ix))},index=ix)


class KlineTests(unittest.TestCase):
    def test_all_six_patterns_and_five_forward_windows(self):
        result,baseline=numbers(fake_frame(),None)
        self.assertEqual(len(result),6)
        self.assertEqual(set(baseline),{"discovery","validation","test"})
        for row in result.values():
            self.assertEqual(set(row["horizons"]),{str(k) for k in HORIZONS})
        sample={"start":str(START),"as_of":str(END),
                "rows_in_study":765,"source":{"invalid_source_rows":0,
                    "history_manifest_status":"COMPLETE",
                    "history_max_date":"20261016"},
                "patterns":result}
        self.assertIn("先按固定形态",render(sample))
        self.assertIn("三年出现次数",render(sample))

    def test_no_lookahead_at_end_of_any_split(self):
        _,base=numbers(fake_frame(),None)
        # 20-day horizon must not include final 20 records in 2026 test.
        self.assertLess(base["test"]["20"],base["test"]["1"])
        self.assertGreater(base["test"]["1"],10)

    def test_unavailable_and_suspended_cannot_be_fake_zero(self):
        p=fake_frame()
        p.loc["2025-05-15",["open","high","low","close","volume_shares"]]=np.nan
        _, marks=indicators(p)
        for rule in marks.values():
            self.assertFalse(bool(rule.loc["2025-05-15"]))
        out,_=numbers(p,None)
        self.assertEqual(len(out),6)

    def test_last_day_event_has_no_future_outcome(self):
        p=fake_frame()
        # Ensure no terminal non-existent +5 day label is possible.
        results,baseline=numbers(p,None)
        self.assertGreater(baseline["test"]["5"],0)
        self.assertLess(baseline["test"]["5"],baseline["test"]["1"])

    def test_no_training_random_split(self):
        x=fake_frame()
        _,a=numbers(x,None)
        self.assertGreater(a["discovery"]["5"],a["validation"]["5"])
        self.assertGreater(a["validation"]["5"],a["test"]["5"])


if __name__=="__main__":
    unittest.main()
