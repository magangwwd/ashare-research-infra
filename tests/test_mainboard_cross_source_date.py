"""Fail-closed cross-source trading date alignment contract tests."""
import unittest

from tools.mainboard_remote_probe import compare_as_of


class CrossSourceDateTests(unittest.TestCase):
    def test_equal_dates_are_only_precondition_not_switch_approval(self):
        result = compare_as_of(
            {"status": "PASS", "as_of": "2026-10-27"},
            {"status": "PASS", "as_of": "2026-10-27"},
        )
        self.assertEqual(result["status"], "ALIGNED_DATE_ONLY")
        self.assertEqual(result["calendar_days_gap"], 0)
        self.assertNotIn("production_switch_allowed", result)

    def test_actual_october_snapshot_mismatch_is_blocked(self):
        result = compare_as_of(
            {"status": "PASS", "as_of": "2026-10-24"},
            {"status": "PASS", "as_of": "2026-10-27"},
        )
        self.assertEqual(result["status"], "TASK01_BEHIND_GOLD")
        self.assertEqual(result["calendar_days_gap"], 3)

    def test_gold_behind_task01(self):
        result = compare_as_of(
            {"status": "PASS", "as_of": "2026-10-28"},
            {"status": "PASS", "as_of": "2026-10-27"},
        )
        self.assertEqual(result["status"], "GOLD_BEHIND_TASK01")

    def test_missing_or_failed_source_blocks(self):
        self.assertEqual(
            compare_as_of({"status": "MARKER_MISMATCH"}, {"status": "PASS", "as_of": "2026-10-27"})["status"],
            "BLOCKED_SOURCE_NOT_VALIDATED",
        )

    def test_malformed_dates_fail_closed(self):
        self.assertEqual(
            compare_as_of({"status": "PASS", "as_of": "not-a-date"},
                          {"status": "PASS", "as_of": "2026-10-27"})["status"],
            "INVALID_AS_OF",
        )

    def test_missing_as_of_fails_closed(self):
        self.assertEqual(
            compare_as_of({"status": "PASS"}, {"status": "PASS", "as_of": "2026-10-27"})["status"],
            "INVALID_AS_OF",
        )


if __name__ == "__main__":
    unittest.main()
