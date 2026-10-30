"""Causal forward-paper checks, with no future session or connected service."""
import unittest
import pandas as pd
from paper_forward_r0 import verified_as_of,snapshot
from gold_pit_risk_guards import GoldPITRisk
from collections import defaultdict
import json
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch, Mock
import paper_forward_r0 as r0

class ForwardPaperTests(unittest.TestCase):
    def test_cannot_backdate_pre_inspection_prices(self):
        with self.assertRaises(RuntimeError):
            verified_as_of({"as_of_date":"2026-10-09","validation":{"primary_keys_unique":True}},
                           pd.Timestamp("2026-10-09"))
    def test_cannot_use_stale_or_future_manifests(self):
        for source in ("2026-10-09","2026-10-13"):
            with self.subTest(source=source),self.assertRaises(RuntimeError):
                verified_as_of({"as_of_date":source,"validation":{"primary_keys_unique":True}},
                    pd.Timestamp("2026-10-12"))
    def test_requires_manifest_integrity(self):
        with self.assertRaises(RuntimeError):
            verified_as_of({"as_of_date":"2026-10-12","validation":{}},
                pd.Timestamp("2026-10-12"))
    def test_today_date_is_necessary_but_not_sufficient(self):
        verified_as_of({"as_of_date":"2026-10-12","validation":{"primary_keys_unique":True}},
            pd.Timestamp("2026-10-12"))

    def run_first_observation(self, final_minute):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        root=Path(temporary.name);manifest=root/'manifest.json'
        manifest.write_text(json.dumps({'as_of_date':'2026-10-29','validation':{'primary_keys_unique':True}}))
        calendar=pd.DataFrame({'calendar_date':pd.to_datetime(['2026-10-29','2026-10-30']),
                               'is_open':[True,True]})
        risk=Mock();risk.limits={('TEST',pd.Timestamp('2026-10-29')):None};risk.summary.return_value={}
        argv=['paper_forward_r0.py','--manifest',str(manifest),'--market-root',str(root),
              '--risk-root',str(root),'--as-of','2026-10-29','--output',str(root/'output')]
        with patch('sys.argv',argv),patch.object(r0.pd,'read_parquet',return_value=calendar),\
             patch.object(r0,'recent_panel',return_value=pd.DataFrame({'trade_date':[pd.Timestamp('2026-10-29')]})),\
             patch.object(r0,'GoldPITRisk',return_value=risk),patch.object(r0,'snapshot',return_value=[]),\
             patch.object(r0,'datetime') as clock:
            clock.now.side_effect=[datetime(2026,10,30,1,14,tzinfo=timezone.utc),
                                   datetime(2026,10,30,1,final_minute,tzinfo=timezone.utc)]
            target=root/'output/signal_snapshot_2026-10-29.json'
            if final_minute>=15:
                with self.assertRaisesRegex(RuntimeError,'window closed'):r0.main()
                self.assertFalse(target.exists())
                return None
            r0.main()
            return json.loads(target.read_text())

    def test_real_main_refuses_write_if_computation_crosses_auction(self):
        self.run_first_observation(16)

    def test_real_main_records_first_observed_time_before_auction(self):
        record=self.run_first_observation(14)
        self.assertEqual(record['first_observed_at_utc'],'2026-10-30T01:14:00+00:00')

if __name__=="__main__": unittest.main()
