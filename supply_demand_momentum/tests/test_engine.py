import json
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from supply_demand_momentum.engine import initial_state,step,fee,zones,signal_for_stock
from supply_demand_momentum.data import track_events
from supply_demand_momentum.run import canonical,annotate_signals
from datetime import datetime
from zoneinfo import ZoneInfo

CFG=json.loads((Path(__file__).parents[1]/'config.json').read_text())


def signal(code='600000.SH',event='event1',day='2026-11-02'):
    return dict(code=code,event_id=event,signal_date=day,score=9,entry_limit=10.1,
                stop=9.,target=14.,reference_factor=1.,mode=CFG['mode'])


def bar(**overrides):
    b=dict(open=10.,high=10.4,low=9.8,close=10.1,limit_up=11.,limit_down=9.,
           adjustment_factor=1.,eligible=True,suspended=False,one_word_up=False,one_word_down=False)
    b.update(overrides)
    return b


class PaperTests(unittest.TestCase):
    def pending(self):
        s,_=step(initial_state(CFG),'2026-11-02',{},[signal()],CFG)
        return s

    def held(self):
        s,a=step(self.pending(),'2026-11-03',{'600000.SH':bar()},[],CFG)
        self.assertEqual(len(s['positions']),1)
        return s

    def test_no_same_day_fill(self):
        s=self.pending()
        self.assertEqual(s['trades'],[])
        self.assertEqual(len(s['pending']),1)

    def test_fees_round_lots_buffer(self):
        s=self.held();p=s['positions'][0]
        self.assertEqual(p['quantity']%100,0)
        self.assertLessEqual(p['cost'],30000)
        self.assertGreaterEqual(s['cash'],10000)
        self.assertGreater(p['cost'],p['quantity']*p['entry_price'])

    def test_t_plus_one_even_if_buy_bar_hits_stop(self):
        s,a=step(self.pending(),'2026-11-03',{'600000.SH':bar(low=8.)},[],CFG)
        self.assertEqual([x['kind'] for x in a],['PAPER_BUY','HOLD_WATCH'])
        self.assertEqual(len(s['positions']),1)

    def test_duplicate_date_idempotent(self):
        s=self.held()
        out,a=step(s,'2026-11-03',{},[],CFG)
        self.assertEqual(out,s)
        self.assertEqual(a,[])

    def test_gap_stop_uses_gap_price(self):
        s=self.held()
        out,a=step(s,'2026-11-04',{'600000.SH':bar(open=8.,low=7.5,high=8.3,close=8.,limit_down=7.)},[],CFG)
        trade=out['trades'][-1]
        self.assertEqual(trade['reason'],'STOP')
        self.assertLess(trade['price'],8.)
        self.assertEqual(len(out['positions']),0)

    def test_both_stop_target_chooses_stop(self):
        s=self.held()
        out,_=step(s,'2026-11-04',{'600000.SH':bar(low=8.8,high=15.,limit_down=8.)},[],CFG)
        self.assertEqual(out['trades'][-1]['reason'],'STOP')

    def test_five_limit_down_days_never_forces_fill(self):
        s=self.held()
        for day in pd.bdate_range('2026-11-04',periods=7):
            s,a=step(s,str(day.date()),{'600000.SH':bar(one_word_down=True,low=8.5,open=8.5,high=8.5,close=8.5)},[],CFG)
            self.assertEqual(len(s['positions']),1)
            self.assertEqual(a[0]['kind'],'SELL_BLOCKED')
        self.assertEqual(len(s['trades']),1)

    def test_no_lot_no_fill(self):
        sig=dict(signal(),entry_limit=500,stop=400,target=800)
        s=initial_state(CFG);s['pending']=[sig]
        out,a=step(s,'2026-11-03',{'600000.SH':bar(open=450,high=470,low=440,close=460,limit_down=400,limit_up=500)},[],CFG)
        self.assertEqual(len(out['positions']),0)
        self.assertEqual(a[0]['reason'],'NO_AFFORDABLE_LOT')

    def test_three_slots_skip_fourth_no_dilution(self):
        s=initial_state(CFG)
        s['pending']=[signal(str(i),str(i)) for i in range(4)]
        s,a=step(s,'2026-11-03',{str(i):bar() for i in range(4)},[],CFG)
        self.assertEqual(len(s['positions']),3)
        self.assertGreaterEqual(s['cash'],10000)
        self.assertEqual([x for x in a if x['kind']=='SKIP'][0]['reason'],'NO_SLOT')

    def test_gap_above_limit_no_fill(self):
        s,a=step(self.pending(),'2026-11-03',{'600000.SH':bar(open=10.5)},[],CFG)
        self.assertEqual(len(s['positions']),0)
        self.assertEqual(a[0]['reason'],'OPEN_OUTSIDE_ENTRY_LIMIT')

    def test_corporate_action_blocks_fake_pnl(self):
        s=self.held()
        with self.assertRaisesRegex(ValueError,'corporate_action'):
            step(s,'2026-11-04',{'600000.SH':bar(adjustment_factor=1.2)},[],CFG)

    def test_missing_hold_never_disappears(self):
        with self.assertRaisesRegex(ValueError,'held_stock'):
            step(self.held(),'2026-11-04',{},[],CFG)

    def test_nonfinite_bundle_rejected(self):
        with self.assertRaises(ValueError):
            canonical({'price':float('nan')})

    def test_event_track_waits_future_sessions(self):
        e=signal()
        cal=pd.Series(pd.bdate_range('2026-11-02',periods=25))
        df=pd.DataFrame([dict(vendor_symbol=e['code'],trade_date=pd.Timestamp('2026-11-03'),o=10.,c=11.)])
        r=track_events([e],df,cal,'2026-11-03')[0]
        self.assertAlmostEqual(r['1_session']['return_pct'],10.)
        self.assertEqual(r['3_session']['status'],'NOT_MATURED')

    def test_late_run_never_backfills_opening_order(self):
        cal=pd.Series(pd.bdate_range('2026-11-02',periods=5))
        signals=annotate_signals([signal()],cal,'2026-11-02',datetime(2026,11,3,10,0,tzinfo=ZoneInfo('Asia/Shanghai')))
        s,a=step(initial_state(CFG),'2026-11-02',{},signals,CFG)
        self.assertEqual(s['pending'],[])
        self.assertEqual(a[0]['kind'],'EXPIRED_SIGNAL')

    def test_preopen_run_can_schedule_next_session(self):
        cal=pd.Series(pd.bdate_range('2026-11-02',periods=5))
        signals=annotate_signals([signal()],cal,'2026-11-02',datetime(2026,11,3,8,0,tzinfo=ZoneInfo('Asia/Shanghai')))
        self.assertEqual(signals[0]['action_status'],'PENDING')


class SignalTests(unittest.TestCase):
    def data(self):
        n=80;c=np.linspace(8,12,n);c[-1]=10.
        return pd.DataFrame(dict(o=c-.1,h=c+.3,l=c-.3,c=c,volume_shares=np.ones(n)*10000,
                   trade_date=pd.bdate_range('2026-07-01',periods=n),eligible=True,
                   vendor_symbol='600000.SH',instrument_id='SSE_600000',name_zh='TEST',adjustment_factor=1.))

    def custom_zones(self):
        return [dict(kind='demand',pattern='DBR',proximal=10.2,distal=9.8,base_idx=60,confirmed_idx=63,base_date='2026-09-23',strength=2),
                dict(kind='supply',pattern='RBD',proximal=14.,distal=15.,base_idx=35,confirmed_idx=38,base_date='2026-08-19',strength=2)]

    def test_confirmation_bars_not_retest(self):
        g=self.data();z=self.custom_zones();z[0]['confirmed_idx']=len(g)-1
        with patch('supply_demand_momentum.engine.zones',return_value=z):
            self.assertEqual(signal_for_stock(g,CFG),[])

    def test_known_untested_target_produces_scored_signal(self):
        g=self.data()
        with patch('supply_demand_momentum.engine.zones',return_value=self.custom_zones()):
            s=signal_for_stock(g,CFG)
        self.assertEqual(len(s),1)
        self.assertEqual(s[0]['score'],10)
        self.assertGreaterEqual(s[0]['rr'],3)
        self.assertEqual(s[0]['target'],14.)

    def test_tested_supply_not_target(self):
        g=self.data();g.loc[50,'c']=14.5
        with patch('supply_demand_momentum.engine.zones',return_value=self.custom_zones()):
            self.assertEqual(signal_for_stock(g,CFG),[])

    def test_unknown_eligibility_rejects(self):
        g=self.data();g.loc[len(g)-1,'eligible']=False
        self.assertEqual(signal_for_stock(g,CFG),[])

    def test_zone_confirmation_never_precedes_departure(self):
        g=self.data()
        for z in zones(g,CFG):
            self.assertEqual(z['confirmed_idx'],z['base_idx']+3)
            self.assertLess(z['confirmed_idx'],len(g))

    def test_prefix_zones_unchanged_by_future_prices(self):
        rng=np.random.default_rng(9);n=500
        c=np.cumsum(rng.normal(0,.1,n))+20
        o=c-.4;h=c+.1;l=o-.1
        for start in (30,90,180,280,390):
            o[start:start+4]=c[start:start+4]-.01
            h[start:start+4]=c[start:start+4]+.03
            l[start:start+4]=c[start:start+4]-.03
        g=pd.DataFrame(dict(o=o,h=h,l=l,c=c,
                    volume_shares=np.ones(n)*10000,trade_date=pd.bdate_range('2024-01-01',periods=n)))
        prefix=zones(g.iloc[:350],CFG)
        full=[z for z in zones(g,CFG) if z['confirmed_idx']<350]
        self.assertGreaterEqual(len(prefix),3)
        self.assertEqual(prefix,full)


if __name__=='__main__':
    unittest.main()
