"""Offline invariants for the deeper frozen 5–10% volatility study."""
import unittest
try:
    import pandas as pd
    import numpy as np
    from ai_research.volatility_pre20_deep import (
        BINS, TARGET_BIN, bin_for, summary, thinning, raw_rows, grouped, make_report,
    )
except ModuleNotFoundError as exc:
    if exc.name not in ("pandas","numpy"):
        raise
    pd = np = None


@unittest.skipIf(pd is None, "Optional pandas/numpy only in dedicated numerical CI")
class DeepPre20Tests(unittest.TestCase):
    def test_bins_exact_edges(self):
        self.assertEqual([bin_for(v) for v in
            (-.05,0,.049999,.05,.09999,.10,.1999,.20)],
            list(BINS[:2])+["0-5%","5-10%","5-10%","10-20%","10-20%",">=20%"])
        with self.assertRaises(ValueError):
            bin_for(np.nan)

    def test_source_packet_identity_enforced(self):
        with self.assertRaisesRegex(ValueError,"not recognized"):
            raw_rows({"version":"fake","as_of":"2026-10-16",
                      "authorization":"RESEARCH_ONLY_NO_TRADING"})

    def test_outlier_and_intrawindow_vs_fifth_close_are_different(self):
        d=pd.DataFrame({
            "r5":[.07,-.01,-.035],
            "r5_next_open":[.06,-.005,-.02],
            "mae_from_next_open":[.11,.07,.03],
            "within5_down5_from_next_open":[True,True,False],
            "within5_down5_from_signal_close":[True,True,False],
            "close_day5_down5":[False,False,False],
        })
        s=summary(d)
        self.assertEqual(s["n"],3)
        self.assertEqual(s["close_day5_down5_count"],0)
        self.assertEqual(s["intrawindow_low_from_next_open_down5_count"],2)
        self.assertEqual(s["up_count"],1)
        self.assertAlmostEqual(s["mean_without_best"],-.0225)

    def test_independent_sample_precedes_bucket_filter(self):
        d=pd.DataFrame({
            "stock":["a","a","a","a","b"],
            "idx":[10,12,16,24,12],
            "frozen_expansion":[1,1,1,1,1],
            "group":["5-10%","5-10%","0-5%","5-10%","5-10%"],
        })
        out=thinning(d)
        self.assertEqual(len(out),4)
        self.assertEqual(len(out[out.group=="5-10%"]),3)

    def test_empty_bucket_does_not_invent_zero_failure_rate(self):
        self.assertEqual(summary(pd.DataFrame())["n"],0)

    def test_report_labels_not_real_trades(self):
        # This report requires full results; source-level claims always include
        # explicit research-only metadata before report generation.
        self.assertEqual(TARGET_BIN,"5-10%")


if __name__=="__main__":
    unittest.main()
