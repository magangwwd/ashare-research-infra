"""Actual Parquet -> state output -> verified cache smoke tests; no services."""
import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import state_definition_pool as states
from state_transition_backtest import build_or_load_state_history
from test_disclosure_counts_pit import fixtures


class StatePipelineParquetSmokeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.market = self.root / 'gold'; self.market.mkdir()
        self.days = pd.bdate_range(end='2026-10-29', periods=300)
        n=len(self.days); price=10+np.arange(n)*0.01
        identity={'instrument_id':['I1']*n, 'trade_date':self.days}
        self.prices=pd.DataFrame({**identity,'vendor_symbol':'600000.SH','open':price-.02,
            'high':price+.1,'low':price-.1,'close':price,'turnover_cny':2e8,'is_quality_valid':True})
        self.write_dataset('daily_prices',self.prices)
        self.write_dataset('daily_metrics',pd.DataFrame({**identity,'turnover_rate_pct':1.2,'float_market_cap_cny':1e10}))
        self.write_dataset('adjustment_factors',pd.DataFrame({**identity,'adjustment_factor':1.0}))
        self.write_dataset('daily_limits',pd.DataFrame({**identity,'up_limit':price*1.1,'down_limit':price*.9}))
        pd.DataFrame({'calendar_date':self.days,'is_open':True}).to_parquet(self.market/'trading_calendar.parquet',index=False)
        pd.DataFrame({'instrument_id':['I1'],'vendor_symbol':['600000.SH'],'name_zh':['TEST'],
            'industry_vendor':['TEST'],'board':['MAIN_BOARD'],'exchange':['XSHG'],
            'list_date':pd.to_datetime(['2010-01-01']),'delist_date':[pd.NaT],
            'listing_status':['LISTED']}).to_parquet(self.market/'instruments.parquet',index=False)
        pd.DataFrame({'instrument_id':['I1'],'effective_date':pd.to_datetime(['2010-01-01']),
            'end_date':[pd.NaT],'announcement_date':pd.to_datetime(['2009-12-31']),
            'is_st':[False]}).to_parquet(self.market/'name_history.parquet',index=False)
        (self.market/'manifest.json').write_text(json.dumps({'as_of_date':'2026-10-29'}))
        _,self.events=fixtures()
        self.events['ann_date']=pd.to_datetime(['2026-08-01','2026-09-01'])
        self.events['holder_report_end']=pd.to_datetime(['2026-06-30','2026-06-30'])
        self.events['composition_report_end']=pd.to_datetime(['2026-06-30','2026-06-30'])
        processed=self.root/'processed';processed.mkdir()
        self.events.to_parquet(processed/'ownership_events.parquet',index=False)
        self.config=self.root/'config.json'
        self.config.write_text(json.dumps({'market_data_root':str(self.market),
            'processed_data_dir':str(processed),'research_end':'2026-10-29','output_dir':str(self.root/'output')}))

    def write_dataset(self,name,frame):
        folder=self.market/name;folder.mkdir(exist_ok=True)
        frame.to_parquet(folder/'data.parquet',index=False)

    def run_state(self):
        args=argparse.Namespace(config=str(self.config),as_of='2026-10-29',
            history_start=str(self.days[0].date()),output_dir=str(self.root/'output'))
        with patch.object(states,'parse_args',return_value=args):return states.main()

    def test_real_parquet_state_and_diagnostics_share_exact_date(self):
        self.assertEqual(self.run_state(),0)
        summary=json.loads((self.root/'output/summary.json').read_text())
        self.assertEqual(summary['as_of_date'],'2026-10-29')
        quality=json.loads((self.root/'output/ownership_quality_summary.json').read_text())
        self.assertEqual(quality['ownership_failed_current_rows'],0)
        self.assertEqual(sum(quality['primary_reason_counts'].values()),summary['listed_universe_rows'])

    def test_stale_prices_never_generate_new_day_report(self):
        self.write_dataset('daily_prices',self.prices.iloc[:-1])
        with self.assertRaisesRegex(RuntimeError,'mismatch'):self.run_state()
        self.assertFalse((self.root/'output/summary.json').exists())

    def test_actual_state_cache_reuse_and_changed_announcements_refusal(self):
        path=self.root/'cache.parquet';end=pd.Timestamp('2026-10-29')
        first,_=build_or_load_state_history(path,self.market,self.events,end,True)
        second,audit=build_or_load_state_history(path,self.market,self.events,end,False)
        self.assertTrue(audit['loaded_cache'])
        pd.testing.assert_frame_equal(first,second)
        changed=self.events.copy();changed.loc[0,'holder_num']+=1
        with self.assertRaisesRegex(RuntimeError,'rebuild'):
            build_or_load_state_history(path,self.market,changed,end,False)

    def test_same_day_price_correction_is_used_instead_of_cached_return(self):
        self.run_state()
        original=pd.read_parquet(self.root/'output/current_snapshot.parquet')['ret_20_behavior'].iloc[0]
        corrected=self.prices.copy()
        corrected.loc[corrected.index[-1],['open','high','low','close']]-=1
        self.write_dataset('daily_prices',corrected)
        self.run_state()
        updated=pd.read_parquet(self.root/'output/current_snapshot.parquet')['ret_20_behavior'].iloc[0]
        self.assertLess(updated,original)
        expected=corrected['close'].iloc[-1]/corrected['close'].iloc[-21]-1
        self.assertAlmostEqual(updated,expected,places=6)

    def test_unchanged_recent_cache_reuses_but_corrupted_bytes_rebuild(self):
        self.run_state()
        with patch.object(states,'load_market_history',side_effect=AssertionError('unexpected rebuild')):
            self.run_state()
        (self.root/'output/recent_feature_cache.parquet').write_bytes(b'partial write')
        self.assertEqual(self.run_state(),0)

    def test_same_manifest_with_changed_market_partition_refuses_historical_cache(self):
        path=self.root/'cache.parquet';end=pd.Timestamp('2026-10-29')
        build_or_load_state_history(path,self.market,self.events,end,True)
        corrected=self.prices.copy();corrected.loc[corrected.index[-1],'close']-=.01
        self.write_dataset('daily_prices',corrected)
        with self.assertRaisesRegex(RuntimeError,'rebuild'):
            build_or_load_state_history(path,self.market,self.events,end,False)


if __name__ == '__main__':unittest.main()
