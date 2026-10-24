"""V5 causal ranking tests with pre-entry synthetic fixtures, never future PnL."""
from __future__ import annotations
import unittest
from unittest.mock import patch
import pandas as pd
import short_swing_3to5_v5_causal_ranking as v5
from short_swing_3to5_v1 import Policy

def sample():
    # High breakout is deliberately the original first-listed signal.
    day=pd.Timestamp("2026-01-05")
    idx=pd.MultiIndex.from_tuples([(day,"600001.SH"),(day,"600002.SH"),
                                   (day,"600003.SH")], names=("trade_date","vendor_symbol"))
    features=pd.DataFrame({"breakout":[.045,.005,.018],
                           "prior5_gain":[.04,.12,.02],
                           "atr14_pct":[.02]*3,
                           "candle_gain":[.01]*3},index=idx)
    signals={day:[("600001.SH",.046),("600003.SH",.025),("600002.SH",.02)]}
    return signals,features

class TestCausalRanking(unittest.TestCase):
    def test_original_membership_exact(self):
        signals,features=sample()
        result=v5.rank_signals(signals,features,"V2_LOOSE_UNCHANGED")
        self.assertEqual(result,signals)
        self.assertIsNot(result[tuple(signals)[0]],signals[tuple(signals)[0]])
    def test_less_extended_vs_less_runup_are_distinct(self):
        signals,features=sample()
        low_breakout=v5.rank_signals(signals,features,"LESS_EXTENDED_BREAKOUT_FIRST")
        low_runup=v5.rank_signals(signals,features,"LESS_RECENT_RUNUP_FIRST")
        day=list(signals)[0]
        self.assertEqual([s for s,_ in low_breakout[day]],
                         ["600002.SH","600003.SH","600001.SH"])
        self.assertEqual([s for s,_ in low_runup[day]],
                         ["600003.SH","600001.SH","600002.SH"])
        self.assertCountEqual(low_breakout[day],signals[day])
        self.assertCountEqual(low_runup[day],signals[day])
    def test_no_future_outcomes_required(self):
        signals,features=sample()
        columns=set(features.columns)
        self.assertEqual(columns,{"breakout","prior5_gain","atr14_pct","candle_gain"})
        self.assertNotIn("exit_reason",columns)
    def test_ties_preserve_original_rank(self):
        signals,features=sample()
        features.loc[(pd.Timestamp("2026-01-05"),"600003.SH"),"breakout"] = .005
        r=v5.rank_signals(signals,features,"LESS_EXTENDED_BREAKOUT_FIRST")
        self.assertEqual([s for s,_ in next(iter(r.values()))][:2],
                         ["600003.SH","600002.SH"])
    def test_missing_fails_closed(self):
        signals,features=sample()
        features=features.drop((pd.Timestamp("2026-01-05"),"600001.SH"))
        with self.assertRaises(ValueError):
            v5.rank_signals(signals,features,"LESS_EXTENDED_BREAKOUT_FIRST")
    def test_nonfinite_fails_closed(self):
        signals,features=sample()
        features.iloc[0,features.columns.get_loc("breakout")]=float("nan")
        with self.assertRaises(ValueError):
            v5.rank_signals(signals,features,"LESS_EXTENDED_BREAKOUT_FIRST")
    def test_unknown_variant_fails_closed(self):
        signals,features=sample()
        with self.assertRaises(ValueError):v5.rank_signals(signals,features,"POSTHOC_WINNERS")
    def test_trading_paths_both_executed_and_not_subset_deleted(self):
        signals,features=sample()
        fake_sim={"mode":"PAPER_ONLY_NO_BROKER_NO_LIVE_AUTHORIZATION",
            "policy":{"initial_cash_cny":50000}, "trades":[{"entry_date":"2026-01-06",
              "exit_date":"2026-01-08","pnl_cny":-1500,"exit_reason":"STOP_GAP"}],
            "orders":[{"date":"2026-01-06","side":"BUY_ALL","notional_cny":40000}],
            "daily_nav":[{"date":"2026-01-06","equity_cny":50000},
                         {"date":"2026-01-08","equity_cny":48500}],
            "summary":{"closed_trades":1,"realized_pnl_cny":-1500,
                       "total_return":-.03,"max_drawdown":-.03},
            "open_position":None}
        with patch.object(v5,"build_signals",return_value=signals),\
             patch.object(v5,"signal_day_features",return_value=features),\
             patch.object(v5,"filter_signals",return_value=(signals,{"original_candidates":3})),\
             patch.object(v5,"simulate",return_value=fake_sim) as f:
            result=v5.run_rank_comparison(pd.DataFrame(),None,Policy(),"2026-01-01")
        self.assertEqual(f.call_count,3)
        self.assertEqual(len(result["variants"]),3)
        self.assertTrue(all(len(v["trades"])==1 for v in result["variants"]))
        self.assertTrue(all(v["summary"]["max_drawdown"]==-.03 for v in result["variants"]))
    def test_no_zero_baseline(self):
        signals,features=sample()
        fake_sim={"mode":"PAPER_ONLY_NO_BROKER_NO_LIVE_AUTHORIZATION",
          "policy":{"initial_cash_cny":50000},"trades":[],"orders":[],
          "daily_nav":[{"date":"2026-01-06","equity_cny":50000}],
          "summary":{"closed_trades":0},"open_position":None}
        with patch.object(v5,"build_signals",return_value=signals),\
             patch.object(v5,"signal_day_features",return_value=features),\
             patch.object(v5,"filter_signals",return_value=(signals,{})),\
             patch.object(v5,"simulate",return_value=fake_sim):
            with self.assertRaises(ValueError):
                v5.run_rank_comparison(pd.DataFrame(),None,Policy(),"2026-01-01")

if __name__=="__main__":unittest.main()
