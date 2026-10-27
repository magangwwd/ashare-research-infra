"""Offline structural checks for the ETF historical Drive byte-readback evidence.

These tests validate an archived *claim*, not remote Drive bytes. The actual
SHA256 byte hashing was performed independently before evidence was committed.
No market data downloads, trading, backtests or queue-state changes occur here.
"""
import json
import re
import unittest
from pathlib import Path

EVIDENCE = (
    Path(__file__).resolve().parents[1]
    / "metadata"
    / "ETF_DRIVE_39599580852_48of48_readback_20261028.json"
)


class HistoricalDriveReadbackEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))

    def test_fixed_scope_and_no_trading_authorization(self):
        e = self.evidence
        self.assertEqual(e["historical_run_id"], 39599580852)
        self.assertEqual(e["task_id"], "ETF-DIV-004")
        self.assertEqual(e["status"], "REVIEW_REQUIRED")
        self.assertEqual(e["trading_status"], "NOT_FOR_TRADING")
        self.assertIn("Does not verify original issuer dividends", " ".join(e["scope_limits"]))

    def test_unique_count_reconciliation(self):
        e = self.evidence
        self.assertEqual(e["previous_distinct_verified_from_comments"], 29)
        self.assertEqual(e["new_distinct_verified"], 19)
        self.assertEqual(e["verified_unique_archived_filenames_total"], 48)
        self.assertEqual(e["expected_unique_archived_filenames"], 48)
        self.assertEqual(e["drive_objects_with_run_id"], 49)
        self.assertEqual(e["duplicate_name_objects"], 1)
        self.assertEqual(e["remaining_unique_unverified"], 0)
        self.assertEqual(e["new_byte_hash_mismatches"], 0)
        self.assertEqual(len(e["new_files"]), 19)
        self.assertEqual(
            e["previous_distinct_verified_from_comments"] + len(e["new_files"]),
            e["verified_unique_archived_filenames_total"],
        )

    def test_filename_sha_prefix_and_ids_are_unique(self):
        rows = self.evidence["new_files"]
        self.assertEqual(len({r["filename"] for r in rows}), len(rows))
        self.assertEqual(len({r["drive_file_id"] for r in rows}), len(rows))
        for row in rows:
            with self.subTest(file=row["filename"]):
                match = re.search(r"_([0-9a-f]{12})\\.(?:csv|json)$", row["filename"])
                self.assertIsNotNone(match)
                self.assertRegex(row["sha256"], r"^[0-9a-f]{64}$")
                self.assertTrue(row["sha256"].startswith(match.group(1)))
                self.assertGreater(row["bytes"], 0)

    def test_five_full_checkpoint_comparators_are_distinct(self):
        rows = self.evidence["new_files"]
        full = [r for r in rows if r["comparison"] == "FULL_CHECKPOINT_SHA256"]
        self.assertEqual(len(full), 5)
        self.assertEqual(len({r["filename"] for r in full}), 5)
        self.assertTrue(all(
            r["comparison"] in {
                "FULL_CHECKPOINT_SHA256",
                "ARCHIVED_FILENAME_SHA256_PREFIX_12",
            }
            for r in rows
        ))


if __name__ == "__main__":
    unittest.main()
