"""Synthetic off-exchange tests. The Action's real-data job also checks exact
VOL_BASE and VOL_DD5 account equity/trade parity against previous vol study.
"""
from datetime import date,timedelta
import importlib.util
import math
from pathlib import Path
import sys
from unittest import TestCase

MODULE=Path(__file__).resolve().parents[1]/'regime'/'vol_noadd_reentry_study.py'
spec=importlib.util.spec_from_file_location('btc_vol_noadd_reentry_tests',MODULE)
m=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=m
spec.loader.exec_module(m)


def synthetic(n=1150):
    out=[];start=date(2023,1,1)
    for i in range(n):
        level=10000*math.exp(.0003*i + .018*math.sin(i/11)-.012*max(0,i-1000))
        prev=out[-1]['close'] if out else level
        out.append({'date':start+timedelta(days=i),'open':prev,
                    'high':max(prev,level)*1.01,'low':min(prev,level)*.99,
                    'close':level,'volume':100.0})
    return out


class Tests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows=synthetic();cls.f=m.features(cls.rows);cls.folds=m.folds_for(cls.rows)

    def test_continuous_single_account_and_next_day_only(self):
        for name in m.NAMES:
            result,curve,trades,events,folds=m.simulate(self.rows,self.folds,self.f,name,1)
            self.assertTrue(all(t['signal_asof']<t['fill_date'] for t in trades),name)
            self.assertTrue(all(x['cash_usdt']>=-1e-7 and x['btc_qty']>=-1e-10 for x in curve),name)
            self.assertTrue(all(0<=x['btc_weight']<=.51 for x in curve),name)
            self.assertTrue(all(folds[i]['start_nav']==folds[i-1]['end_nav'] for i in range(1,len(folds))),name)
            self.assertEqual(result['ending_nav'],curve[-1]['nav'])

    def test_noadd_veto_never_places_buy_on_veto_execution_date(self):
        for mult in (1,2):
            for name in m.NOADD:
                _,_,trades,events,_=m.simulate(self.rows,self.folds,self.f,name,mult)
                veto_dates={e['execution_date'] for e in events if e['event']=='VETO_BUY_DEEP_REDUCED'}
                self.assertFalse(any(t['fill_date'] in veto_dates and t['side']=='BUY' for t in trades))

    def test_reentry_confirmations_are_causal(self):
        for name in m.STAGED | m.GLOBAL_REENTRY:
            _,_,_,events,_=m.simulate(self.rows,self.folds,self.f,name,1)
            for event in events:
                if 'STAGE_' in event['event']:
                    self.assertEqual(date.fromisoformat(event['execution_date']).weekday(),0)
                    self.assertLess(event['signal_asof'],event['execution_date'])

    def test_last_close_change_does_not_change_historical_orders(self):
        alt=[dict(r) for r in self.rows]
        alt[-1]['close']*=.7
        for name in m.NAMES:
            _,a,orders,_,_=m.simulate(self.rows,self.folds,self.f,name,1)
            _,b,altorders,_,_=m.simulate(alt,self.folds,m.features(alt),name,1)
            self.assertEqual(a[:-1],b[:-1],name)
            self.assertEqual(orders,altorders,name)