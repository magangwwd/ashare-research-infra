"""V4 cohort audit adversarial tests. Synthetic ledgers: no data download."""
import copy
import unittest
from short_swing_3to5_v4_cohort_audit import inspect_variant, group, audit

def ledger():
    ts=[]
    os=[]
    specs=[
       ('2024-01-02','2024-01-03',-100,2,.02,'DEFERRED_STOP_OR_TIME','600351.SH'),
       ('2025-01-02','2025-01-06',300,3,.08,'TARGET_GAP','600002.SH'),
       ('2026-01-05','2026-01-06',-100,2,.04,'STOP_INTRADAY_ASSUMED','600003.SH')]
    for entry,exit,pnl,h,score,reason,sym in specs:
        ts.append({'symbol':sym,'entry_date':entry,'exit_date':exit,'entry_shares':100,
                   'pnl_cny':pnl,'holding_sessions':h,'net_return':pnl/10000,
                   'exit_reason':reason})
        os += [{'side':'BUY_ALL','symbol':sym,'date':entry,'shares':100,'score':score},
               {'side':'SELL_ALL','symbol':sym,'date':exit,'shares':100}]
    return {'name':'NO_GATE_REPRODUCED_V2','trades':ts,'orders':os,
            'summary':{'closed_trades':3,'realized_pnl_cny':100},
            'unclosed_position':False}

class TestCohortAudit(unittest.TestCase):
    def test_nonmutation_and_subset(self):
        a=ledger();old=copy.deepcopy(a)
        result=inspect_variant(a,{'600351.SH'})
        self.assertEqual(a,old)
        self.assertEqual(result['unique_traded_symbols'],3)
        self.assertEqual(result['minute_symbol_overlap'],['600351.SH'])
        self.assertEqual(result['intraday_assumed_exits_missing_minute_symbol'],1)
        self.assertEqual(result['two_session_trades']['pnl_cny'],-200)
        self.assertEqual(result['deferred_exit_trades']['n'],1)
        self.assertEqual(result['target_gap_trades']['pnl_cny'],300)
    def test_pre_2026_median_only(self):
        r=inspect_variant(ledger(),set())
        self.assertEqual(r['training_score_median_2024_25'],.05)
        self.assertEqual(r['descriptive_score_groups_2026']['below_train_median']['n'],1)
        self.assertEqual(r['descriptive_score_groups_2026']['at_or_above_train_median']['n'],0)
    def test_buy_mismatch_fails(self):
        x=ledger();x['orders'][0]['date']='2024-01-08'
        with self.assertRaises(ValueError):inspect_variant(x,set())
    def test_cash_math_mismatch_fails(self):
        x=ledger();x['summary']['realized_pnl_cny']=999
        with self.assertRaises(ValueError):inspect_variant(x,set())
    def test_unclosed_position_fails(self):
        x=ledger();x['unclosed_position']=True
        with self.assertRaises(ValueError):inspect_variant(x,set())
    def test_nan_score_fails(self):
        x=ledger();x['orders'][0]['score']=float('nan')
        with self.assertRaises(ValueError):inspect_variant(x,set())
    def test_future_missing_year_fails(self):
        x=ledger();x['trades'][0]['entry_date']='2027-01-02'
        with self.assertRaises(ValueError):inspect_variant(x,set())
    def test_source_hash_fails_closed(self):
        with self.assertRaises(ValueError):audit(b'{}',{})
    def test_empty_cohort(self):
        self.assertIsNone(group([])['mean_trade_net_return'])

if __name__=='__main__':unittest.main()
