"""No-network validation checks for the finance Clean bridge."""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from ai_research.financial_clean_bridge import (
    iso_day, published_local_day, validate_snapshot, clean_agent, evaluate,
)


class FinanceBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pointer = {
            "version": "2026-10-05T215756.864024_0000-b88e66ac",
            "validated": True,
            "snapshot_as_of": "2026-10-06",
            "published_at": "2026-10-05T22:17:56.552775+00:00",
            "critical_history_complete": False,
        }
        self.persist()

    def persist(self, accepted=True):
        (self.root / "clean_current_20261005.json").write_text(
            json.dumps(self.pointer), encoding="utf-8")
        (self.root / "acceptance_v20261005.json").write_text(
            json.dumps({"passed": accepted}), encoding="utf-8")

    def test_publication_zone_blocks_future(self):
        self.assertEqual(published_local_day("2026-10-05T22:17:56+00:00"),
                         date(2026, 10, 6))
        with self.assertRaisesRegex(ValueError, "not safely published"):
            validate_snapshot(self.root, date(2026, 10, 6))
        meta = validate_snapshot(self.root, date(2026, 10, 9))
        self.assertEqual(meta["age_days"], 3)
        self.assertEqual(meta["status"], "ACCEPTED_PARTIAL")

    def test_no_future_snapshot_or_bad_acceptance(self):
        self.pointer["snapshot_as_of"] = "2026-10-10"
        self.persist()
        with self.assertRaisesRegex(ValueError, "after research"):
            validate_snapshot(self.root, date(2026, 10, 9))
        self.pointer["snapshot_as_of"] = "2026-10-06"
        self.persist(accepted=False)
        with self.assertRaisesRegex(ValueError, "Unaccepted"):
            validate_snapshot(self.root, date(2026, 10, 9))

    def test_empty_staged_source_never_fabricates_financials(self):
        meta = validate_snapshot(self.root, date(2026, 10, 9))
        result = clean_agent({}, "600351.SH", date(2026, 10, 9), meta)
        self.assertEqual(result["status"], "NO_DATED_FINANCIAL")
        self.assertTrue(all(v is False for v in result["coverage"].values()))

    def test_iso_date_normalization(self):
        self.assertEqual(iso_day("20260630"), date(2026, 6, 30))
        self.assertEqual(iso_day("NaT"), None)
        self.assertEqual(iso_day(None), None)


if __name__ == "__main__":
    unittest.main()
