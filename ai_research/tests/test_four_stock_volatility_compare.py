"""No-network tests for frozen four-stock Gold OHLCV event comparison."""
import unittest
try:
    import numpy as np
    import pandas as pd
    from ai_research.four_stock_volatility_compare import (
        prepared_one, render, compute, SYMBOLS, VERSION)
except ModuleNotFoundError as e:
    if e.name not in ("pandas","numpy"):
        raise
    np=pd=None


def synth():
    ix=pd.bdate_range("2023-05-02","2026-10-16")
    z=np.arange(len(ix))
    price=10 + .004*z + .35*np.sin(z/12)
    # Include arbitrary shocks in each period to provoke volatility expansion.
    price=np.array(price)
    for k in (180,295,415,490,630,730):
        price[k:]+=np.where(k%2==0, .3,-.4)
    return pd.DataFrame({
       "open":price-.015,"high":price+.05,"low":price-.07,
       "close":price,"volume_shares":10000.+1000*(z%3),
       "adjustment_factor":np.ones(len(ix)),
    },index=ix)


@unittest.skipIf(pd is None,"Optional pandas/numpy installed in dedicated Kline CI")
class FourStockTests(unittest.TestCase):
    def test_four_fixed_tickers_exact(self):
        self.assertEqual(set(SYMBOLS),{
           "002107.SZ","600641.SH","002568.SZ","002296.SZ"})

    def test_frozen_expansion_matches_original_function(self):
        from ai_research.kline_three_year import indicators,START,END
        x=synth()
        _,rule=indicators(x)
        ret=prepared_one(x,"002107.SZ")
        observed={r["signal_date"] for r in ret["events"]}
        expected={str(d.date()) for d in x.index[
            rule["volatility_expansion"] & (x.index.date>=START) &
            (x.index.date<=END)][:-5]}
        # All retained events are a subset of original fixed detections;
        # some may be right-censored at study end.
        self.assertTrue(observed.issubset(expected) or
                        observed.issubset({str(d.date()) for d in x.index[
                            rule["volatility_expansion"]]}))
        self.assertEqual(ret["valid_daily_gold_days"],int(
            ((x.index.date>=START)&(x.index.date<=END)).sum()))

    def test_chronological_periods_and_relative_baseline(self):
        s=prepared_one(synth(),"600641.SH")
        self.assertEqual(set(s["split"]),{"discovery","validation","test"})
        for period,x in s["split"].items():
            self.assertGreater(x["baseline_ordinary_days"]["n"],100)
            self.assertLessEqual(x["nonoverlap_signals"]["n"],
                                 x["signals"]["n"])
            self.assertTrue(all(k in x for k in (
                "trend_bins","day_bins","next_open")))

    def test_reject_sparse_gold(self):
        x=synth()
        x.loc[(x.index.year>=2024)&(x.index.year<=2026),"close"]=np.nan
        with self.assertRaisesRegex(ValueError,"complete daily Gold"):
            prepared_one(x,"002296.SZ")

    def test_no_intraday_or_trading_assertion(self):
        from ai_research.four_stock_volatility_compare import render
        obj={"symbols":{},"question_2025":{
            "negative_2025_stock_count":0,
            "negative_2025_at_least_five_events_count":0},
            "risk_flags":["No 15-minute data assumed"]}
        for symbol,name in SYMBOLS.items():
            s=prepared_one(synth(),symbol)
            obj["symbols"][symbol]=s
        txt=render(obj)
        self.assertIn("另外四只股票",txt)
        self.assertIn("2025年",txt)
        self.assertIn("No 15-minute data assumed",txt)


if __name__=="__main__":
    unittest.main()
