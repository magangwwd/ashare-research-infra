"""End-to-end Gold schema/freshness/point-in-time tests; real parquet in CI."""
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

import pandas as pd

from supply_demand_momentum.data import load_gold,sha

CFG=json.loads((Path(__file__).parents[1]/'config.json').read_text())


@unittest.skipUnless(importlib.util.find_spec('pyarrow'), 'pyarrow is installed by Actions requirements')
class GoldTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.days=pd.bdate_range(end='2026-11-04',periods=70)
        codes=['SSE_'+str(i) for i in range(502)]
        p=pd.MultiIndex.from_product([codes,self.days],names=['instrument_id','trade_date']).to_frame(index=False)
        p['vendor_symbol']=p['instrument_id'].str.replace('SSE_','',regex=False)+'.SH'
        p=p.assign(open=10.,high=10.5,low=9.5,close=10.,turnover_cny=100000000.,volume_shares=10000000.)
        self.prices=p
        self.write('daily_prices',p)
        self.write('adjustment_factors',p[['instrument_id','trade_date']].assign(adjustment_factor=1.))
        self.write('daily_limits',p[['instrument_id','trade_date']].assign(up_limit=11.,down_limit=9.))
        instruments=pd.DataFrame(dict(instrument_id=codes,board='MAIN_BOARD',exchange='SSE',list_date=pd.Timestamp('2025-01-01'),delist_date=pd.NaT,name_zh='TEST'))
        instruments.to_parquet(self.root/'instruments.parquet',index=False)
        pd.DataFrame(dict(instrument_id=codes,effective_date=pd.Timestamp('2025-01-01'),is_st=False)).to_parquet(self.root/'name_history.parquet',index=False)
        cal=pd.DataFrame(dict(calendar_date=pd.date_range('2025-01-01','2026-12-31'),exchange='SSE'))
        cal['is_open']=cal['calendar_date'].dt.dayofweek<5
        cal.to_parquet(self.root/'trading_calendar.parquet',index=False)
        self.manifest=dict(as_of_date='2026-11-04',files={'instruments.parquet':{'sha256':sha(self.root/'instruments.parquet')}},validation={'primary_keys_unique':True,'ohlc_valid':True})
        (self.root/'manifest.json').write_text(json.dumps(self.manifest))
        self.now=datetime(2026,11,5,6,47,tzinfo=ZoneInfo('Asia/Shanghai'))

    def write(self,name,p):
        d=self.root/name/'year=2026'/'month=11';d.mkdir(parents=True,exist_ok=True)
        p.to_parquet(d/'data.parquet',index=False)

    def test_complete_parquet_schema_and_opening_liquidity(self):
        p,cal,q=load_gold(self.root,CFG,self.now)
        self.assertEqual(q['as_of'],'2026-11-04')
        self.assertEqual(q['latest_rows'],502)
        self.assertEqual(q['eligible_rows'],502)
        self.assertTrue(p.loc[p['trade_date']==p['trade_date'].max(),'entry_eligible'].all())
        self.assertFalse(p.loc[p['trade_date']==p['trade_date'].min(),'entry_eligible'].any())

    def test_future_st_record_does_not_make_past_eligible(self):
        names=pd.read_parquet(self.root/'name_history.parquet')
        names.loc[0,'effective_date']=pd.Timestamp('2026-11-06')
        names.to_parquet(self.root/'name_history.parquet',index=False)
        p,_,q=load_gold(self.root,CFG,self.now)
        self.assertFalse(p[p['instrument_id']==names.iloc[0]['instrument_id']]['eligible'].any())
        self.assertEqual(q['eligible_rows'],501)

    def test_duplicate_partition_keys_rejected(self):
        self.write('daily_prices',pd.concat([self.prices,self.prices.iloc[[0]]]))
        with self.assertRaisesRegex(ValueError,'duplicate_keys'):
            load_gold(self.root,CFG,self.now)

    def test_stale_manifest_cannot_emit_signal(self):
        self.manifest['as_of_date']='2026-11-03'
        (self.root/'manifest.json').write_text(json.dumps(self.manifest))
        with self.assertRaisesRegex(ValueError,'stale_or_future_gold'):
            load_gold(self.root,CFG,self.now)
