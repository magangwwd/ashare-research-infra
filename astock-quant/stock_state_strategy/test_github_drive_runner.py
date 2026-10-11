"""Offline tests of the drive-first runner's safety gates."""
import json
import tempfile
import unittest
from pathlib import Path

from github_drive_runner import DATA_BACKUP, TASKS, validate_gold


class DriveRunnerTest(unittest.TestCase):
    def test_git_backup_is_explicit_separate_task(self):
        self.assertIn("backup-repo-data", TASKS)
        self.assertIn("daily_data", DATA_BACKUP)

    def test_gold_checks_dates_and_quality(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = root / "gold/main_board/manifest.json"
            p.parent.mkdir(parents=True)
            p.write_text(json.dumps({
                "as_of_date": "2026-09-11",
                "validation": {"primary_keys_unique": True, "ohlc_valid": True}
            }), encoding="utf-8")
            self.assertEqual(validate_gold(root), "2026-09-11")
            with self.assertRaisesRegex(RuntimeError, "stale"):
                validate_gold(root, "2026-10-09")
            payload = json.loads(p.read_text(encoding="utf-8"))
            payload["validation"]["ohlc_valid"] = False
            p.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "OHLC"):
                validate_gold(root)

    def test_missing_manifests_cannot_pass_complete_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = root / "gold/main_board/manifest.json"
            p.parent.mkdir(parents=True)
            p.write_text(json.dumps({
                "as_of_date": "2026-09-11",
                "validation": {"primary_keys_unique": True, "ohlc_valid": True}
            }), encoding="utf-8")
            with self.assertRaises(FileNotFoundError):
                validate_gold(root, full=True)


if __name__ == "__main__":
    unittest.main()
