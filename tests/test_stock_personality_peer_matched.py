"""Offline unit tests for peer_matched_v04 (no live market dependency)."""
import importlib.util
from pathlib import Path
import unittest
import numpy as np
import pandas as pd

SCRIPT=Path(__file__).resolve().parents[1]/'skills/stock-personality-analysis/peer_matched_v04.py'
spec=importlib.util.spec_from_file_location('peer_matched_v04',SCRIPT)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class PeerMatchedTest(unittest.TestCase):
    def setUp(self):
        dates=pd.bdate_range('2025-01-02',periods=60)
        records=[]
        for code in ['600031.SH','000157.SZ','000425.SZ','600761.SH','601100.SH']:
            for i,d in enumerate(dates):
                o=10+.02*i+(.01*i if code=='600031.SH' else 0)
                records.append({'ts_code':code,'trade_date':d.strftime('%Y-%m-%d'),
                    'open':o,'high':o+.2,'low':o-.2,'close':o,
                    'vol':10000,'pre_close':o-.02})
        self.panel=pd.DataFrame(records)
        stock={}
        for h in (3,5):
            stock[str(h)]={}
            for pattern,_ in module.KEYS:
                entry=dates[11].strftime('%Y-%m-%d')
                end=dates[11+h].strftime('%Y-%m-%d')
                op=10+.03*11
                ex=10+.03*(11+h)
                trade={'signal_date':dates[10].strftime('%Y-%m-%d'),
                    'entry_date':entry,'exit_date':end,'year':2025,
                    'gross_return_pct':round(100*(ex/op-1),4)}
                stock[str(h)][pattern]={'target_trades':[trade]}
        self.execution={'target':'600031.SH','stocks':{'600031.SH':stock}}
    def test_deterministic_and_peer_alpha(self):
        a=module.audit(self.panel,self.execution,draws=100,seed=123)
        self.assertEqual(a,module.audit(self.panel,self.execution,draws=100,seed=123))
        self.assertEqual(a['rows'],300)
        self.assertGreater(a['results'][0]['mean_target_minus_peer_pp'],0)
        self.assertIsNone(a['results'][0]['same_month_placebo_two_sided_p_exploratory'])
    def test_duplicate_rejected(self):
        with self.assertRaisesRegex(ValueError,'duplicate'):
            module.audit(pd.concat([self.panel,self.panel.iloc[:1]]),self.execution,draws=10)
    def test_missing_preclose_flag(self):
        self.panel.loc[self.panel.index[20],'pre_close']=np.nan
        a=module.audit(self.panel,self.execution,draws=10)
        self.assertEqual(a['missing_pre_close_rows'],1)
    def test_missing_peer_session_rejected(self):
        with self.assertRaisesRegex(ValueError,'misaligned'):
            module.audit(self.panel.drop(index=self.panel.index[0]),self.execution,draws=10)
