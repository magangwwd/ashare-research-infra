"""V4 static-fill audit adversarial checks; synthetic ledgers, zero market downloads."""
import copy
import json
import unittest
from short_swing_3to5_v4_execution_audit import audit, audit_variant

def fixture():
    a={'symbol':'600001.SH','entry_date':'2024-01-02','exit_date':'2024-01-04',
       'entry_shares':100,'gross_entry_cny':1000.,'pnl_cny':100.,
       'exit_reason':'STOP_INTRADAY_ASSUMED'}
    b={'symbol':'600002.SH','entry_date':'2024-01-05','exit_date':'2024-01-09',
       'entry_shares':100,'gross_entry_cny':2000.,'pnl_cny':200.,
       'exit_reason':'TARGET_GAP'}
    orders=[
      {'symbol':'600001.SH','date':'2024-01-02','shares':100,'side':'BUY_ALL'},
      {'symbol':'600001.SH','date':'2024-01-04','shares':100,'side':'SELL_ALL',
       'reason':'STOP_INTRADAY_ASSUMED','execution':'DAILY_OHLC_ASSUMPTION'},
      {'symbol':'600002.SH','date':'2024-01-05','shares':100,'side':'BUY_ALL'},
      {'symbol':'600002.SH','date':'2024-01-09','shares':100,'side':'SELL_ALL',
       'reason':'TARGET_GAP','execution':'OPEN'},
    ]
    return {'name':'NO_GATE_REPRODUCED_V2','summary':{'closed_trades':2,
      'realized_pnl_cny':300,'total_return':.006,'max_drawdown':-.04},
      'trades':[a,b],'orders':orders,'unclosed_position':False}

class V4Audit(unittest.TestCase):
    def test_static_stress_only_affects_assumed_intraday(self):
        v=fixture()
        o=audit_variant(v)
        self.assertEqual(o['intraday_assumed_exit_count'],1)
        self.assertEqual(o['daily_ohlc_assumption_count'],1)
        self.assertEqual(o['stress'][1]['mechanical_pnl_haircut_cny'],5)
        self.assertEqual(o['stress'][1]['approximate_total_pnl_after_haircut_cny'],295)
        self.assertEqual(o['mechanical_pnl_excluding_top_one_cny'],100)
        self.assertEqual(v,fixture())

    def test_dont_silently_mismatch_sell_execution(self):
        v=fixture()
        v['orders'][1]['execution']='OPEN'
        with self.assertRaises(ValueError):audit_variant(v)
    def test_dont_silently_accept_trade_order_mismatch(self):
        v=fixture()
        v['orders'][1]['date']='2024-01-05'
        with self.assertRaises(ValueError):audit_variant(v)
    def test_dont_accept_unknown_reason_or_fictional_position(self):
        v=fixture();v['trades'][0]['exit_reason']='HINDSIGHT'
        with self.assertRaises(ValueError):audit_variant(v)
        v=fixture();v['unclosed_position']=True
        with self.assertRaises(ValueError):audit_variant(v)
    def test_provenance_and_label_guard(self):
        d={'version':'MAINBOARD_SWING_3TO5_STATEFUL_GUARDS_PAPER_V3',
           'mode':'RESEARCH_ONLY_NO_LIVE_AUTHORIZATION','gold_source':{'sha256':'a'*64},
           'period':{'start':'2024-01-01','end':'2026-10-16'},
           'policy':{'initial_cash_cny':50000},
           'datasets':[{'v2_base':'BALANCED','variants':[fixture() for _ in range(5)]},
                       {'v2_base':'LOOSE','variants':[fixture() for _ in range(5)]}]}
        out=audit(d,b'original-json-content')
        self.assertEqual(out['mode'],'POST_HOC_SENSITIVITY_NOT_EXECUTION_SIMULATION')
        self.assertEqual(len(out['datasets']),2)
        d['gold_source']={}
        with self.assertRaises(ValueError):audit(d,b'bad')
        d['gold_source']={'sha256':'a'*64};d['mode']='LIVE_TRADING'
        with self.assertRaises(ValueError):audit(d,b'bad')

if __name__=='__main__':unittest.main()
