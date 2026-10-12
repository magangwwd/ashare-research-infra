"""No network / no credentials required to audit isolated five-stock finance backfill."""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from ai_research.five_clean_backfill import (
    SYMBOLS, DATASETS, PERIODS, collect, select_report,
)

try:
    import pandas as pd
    import pyarrow.parquet as pq
except ImportError:
    pd = None
    pq = None


@unittest.skipIf(pd is None or pq is None, "requires pandas and pyarrow")
class FiveCleanBackfillTests(unittest.TestCase):
    def test_scope(self):
        self.assertEqual(len(SYMBOLS), 5)
        self.assertEqual(len(DATASETS)*len(PERIODS)*len(SYMBOLS), 40)

    def make_frame(self, dataset, symbol, period):
        row = {
            "ts_code": symbol, "end_date": period, "ann_date": "20260801",
            "report_type": "1", "comp_type": "1", "update_flag": "1",
            "total_revenue": 1000000.0, "n_income_attr_p": 20000.0,
            "operate_profit": 22000.0, "total_assets": 7000000.0,
            "total_liab": 2000000.0, "total_cur_assets": 5000000.0,
            "total_cur_liab": 1000000.0, "n_cashflow_act": 30000.0,
            "roe": 2.0, "debt_to_assets": 28.57, "current_ratio": 5.0,
        }
        if dataset == "fina_indicator":
            row.pop("report_type")
            row.pop("comp_type")
        return pd.DataFrame([row])

    def test_consolidated_only(self):
        frame = self.make_frame("income", SYMBOLS[0], PERIODS[1])
        other = frame.copy()
        other["report_type"] = "2"
        chosen = select_report(pd.concat([frame, other]), SYMBOLS[0], PERIODS[1], "income")
        self.assertEqual(len(chosen), 1)
        self.assertEqual(str(chosen.iloc[0]["report_type"]), "1")

    def test_wrong_period_no_backfill(self):
        with self.assertRaisesRegex(ValueError, "NO_COMPLETE"):
            select_report(self.make_frame("income", SYMBOLS[0], PERIODS[0]),
                          SYMBOLS[0], PERIODS[1], "income")

    def test_absent_field_rejected(self):
        frame = self.make_frame("income", SYMBOLS[0], PERIODS[1])
        frame = frame.drop(columns=["operate_profit"])
        with self.assertRaisesRegex(ValueError, "MISSING_COLUMN:operate_profit"):
            select_report(frame, SYMBOLS[0], PERIODS[1], "income")

    def test_accept_only_exact_duplicate_source_rows(self):
        frame = self.make_frame("fina_indicator", SYMBOLS[0], PERIODS[1])
        chosen = select_report(pd.concat([frame, frame], ignore_index=True),
                               SYMBOLS[0], PERIODS[1], "fina_indicator")
        self.assertEqual(len(chosen), 1)
        self.assertEqual(chosen.attrs["deduplicated_identical_source_rows"], 1)

    def test_reject_conflicting_duplicate_source_rows(self):
        frame = self.make_frame("fina_indicator", SYMBOLS[0], PERIODS[1])
        different = frame.copy()
        different["roe"] = 7.5
        with self.assertRaisesRegex(ValueError, "CONFLICTING_DUPLICATE_REPORT_FIELDS:roe"):
            select_report(pd.concat([frame, different], ignore_index=True),
                          SYMBOLS[0], PERIODS[1], "fina_indicator")

    def test_extra_source_only_conflicts_keep_clean_projection(self):
        raw = self.make_frame("fina_indicator", "002107.SZ", "20260331")
        raw["fcff"] = None
        enriched = raw.copy()
        enriched["fcff"] = 58944011.7006
        selected = select_report(pd.concat([raw, enriched], ignore_index=True),
                                 "002107.SZ", "20260331", "fina_indicator")
        self.assertEqual(selected.attrs["reconciliation"]["status"], "CLEAN_PROJECTION_EQUAL")
        self.assertIn("fcff", selected.attrs["reconciliation"]["out_of_clean_schema_conflicts"])
        self.assertFalse(selected.attrs["reconciliation"]["filled_clean_columns"])

    def test_600641_official_pdf_gates_null_to_value_completion(self):
        a = self.make_frame("fina_indicator", "600641.SH", "20260331")
        a["ann_date"] = "20260430"
        a["eps"] = None
        a["dt_eps"] = None
        a["profit_dedt"] = None
        a["extra_item"] = None
        b = a.copy()
        for k, v in (("eps", -0.0186), ("dt_eps", -0.0186),
                     ("profit_dedt", -17819692.06), ("extra_item", 917627.99)):
            b[k] = v
        selected = select_report(pd.concat([a, b], ignore_index=True),
                                 "600641.SH", "20260331", "fina_indicator")
        self.assertEqual(float(selected.iloc[0]["eps"]), -0.0186)
        self.assertEqual(selected.attrs["reconciliation"]["status"],
                         "OFFICIAL_Q1_GATED_NONNULL_COMPLETION")
        self.assertEqual(len(selected.attrs["reconciliation"]["filled_clean_columns"]), 4)
        self.assertIn("cninfo.com.cn", selected.attrs["reconciliation"]
                      ["issuer_document_verification"]["source_url"])

    def test_600641_bad_nonnull_value_is_never_accepted(self):
        a = self.make_frame("fina_indicator", "600641.SH", "20260331")
        a["ann_date"] = "20260430"
        a["eps"] = None
        b = a.copy()
        b["eps"] = 0.17
        for frame in (a,b):
            frame["dt_eps"] = -0.0186
            frame["extra_item"] = 917627.99
            frame["profit_dedt"] = -17819692.06
        with self.assertRaisesRegex(ValueError, "OFFICIAL_REPORT_VERIFICATION_FAILED:eps"):
            select_report(pd.concat([a,b], ignore_index=True),
                          "600641.SH", "20260331", "fina_indicator")

    def test_unverified_null_to_value_clean_field_is_rejected(self):
        a = self.make_frame("fina_indicator", "002107.SZ", "20260331")
        a["eps"] = None
        b = a.copy()
        b["eps"] = 0.08
        with self.assertRaisesRegex(ValueError, "UNVERIFIED_PARTIAL_CLEAN_PROJECTION"):
            select_report(pd.concat([a,b], ignore_index=True),
                          "002107.SZ", "20260331", "fina_indicator")

    def test_different_nonnull_clean_metrics_remain_hard_fail(self):
        a = self.make_frame("fina_indicator", "002568.SZ", "20260630")
        b = a.copy()
        a["roe"] = 10.1
        b["roe"] = 10.2
        with self.assertRaisesRegex(ValueError, "CONFLICTING_DUPLICATE_REPORT_FIELDS:roe"):
            select_report(pd.concat([a,b], ignore_index=True),
                          "002568.SZ", "20260630", "fina_indicator")

    def test_e2e_full_40_partitions_and_checksums(self):
        outer = self
        class FakeClient:
            def query(self, dataset, ts_code, period):
                return outer.make_frame(dataset, ts_code, period)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertTrue(collect(root, FakeClient()))
            report = json.loads((root/"audit.json").read_text())
            self.assertEqual(report["valid"], 40)
            self.assertEqual(report["status"], "COMPLETE")
            self.assertEqual(len(report["files"]), 8)
            for file in report["files"]:
                self.assertEqual(len(pq.read_table(root/file)), 5)
            self.assertEqual(json.loads((root/"targeted_acceptance.json").read_text())["groups"], 40)

    def test_missing_one_stock_fails_promotion(self):
        outer = self
        class IncompleteClient:
            def query(self, dataset, ts_code, period):
                if ts_code == SYMBOLS[-1] and dataset == "cashflow" and period == PERIODS[-1]:
                    return pd.DataFrame()
                return outer.make_frame(dataset, ts_code, period)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            self.assertFalse(collect(root, IncompleteClient()))
            self.assertFalse((root/"targeted_current.json").exists())
            audit=json.loads((root/"audit.json").read_text())
            self.assertEqual(audit["valid"],39)
            self.assertEqual(audit["status"],"INCOMPLETE")


if __name__ == "__main__":
    unittest.main()
