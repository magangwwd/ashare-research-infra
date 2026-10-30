"""Stale or malformed state output must fail before publication."""
import json
from pathlib import Path
import tempfile
import unittest

from state_run_contracts import require_exact_date, validate_state_output, phase_status_path


class StateRunContractTests(unittest.TestCase):
    def test_exact_date(self):
        require_exact_date("2026-10-29", "2026-10-29")

    def test_old_partition_new_manifest_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "mismatch"):
            require_exact_date("2026-10-29", "2026-10-28")

    def test_future_partition_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "mismatch"):
            require_exact_date("2026-10-29", "2026-10-30")

    def test_invalid_or_missing_date_is_rejected(self):
        for value in (None, "NaT", "2026-02-30", ""):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                require_exact_date("2026-10-29", value)

    def test_summary_is_checked_before_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "summary.json").write_text(json.dumps({"as_of_date":"2026-10-28"}))
            with self.assertRaises(RuntimeError):
                validate_state_output(root, "2026-10-29")
            (root / "summary.json").write_text(json.dumps({"as_of_date":"2026-10-29"}))
            self.assertEqual(validate_state_output(root, "2026-10-29"), "2026-10-29")

    def test_missing_summary_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(FileNotFoundError):
            validate_state_output(Path(tmp), "2026-10-29")

    def test_phase_receipts_do_not_overwrite_each_other(self):
        gold = phase_status_path("123-1", "gold-refresh")
        state = phase_status_path("123-1", "current-state")
        self.assertNotEqual(gold, state)
        self.assertIn("/gold-refresh/", gold)
        self.assertIn("/current-state/", state)

    def test_phase_name_is_allowlisted(self):
        with self.assertRaises(ValueError):
            phase_status_path("123-1", "../unexpected")


if __name__ == "__main__":
    unittest.main()
