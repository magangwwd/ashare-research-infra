"""Offline tests for the production-credential read-only marker probe."""
import unittest
from unittest.mock import patch

from tools.mainboard_remote_probe import inspect_markers, rclone_list, gold_probe


class ProbeTests(unittest.TestCase):
    def test_complete_task01(self):
        actual = [
            {"name": "current.json", "mimeType": "application/json"},
            {"name": "versions", "mimeType": "application/vnd.google-apps.folder"},
            {"name": "staging", "mimeType": "application/vnd.google-apps.folder"},
        ]
        required = {"current.json": "file", "versions": "folder", "staging": "folder"}
        self.assertEqual(inspect_markers(actual, required),
                         {"missing": [], "duplicate": [], "type_mismatch": []})

    def test_missing_pointer(self):
        self.assertEqual(inspect_markers([], {"current.json": "file"})["missing"], ["current.json"])

    def test_duplicate_pointer(self):
        self.assertEqual(inspect_markers([{"name": "current.json"}, {"name": "current.json"}],
                                         {"current.json": "file"})["duplicate"], ["current.json"])

    def test_wrong_type(self):
        self.assertEqual(inspect_markers([{"Name": "manifest.json", "IsDir": True}],
                                         {"manifest.json": "file"})["type_mismatch"], ["manifest.json"])

    @patch("tools.mainboard_remote_probe.subprocess.run")
    def test_rclone_error_never_exposes_stderr(self, run):
        run.return_value.returncode = 1
        run.return_value.stderr = "secret token"
        with self.assertRaisesRegex(RuntimeError, "^RCLONE_FAILED$"):
            rclone_list("gdrive:down_data/")

    @patch.dict("os.environ", {"RCLONE_CONFIG": "/tmp/mock"}, clear=True)
    @patch("tools.mainboard_remote_probe.rclone_list")
    def test_wrong_remote_root_fails_closed(self, listing):
        listing.side_effect = [[], []]
        self.assertEqual(gold_probe()["status"], "WRONG_REMOTE_ROOT")
        self.assertEqual(listing.call_count, 2)

    @patch.dict("os.environ", {}, clear=True)
    def test_missing_rclone_secret(self):
        self.assertEqual(gold_probe()["status"], "MISSING_SECRET")


if __name__ == "__main__":
    unittest.main()
