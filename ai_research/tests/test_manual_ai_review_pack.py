"""Offline tests: no network, LLM, credentials or original research strategy calls."""
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from ai_research.manual_ai_review_pack import (AUTHORIZATION, DATASETS, PERIODS,
                                                 SYMBOLS, package, instruction)

try:
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:
    pd = None
    pq = None


@unittest.skipIf(pd is None or pq is None, "pandas + pyarrow are optional in lightweight CI")
class ManualAiEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.clean = self.root / "clean"
        self.clean.mkdir()
        self.report = self.root / "report.json"
        self.latest = self.root / "latest.json"
        self.review_clock = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)
        market = {"version":"test","as_of":"2026-10-09",
                  "mode":"DETERMINISTIC_MULTI_ROLE_NO_LLM_NO_NETWORK",
                  "authorization":AUTHORIZATION,"stocks":[]}
        for i,sym in enumerate(SYMBOLS):
            market["stocks"].append({
                "symbol":sym,"name":"test-"+sym,"research_rank":i+1,
                "original_state":"模拟状态","technical_agent":{
                    "status":"CALCULATED_GOLD_PIT",
                    "facts":{"return_20_sessions_pct":5.1},
                    "flags":[]},
                "fundamental_agent":{"status":"LEGACY_FALLBACK_CLEAN_GAP","facts":{"roe_pct":999}},
                "news_agent":{"evidence":[]},
            })
        self.report.write_text(json.dumps(market),encoding="utf-8")
        self.populate_finance()

    def populate_finance(self):
        partitions={}
        for dataset in DATASETS:
            for period in PERIODS:
                rows=[]
                for sym in SYMBOLS:
                    rows.append({
                        "ts_code":sym,
                        "report_period":"2026-03-31" if period=="20260331" else "2026-06-30",
                        "ann_date":"2026-04-30" if period=="20260331" else "2026-08-01",
                        "actual_ann_date":None,"available_date":None,"schema_version":"1.0.0",
                        "total_revenue":1e8,"n_income_attr_p":1e7,"operate_profit":1.5e7,
                        "total_assets":8e8,"total_liab":3e8,
                        "total_cur_assets":3e8,"total_cur_liab":1e8,
                        "n_cashflow_act":3e7,"roe":5.2,
                        "debt_to_assets":37.5,"current_ratio":3.0,
                    })
                path=self.clean/(dataset+"_"+period+".parquet")
                pd.DataFrame(rows).to_parquet(path,index=False)
                partitions[path.name]={"rows":5,"bytes":path.stat().st_size,
                                       "sha256":hashlib.sha256(path.read_bytes()).hexdigest()}
        current={
            "version":"five-clean-12345","validated":True,"target_scope_complete":True,
            "snapshot_as_of":"2026-10-12","published_at":"2026-10-12T07:53:38+00:00",
            "target_symbols":list(SYMBOLS),"report_periods":list(PERIODS),
            "datasets":list(DATASETS),"authorization":AUTHORIZATION}
        acceptance={"passed":True,"groups":40,"partitions":partitions}
        audit={"status":"COMPLETE","valid":40,"issues":[],
               "files":partitions,"records":[{"k":i} for i in range(40)]}
        latest={"status":"COMPLETE","groups":40,"run_id":"12345",
                "version_path":"stock_state_strategy/finance_clean_five/versions/12345"}
        for name,obj in (("targeted_current.json",current),
                         ("targeted_acceptance.json",acceptance),("audit.json",audit)):
            (self.clean/name).write_text(json.dumps(obj),encoding="utf-8")
        self.latest.write_text(json.dumps(latest),encoding="utf-8")

    def test_five_stock_packet_from_accepted_clean(self):
        pack=package(self.report,self.clean,self.latest,self.review_clock)
        self.assertEqual(len(pack["stocks"]),5)
        self.assertEqual(pack["market_as_of"],"2026-10-09")
        self.assertFalse(pack["historical_backtest_eligible"])
        self.assertEqual(pack["finance_clean_version"],"five-clean-12345")
        self.assertEqual(pack["stocks"][0]["finance"]["20260630"]["income"]["fields"]["total_revenue"],1e8)
        self.assertEqual(len(pack["stocks"][0]["finance"]["20260630"]),4)
        self.assertEqual(pack["stocks"][0]["technical"]["id"],
                         "T:600351.SH:2026-10-09")
        self.assertEqual(pack["stocks"][0]["r1_signal"],"NOT_GENERATED_OR_CHANGED_BY_THIS_REVIEW")
        self.assertNotIn("fundamental_agent",json.dumps(pack))
        self.assertNotIn("999",json.dumps(pack))
        self.assertIn("不",instruction(pack))

    def test_fails_on_tampered_financial_partitions(self):
        path=self.clean/"cashflow_20260630.parquet"
        path.write_bytes(path.read_bytes()+b"\n")
        with self.assertRaisesRegex(ValueError,"checksum mismatch"):
            package(self.report,self.clean,self.latest,self.review_clock)

    def test_fails_before_source_financial_publication(self):
        with self.assertRaisesRegex(ValueError,"after review time"):
            package(self.report,self.clean,self.latest,
                    datetime(2026,10,12,7,40,tzinfo=timezone.utc))

    def test_fails_if_not_40_accepted(self):
        path=self.clean/"audit.json"
        audit=json.loads(path.read_text())
        audit["valid"]=39
        path.write_text(json.dumps(audit))
        with self.assertRaisesRegex(ValueError,"not accepted"):
            package(self.report,self.clean,self.latest,self.review_clock)

    def test_fails_if_report_not_fixed_five(self):
        report=json.loads(self.report.read_text())
        report["stocks"].pop()
        self.report.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError,"exactly the fixed five"):
            package(self.report,self.clean,self.latest,self.review_clock)


if __name__=="__main__":
    unittest.main()
