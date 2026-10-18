"""Numerical checks for fixed-bin 600351 volatility attribution, offline only."""
import unittest
try:
    import pandas as pd
    import numpy as np
    from ai_research.kline_volatility_regime import (
        bin_trend, bin_day, summarize, nonoverlap, write, analyze)
except ModuleNotFoundError as exc:
    if exc.name not in ("pandas","numpy"):
        raise
    pd=np=None


@unittest.skipIf(pd is None,"Dedicated numeric CI installs pandas and numpy")
class AttributionTests(unittest.TestCase):
    def test_trend_bins_use_fixed_plus_minus_five_percent(self):
        self.assertEqual(bin_trend(-.06),"prior20_down_le_minus5pct")
        self.assertEqual(bin_trend(-.05),"prior20_down_le_minus5pct")
        self.assertEqual(bin_trend(-.049),"prior20_sideways")
        self.assertEqual(bin_trend(.05),"prior20_up_ge_plus5pct")
        with self.assertRaises(ValueError):
            bin_trend(np.nan)

    def test_day_bins_independent_fixed_threshold(self):
        self.assertEqual(bin_day(-.021),"signal_day_down_le_minus2pct")
        self.assertEqual(bin_day(.0),"signal_day_neutral")
        self.assertEqual(bin_day(.03),"signal_day_up_ge_plus2pct")

    def test_highest_return_dominance(self):
        sample=[{"future5_close_close":v} for v in (-.09524,.00332,.01)]
        d=summarize(sample)
        self.assertEqual(d["n"],3)
        self.assertLess(d["mean"],0)
        self.assertEqual(d["up_rate"],round(2/3,6))
        self.assertAlmostEqual(d["leave_worst_out_mean"],.00666,places=5)
        self.assertLess(d["leave_best_out_mean"],d["mean"])

    def test_nonoverlap_preserves_chronological_5_day_spacing(self):
        rows=[{"signal_date":f"2025-01-{d:02d}",
               "exchange_session_idx":i,
               "future5_close_close":0.01}
              for d,i in [(3,10),(6,11),(10,16),(11,17),(22,24)]]
        self.assertEqual([x["exchange_session_idx"] for x in nonoverlap(rows)],
                         [10,16,24])

    def test_empty_group_and_unnormalized_authorization_fail_closed(self):
        self.assertEqual(summarize([])["n"],0)
        with self.assertRaisesRegex(ValueError,"authorization"):
            analyze(pd.DataFrame(),{"authorization":"TRADE_ALLOWED"},pd.DataFrame())

    def test_report_writer_does_not_claim_real_trades(self):
        import tempfile, pathlib
        from ai_research.kline_volatility_regime import PERIODS,TREND_LABELS,DAY_LABELS,NEXT_LABELS
        record={"version":"KLINE_600351_VOL_REGIME_ATTRIBUTION_V1",
            "symbol":"600351.SH","start":"2023-10-16","as_of":"2026-10-16",
            "methodology":["NOT_A_TRADE"],
            "split":{}}
        zero=summarize([])
        for p in PERIODS:
            record["split"][p]={
                "total":zero,"nonoverlap":zero,"next_open_proxy":zero,
                "next_10am_proxy":zero,"worst_session_low_from_next_open":zero,
                "trend_bins":{k:zero for k in TREND_LABELS},
                "day_bins":{k:zero for k in DAY_LABELS},
                "next30_bins_post_10am_only":{k:zero for k in NEXT_LABELS}}
        with tempfile.TemporaryDirectory() as t:
            write(record,pathlib.Path(t))
            self.assertEqual({p.name for p in pathlib.Path(t).iterdir()},
                             {"statistics.json","report.md","manifest.json"})
            txt=(pathlib.Path(t)/"report.md").read_text()
            self.assertIn("NOT_A_TRADE",txt)
            self.assertIn("不算真正未见样本",txt)


if __name__=="__main__":
    unittest.main()
