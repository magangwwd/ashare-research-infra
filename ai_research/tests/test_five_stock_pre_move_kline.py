"""Offline tests for fixed-feature pre-up / pre-down evidence study."""
import unittest

try:
    import numpy as np
    import pandas as pd
    from ai_research.five_stock_pre_move_kline import (
        STOCKS, PERIODS, HORIZONS, FEATURES, which_period, episodes,
        features_from_gold, group_difference, report,
    )
    from ai_research.kline_three_year import indicators
except ModuleNotFoundError as exc:
    if exc.name not in ("numpy","pandas"):
        raise
    np = pd = None


def sample():
    dates=pd.bdate_range("2023-05-02","2026-10-16")
    z=np.arange(len(dates))
    c=np.array(9+z*.003+.2*np.sin(z/5),dtype=float)
    return pd.DataFrame({
        "open":c-.02,"high":c+.10,"low":c-.12,"close":c,
        "volume_shares":np.ones(len(dates))*100000,
        "adjustment_factor":np.ones(len(dates)),
    },index=dates)


@unittest.skipIf(pd is None,"Numerical dependencies installed by focused workflow")
class PreMoveTests(unittest.TestCase):
    def test_five_fixed_symbols_and_two_fixed_labels(self):
        self.assertEqual(set(STOCKS),{
            "600351.SH","002107.SZ","600641.SH","002568.SZ","002296.SZ"})
        self.assertEqual(HORIZONS,{1:.05,5:.10})
        self.assertEqual(len(FEATURES),14)

    def test_all_features_stop_at_anchor_day(self):
        a=sample()
        d=pd.Timestamp("2025-03-20")
        raw,_,_=features_from_gold(a)
        updated=a.copy()
        updated.loc[d+pd.Timedelta(days=1):,"close"] *= 1.38
        later,_,_=features_from_gold(updated)
        pd.testing.assert_series_equal(raw.loc[d],later.loc[d])

    def test_original_frozen_volatility_is_unchanged(self):
        a=sample()
        features,_,_=features_from_gold(a)
        _,existing=indicators(a)
        a_series=features["frozen_vol_expansion"].dropna()
        b=existing["volatility_expansion"].loc[a_series.index].astype(float)
        pd.testing.assert_series_equal(a_series,b,check_names=False)

    def test_no_missing_historical_input_faked(self):
        a=sample()
        a.loc["2025-04-01",["close","high","low","open","volume_shares"]]=np.nan
        feat,_,_=features_from_gold(a)
        self.assertTrue(feat.loc["2025-04-01"].isna().all())
        self.assertTrue(feat.loc["2025-04-09"].isna().all())

    def test_periods_and_nonoverlap(self):
        self.assertEqual(which_period("2024-12-31"),"2023_2024")
        self.assertEqual(which_period("2025-01-01"),"2025")
        self.assertEqual(which_period("2026-10-16"),"2026")
        self.assertEqual(episodes([4,6,9,10,16,18],5),[4,10,16])

    def test_sparse_group_does_not_claim_discriminative_power(self):
        rows=[
            {"symbol":"600351.SH","horizon":5,"period":"2025","label":"BIG_UP",
             "index":200,"features":{k:1 for k in FEATURES}},
            {"symbol":"002107.SZ","horizon":5,"period":"2025","label":"BIG_DOWN",
             "index":201,"features":{k:-1 for k in FEATURES}},
        ]
        d=group_difference(rows,5,"2025")
        self.assertEqual(d["labels"]["BIG_UP"],1)
        self.assertEqual(d["labels"]["BIG_DOWN"],1)
        self.assertFalse(d["feature_compare"][0]["interpretation_eligible"])

    def test_report_empty_group_visible(self):
        pooled={}
        for horizon in HORIZONS:
            pooled[str(horizon)]={}
            for period in PERIODS:
                pooled[str(horizon)][period]=group_difference([],horizon,period)
        stocks={}
        for s in STOCKS:
            stocks[s]={"name":STOCKS[s],"counts":{
                str(h):{per:{"class_counts":{
                    "BIG_UP":0,"BIG_DOWN":0,"NORMAL":0}}
                for per in PERIODS}
                for h in HORIZONS}}
        text=report({"as_of":"2026-10-16",
                     "pooled":pooled,"stocks":stocks,"risk_flags":["NOT TRADEABLE"]})
        self.assertIn("不能判断方向规律",text)
        self.assertIn("NOT TRADEABLE",text)


if __name__=="__main__":
    unittest.main()
