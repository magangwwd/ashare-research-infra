"""Test both Git storage exclusion and safe Drive path handling without network."""
from __future__ import annotations
import unittest
from check_git_storage_policy import violates
from legacy_drive_runner import safe_prefix, TASKS

class DriveOnlyContractTests(unittest.TestCase):
    def test_market_data_and_outputs_never_tracked(self):
        forbidden = ["market_meta/daily_basic.csv", "daily_data/000001.SZ.csv",
            "financial_data/valuation.csv.bak", "stock_state_strategy/output/run/report.md",
            "financial_data/reports/report_2026.md", ".claude/settings.local.json",
            "kronos_return_top3/finetune_data/train_manifest.csv"]
        for path in forbidden:
            with self.subTest(path=path):
                self.assertTrue(violates(path))

    def test_code_and_docs_are_legal(self):
        for path in ["stock_state_strategy/run_pipeline.py", "kronos_return_top3/vendor_model/kronos.py",
                     "stock_state_strategy/LOCK_TO_MARKUP_PAPER_STRATEGY.md",
                     "stock_state_strategy/live_position_monitor/.env.example"]:
            self.assertFalse(violates(path))

    def test_drive_prefix_is_relative(self):
        self.assertEqual(safe_prefix("astock_quant_repo_backup"), "astock_quant_repo_backup")
        for text in ("", "../private", "/absolute", "C:\\secret"):
            with self.assertRaises(ValueError):
                safe_prefix(text)

    def test_legacy_jobs_are_explicit_allowlist(self):
        self.assertIn("daily-report", TASKS)
        self.assertNotIn("arbitrary-command", TASKS)

if __name__ == "__main__":
    unittest.main()
