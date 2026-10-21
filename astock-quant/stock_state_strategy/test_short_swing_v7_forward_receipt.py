"""Synthetic frozen-V7 prospective receipt tests; never call market or broker."""
import unittest
from unittest.mock import patch
from datetime import date,datetime,timedelta,timezone
import pandas as pd
import short_swing_v7_forward_receipt as f

DAY=date(2026,10,21)
NOW=datetime(2026,10,21,16,20,tzinfo=timezone(timedelta(hours=8)))

def panel_for():
    p=pd.DataFrame({'vendor_symbol':[f'600{i:03d}.SH' for i in range(500)],
                    'trade_date':['2026-10-21']*500})
    p.loc[1,'vendor_symbol']='600001.SH'
    return p

def fake_features():
    idx=pd.MultiIndex.from_tuples([(pd.Timestamp(DAY),'600001.SH')])
    return pd.DataFrame({'prior5_gain':[.035],'breakout':[.012]},index=idx)

class ForwardReceiptTests(unittest.TestCase):
    def test_first_eligible_close_and_before_open(self):
        f.require_true_forward_clock(DAY,NOW)
        with self.assertRaises(ValueError):
            f.require_true_forward_clock(date(2026,10,20),NOW)
        with self.assertRaises(ValueError):
            f.require_true_forward_clock(DAY,datetime(2026,10,21,15,0,tzinfo=timezone(timedelta(hours=8))))
    def test_retroactive_or_naive_clock_fails(self):
        with self.assertRaises(ValueError):
            f.require_true_forward_clock(DAY,datetime(2026,10,22,19,tzinfo=timezone(timedelta(hours=8))))
        with self.assertRaises(ValueError):
            f.require_true_forward_clock(DAY,datetime(2026,10,21,16))
    def test_frozen_entry_limits(self):
        self.assertEqual(f.POLICY.order_budget_cny,40000)
        self.assertEqual(f.POLICY.min_breadth_fraction,.55)
        self.assertEqual(f.POLICY.slippage_rate,.003)
        self.assertEqual((f.POLICY.min_hold_sessions,f.POLICY.max_hold_sessions),(3,5))
    def test_forward_record_is_orderless_and_hashed(self):
        ix=pd.Timestamp(DAY);pick={ix:[('600001.SH',.03)]}
        with patch.object(f,'signal_day_features',return_value=fake_features()),\
             patch.object(f,'build_signals',return_value=pick),\
             patch.object(f,'filter_signals',return_value=(pick,{})),\
             patch.object(f,'rank_signals',return_value=pick):
            out=f.receipt(panel_for(),None,DAY,'x'*64,'2026-10-21',NOW)
            repeat=f.receipt(panel_for(),None,DAY,'x'*64,'2026-10-21',NOW)
        self.assertEqual(out['sha256_of_receipt_fields'],repeat['sha256_of_receipt_fields'])
        self.assertEqual(out['decision'],'CANDIDATES_ONLY_VERIFY_NEXT_OPEN')
        self.assertEqual(out['top10_candidates_in_priority_order'][0]['symbol'],'600001.SH')
        self.assertEqual(out['mode'],'PAPER_CANDIDATE_SNAPSHOT_ONLY_NO_ORDER')
    def test_no_candidate_is_wait_not_fake_buy(self):
        with patch.object(f,'signal_day_features',return_value=fake_features()),\
             patch.object(f,'build_signals',return_value={}),\
             patch.object(f,'filter_signals',return_value=({},{})),\
             patch.object(f,'rank_signals',return_value={}):
            out=f.receipt(panel_for(),None,DAY,'a'*64,'2026-10-21',NOW)
        self.assertEqual(out['decision'],'WAIT')
        self.assertEqual(out['candidate_count'],0)
    def test_old_manifest_rejected(self):
        with self.assertRaises(ValueError):
            f.receipt(panel_for(),None,DAY,'a'*64,'2026-10-20',NOW)
    def test_too_few_symbols_rejected_not_wait(self):
        x=panel_for().iloc[:450]
        with self.assertRaises(ValueError):
            f.receipt(x,None,DAY,'a'*64,'2026-10-21',NOW)
    def test_mismatched_last_panel_day_fails(self):
        x=panel_for();x['trade_date']='2026-10-20'
        with self.assertRaises(ValueError):
            f.receipt(x,None,DAY,'a'*64,'2026-10-21',NOW)
    def test_next_business_morning_before_open(self):
        f.require_true_forward_clock(DAY,datetime(2026,10,22,8,0,tzinfo=timezone(timedelta(hours=8))))
        f.require_true_forward_clock(date(2026,10,23),
            datetime(2026,10,26,8,0,tzinfo=timezone(timedelta(hours=8))))
        for clock in [datetime(2026,10,22,9,25,tzinfo=timezone(timedelta(hours=8))),
                      datetime(2026,10,22,6,59,tzinfo=timezone(timedelta(hours=8))),
                      datetime(2026,10,23,8,0,tzinfo=timezone(timedelta(hours=8)))]:
            with self.assertRaises(ValueError):
                f.require_true_forward_clock(DAY,clock)
    def test_weekday_holiday_gap_must_not_be_silently_accepted(self):
        with self.assertRaises(ValueError):
            f.require_true_forward_clock(date(2026,10,23),
                datetime(2026,10,27,8,0,tzinfo=timezone(timedelta(hours=8))))
    def test_code_sha_in_offline_receipt(self):
        ix=pd.Timestamp(DAY);pick={ix:[('600001.SH',.03)]}
        with patch.object(f,'signal_day_features',return_value=fake_features()),\
             patch.object(f,'build_signals',return_value=pick),\\
             patch.object(f,'filter_signals',return_value=(pick,{})),\\
             patch.object(f,'rank_signals',return_value=pick),\\
             patch.dict('os.environ',{'V7_RESEARCH_COMMIT_SHA':'a'*40}):
            out=f.receipt(panel_for(),None,DAY,'x'*64,'2026-10-21',NOW)
        self.assertEqual(out['research_code_sha'],'a'*40)
if __name__=='__main__':unittest.main()
