"""Synthetic, no-future-information V6 filter checks. No download or broker."""
import unittest
from unittest.mock import patch
import pandas as pd
from short_swing_v6_preentry_cost_gates import (causal_gate,evaluate,VARIANTS,SLIPPAGES)
def fixture():
    day=pd.Timestamp('2026-01-06')
    symbols=['600001.SH','600002.SH','600003.SH','600004.SH']
    f=pd.DataFrame({'prior5_gain':[.02,.07,.09,.12],
                    'breakout':[.01,.03,.01,.04],
                    'atr14_pct':[.02]*4,
                    'candle_gain':[.01]*4},
                   index=pd.MultiIndex.from_tuples([(day,s) for s in symbols]))
    sig={day:[(s,.1-i*.01) for i,s in enumerate(symbols)]}
    return sig,f
class GateTests(unittest.TestCase):
    def test_caps_do_not_rewrite_original_rank(self):
        s,f=fixture()
        self.assertEqual(causal_gate(s,f,VARIANTS[0])[0],s)
        self.assertEqual([x[0] for x in causal_gate(s,f,VARIANTS[1])[0][next(iter(s))]],
                         ['600001.SH','600002.SH'])
        self.assertEqual([x[0] for x in causal_gate(s,f,VARIANTS[2])[0][next(iter(s))]],
                         ['600001.SH','600003.SH'])
    def test_missing_feature_rejected(self):
        s,f=fixture();f=f.iloc[1:]
        with self.assertRaises(ValueError):causal_gate(s,f,VARIANTS[1])
    def test_nan_feature_rejected(self):
        s,f=fixture();f.iloc[0,f.columns.get_loc('breakout')]=float('nan')
        with self.assertRaises(ValueError):causal_gate(s,f,VARIANTS[0])
    def test_posthoc_variant_rejected(self):
        s,f=fixture()
        with self.assertRaises(ValueError):causal_gate(s,f,'EXCLUDE_PREVIOUS_LOSERS')
    def test_keeps_original_candidate_identity(self):
        s,f=fixture()
        for mode in VARIANTS:
            out,stats=causal_gate(s,f,mode)
            self.assertEqual(stats['before'],4)
            self.assertEqual(stats['after']+stats['removed'],4)
            self.assertTrue(set(out[next(iter(s))])<=set(s[next(iter(s))]))
    def test_policy_stress_levels_are_predeclared(self):
        self.assertEqual(SLIPPAGES,(.001,.003,.005))
    def test_executes_full_backtest_per_variant(self):
        s,f=fixture()
        sim={'mode':'PAPER_ONLY_NO_BROKER_NO_LIVE_AUTHORIZATION',
             'trades':[{'entry_date':'2026-01-08','exit_date':'2026-01-09',
                        'exit_reason':'STOP_GAP'}],
             'daily_nav':[{'date':'2026-01-08','equity_cny':50000}],
             'orders':[], 'open_position':None,
             'policy':{'initial_cash_cny':50000},
             'summary':{'closed_trades':1,'total_return':-.05,'max_drawdown':-.05}}
        class Policy:pass
        with patch('short_swing_v6_preentry_cost_gates.build_signals',return_value=s),\
             patch('short_swing_v6_preentry_cost_gates.signal_day_features',return_value=f),\
             patch('short_swing_v6_preentry_cost_gates.filter_signals',return_value=(s,{})),\
             patch('short_swing_v6_preentry_cost_gates.rank_signals',return_value=s),\
             patch('short_swing_v6_preentry_cost_gates.simulate',return_value=sim) as called:
            r=evaluate(pd.DataFrame(),None,Policy(),'2026-01-01')
            self.assertEqual(called.call_count,3)
            self.assertEqual(len(r['runs']),3)
    def test_no_closed_trade_not_accepted(self):
        s,f=fixture()
        sim={'mode':'PAPER_ONLY_NO_BROKER_NO_LIVE_AUTHORIZATION',
            'trades':[],'orders':[], 'open_position':None,
            'policy':{'initial_cash_cny':50000},
            'daily_nav':[{'date':'2026-01-08','equity_cny':50000}],
            'summary':{'closed_trades':0}}
        class Policy:pass
        with patch('short_swing_v6_preentry_cost_gates.build_signals',return_value=s),\
             patch('short_swing_v6_preentry_cost_gates.signal_day_features',return_value=f),\
             patch('short_swing_v6_preentry_cost_gates.filter_signals',return_value=(s,{})),\
             patch('short_swing_v6_preentry_cost_gates.rank_signals',return_value=s),\
             patch('short_swing_v6_preentry_cost_gates.simulate',return_value=sim):
            with self.assertRaises(RuntimeError):
                evaluate(pd.DataFrame(),None,Policy(),'2026-01-01')
if __name__=='__main__':unittest.main()
