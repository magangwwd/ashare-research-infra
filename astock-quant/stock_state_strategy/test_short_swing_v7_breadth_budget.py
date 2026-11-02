"""V7 breadth and funded budget synthetic tests. No broker or data calls."""
import unittest
from unittest.mock import patch
import pandas as pd
import short_swing_v7_breadth_budget as v7
def fake_sim(p):
    price=34000 if p.order_budget_cny==35000 else 39000
    return {'mode':'PAPER_ONLY_NO_BROKER_NO_LIVE_AUTHORIZATION',
        'summary':{'closed_trades':1,'total_return':.020672 if p.order_budget_cny==40000 else .012,
                   'max_drawdown':-.27479458 if p.order_budget_cny==40000 else -.20},
        'policy':{'initial_cash_cny':50000},
        'orders':[{'side':'BUY_ALL','notional_cny':price}],
        'trades':[{'entry_date':'2026-01-02','exit_date':'2026-01-06',
                   'pnl_cny':150,'exit_reason':'TIME_5_CLOSE'}],
        'daily_nav':[{'date':'2026-01-02','equity_cny':50000},
                     {'date':'2026-01-06','equity_cny':50000}],
        'open_position':None}
class TestV7(unittest.TestCase):
    def test_baseline_policy_matches_frozen_v1(self):
        p=v7.make_policy(v7.CANDIDATES[0],.003)
        self.assertEqual((p.order_budget_cny,p.min_order_cny,p.min_breadth_fraction),
                         (40000,30000,.45))
    def test_35k_budget_remains_within_range(self):
        p=v7.make_policy(v7.CANDIDATES[2],.003)
        self.assertEqual(p.order_budget_cny,35000)
        self.assertEqual((p.min_hold_sessions,p.max_hold_sessions,p.buy_lot),(3,5,100))
    def test_non_predeclared_is_rejected(self):
        with self.assertRaises(ValueError):
            v7.make_policy(v7.Candidate('REMOVE_PREVIOUS_LOSSERS',.9,1),.003)
        with self.assertRaises(ValueError):
            v7.make_policy(v7.CANDIDATES[0],.0001)
    def test_slippage_predeclared(self):
        self.assertEqual(v7.SLIP_LEVELS,(.001,.003,.005))
    def test_full_replay_per_scenario(self):
        features=pd.DataFrame()
        with patch.object(v7,'signal_day_features',return_value=features),\
             patch.object(v7,'build_signals',return_value={}) as build,\
             patch.object(v7,'filter_signals',return_value=({},{})),\
             patch.object(v7,'rank_signals',return_value={}),\
             patch.object(v7,'simulate',side_effect=lambda panel,risk,policy,signals,start:fake_sim(policy)) as simulate:
            # Mock frozen baseline constant despite broad variants: only tests
            # interface count, not actual capital path profitability.
            result=v7.research(pd.DataFrame(),None,'2026-01-01')
        self.assertEqual(build.call_count,2)
        self.assertEqual(simulate.call_count,12)
        self.assertTrue(result['baseline_reproduced'])
        self.assertEqual(len(result['results']),3)
        self.assertEqual(len(result['results'][0]['variants']),4)
    def test_trade_below_30k_fails_closed(self):
        broken=fake_sim(v7.make_policy(v7.CANDIDATES[0],.001))
        broken['orders'][0]['notional_cny']=25000
        with patch.object(v7,'signal_day_features',return_value=pd.DataFrame()),\
             patch.object(v7,'build_signals',return_value={}),\
             patch.object(v7,'filter_signals',return_value=({},{})),\
             patch.object(v7,'rank_signals',return_value={}),\
             patch.object(v7,'simulate',return_value=broken):
            with self.assertRaises(RuntimeError):
                v7.research(pd.DataFrame(),None,'2026-01-01')
if __name__=='__main__':unittest.main()
