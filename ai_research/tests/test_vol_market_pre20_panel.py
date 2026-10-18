"""Deterministic synthetic tests of 245-stock pre20 / market / day signal study."""
import unittest

try:
    import pandas as pd
    import numpy as np
    from ai_research.vol_market_pre20_panel import (
        bin_prior20,market_state,day_state,summary,select_nonoverlap,
        summarize_groups,validate_original_five,build_market_proxy,
        events_by_symbol,STOCKS,VERSION
    )
except ModuleNotFoundError as e:
    if e.name not in ("pandas","numpy"):
        raise
    pd=np=None


@unittest.skipIf(pd is None,"Optional numeric stack tested in dedicated workflow")
class ExpandedMarketTests(unittest.TestCase):
    def test_pre20_bins_and_market_thresholds_are_fixed(self):
        self.assertEqual([bin_prior20(x) for x in
            [-.1,0,.05,.0999,.10,.20]],
            ["<0%","0-5%","5-10%","5-10%","10-20%",">=20%"])
        self.assertEqual([market_state(x) for x in [-.031,-.029,.029,.03]],
                         ["DOWN","SIDEWAYS","SIDEWAYS","UP"])
        self.assertEqual([day_state(x) for x in [-.021,-.019,.019,.02]],
                         ["DOWN_2PLUS","FLAT_2","FLAT_2","UP_2PLUS"])

    def test_market_proxy_strictly_excludes_signal_day(self):
        dates=pd.bdate_range("2023-05-01",periods=160)
        stock={}
        for i in range(20):
            vals=10 + .002*np.arange(len(dates))
            stock[str(i)]=pd.DataFrame({"close":vals,
                "adjustment_factor":np.ones(len(dates))},index=dates)
        m,_=build_market_proxy(stock,list(stock),dates)
        copy={k:v.copy() for k,v in stock.items()}
        anchor=dates[100]
        for f in copy.values():
            f.loc[anchor:,"close"]*=2.0
        changed,_=build_market_proxy(copy,list(copy),dates)
        self.assertAlmostEqual(m.loc[anchor,"market_eqw_prior20_excl_day"],
                               changed.loc[anchor,"market_eqw_prior20_excl_day"])
        self.assertNotAlmostEqual(m.loc[dates[101],"market_eqw_prior20_excl_day"],
                                  changed.loc[dates[101],"market_eqw_prior20_excl_day"])

    def test_nonoverlap_applied_to_all_events_prior_to_conditioning(self):
        rows=[{"symbol":"A","session_idx":i,"prior20_target":i%2==0}
              for i in [10,11,16,17,24]]
        out=select_nonoverlap(rows)
        self.assertEqual([x["session_idx"] for x in out],[10,16,24])

    def test_summary_no_invented_result_for_empty_group(self):
        self.assertEqual(summary([])["n"],0)
        g=summarize_groups([])
        self.assertEqual(g["all"]["correct_pre20_5to10"]["n"],0)
        self.assertEqual(g["2025"]["market_phase"]["UP"]["n"],0) if "market_phase" in g["2025"] else None

    def test_reject_original_file_without_133_event_equivalence(self):
        with self.assertRaisesRegex(ValueError,"archive"):
            validate_original_five([],{"version":"INVALID"})


if __name__=="__main__":
    unittest.main()
