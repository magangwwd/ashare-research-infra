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

    def test_reject_same_date_duplicate(self):
        frame = self.make_frame("income", SYMBOLS[0], PERIODS[1])
        with self.assertRaisesRegex(ValueError, "AMBIGUOUS"):
            select_report(pd.concat([frame, frame]), SYMBOLS[0], PERIODS[1], "income")

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
