"""Offline regression tests for Issue #137: no secrets, network, or historical backtest."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests
from etf_single_rotation import upload_drive as u


class Reply:
    def __init__(self, status, payload):
        self.status_code = status
        self.payload = payload

    def json(self):
        return self.payload


class DriveIdempotencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "sample.csv"
        self.path.write_bytes(b"date,value\n2026-10-20,1\n")
        self.progress = Path(self.tmp.name) / "checkpoint.json"
        self.session = mock.Mock()
        self.patchers = [
            mock.patch.object(u, "FILES", [self.path]),
            mock.patch.object(u, "PROGRESS", self.progress),
            mock.patch.object(u, "_oauth_session", return_value=self.session),
            mock.patch.dict("os.environ", {"GITHUB_RUN_ID": "test137"}),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        digest = u._file_hash(self.path)
        tag = hashlib.sha256(self.path.as_posix().encode()).hexdigest()[:7]
        self.name = f"sample_test137_{tag}_{digest[:12]}.csv"
        self.item = {
            "id": "drive137", "name": self.name,
            "parents": [u.FOLDER_ID], "size": str(self.path.stat().st_size),
            "md5Checksum": hashlib.md5(self.path.read_bytes()).hexdigest(),
        }

    def test_exact_name_and_bytes_reuse_without_post(self):
        self.session.get.side_effect = [
            Reply(200, {"files": [self.item]}),
            Reply(200, self.item),
        ]
        u.main()
        self.session.post.assert_not_called()
        saved = json.loads(self.progress.read_text())
        self.assertEqual(saved["files"][self.path.as_posix()]["drive_id"], "drive137")

    def test_checkpoint_skip_requires_remote_byte_and_id_readback(self):
        self.progress.write_text(json.dumps({
            "schema_version": 1, "folder_id": u.FOLDER_ID,
            "files": {self.path.as_posix(): {
                "sha256": u._file_hash(self.path),
                "drive_id": "drive137", "drive_name": self.name,
            }},
        }))
        self.session.get.side_effect = [
            Reply(200, {"files": [self.item]}),
            Reply(200, self.item),
        ]
        u.main()
        self.session.post.assert_not_called()

    def test_duplicate_name_even_with_equal_bytes_fails_closed(self):
        other = {**self.item, "id": "another137"}
        self.session.get.return_value = Reply(200, {"files": [self.item, other]})
        with self.assertRaisesRegex(SystemExit, "Duplicate Drive name"):
            u.main()
        self.session.post.assert_not_called()

    def test_duplicate_across_pages_fails_closed(self):
        other = {**self.item, "id": "another137"}
        self.session.get.side_effect = [
            Reply(200, {"files": [self.item], "nextPageToken": "next"}),
            Reply(200, {"files": [other]}),
        ]
        with self.assertRaisesRegex(SystemExit, "Duplicate Drive name"):
            u.main()
        self.session.post.assert_not_called()

    def test_existing_name_different_bytes_fails_closed(self):
        wrong = {**self.item, "md5Checksum": "0" * 32}
        self.session.get.return_value = Reply(200, {"files": [wrong]})
        with self.assertRaisesRegex(SystemExit, "mismatch"):
            u.main()
        self.session.post.assert_not_called()

    def test_existing_without_remote_md5_fails_closed(self):
        wrong = {k: v for k, v in self.item.items() if k != "md5Checksum"}
        self.session.get.return_value = Reply(200, {"files": [wrong]})
        with self.assertRaisesRegex(SystemExit, "missing checksum"):
            u.main()
        self.session.post.assert_not_called()

    def test_lost_success_response_never_retries_post(self):
        self.session.get.return_value = Reply(200, {"files": []})
        self.session.post.side_effect = requests.exceptions.Timeout("lost 201 response")
        with self.assertRaisesRegex(SystemExit, "outcome ambiguous"):
            u.main()
        self.assertEqual(self.session.post.call_count, 1)
        self.assertEqual(json.loads(self.progress.read_text())["files"], {})

    def test_500_after_server_success_never_retries_post(self):
        self.session.get.return_value = Reply(200, {"files": []})
        self.session.post.return_value = Reply(500, {})
        with self.assertRaisesRegex(SystemExit, "no automatic POST retry"):
            u.main()
        self.assertEqual(self.session.post.call_count, 1)

    def test_preflight_network_error_blocks_post(self):
        self.session.get.side_effect = requests.exceptions.Timeout("Drive list unavailable")
        with self.assertRaisesRegex(SystemExit, "preflight unavailable"):
            u.main()
        self.session.post.assert_not_called()

    def test_successful_create_requires_id_and_name_readback(self):
        self.session.get.side_effect = [
            Reply(200, {"files": []}),
            Reply(200, self.item),
            Reply(200, {"files": [self.item]}),
        ]
        self.session.post.return_value = Reply(201, {"id": "drive137", "name": self.name})
        u.main()
        self.assertEqual(self.session.post.call_count, 1)
        self.assertEqual(self.session.get.call_count, 3)

    def test_created_file_missing_in_name_readback_fails_closed(self):
        self.session.get.side_effect = [
            Reply(200, {"files": []}),
            Reply(200, self.item),
            Reply(200, {"files": []}),
        ]
        self.session.post.return_value = Reply(201, {"id": "drive137"})
        with self.assertRaisesRegex(SystemExit, "name/ID readback"):
            u.main()
        self.assertEqual(self.session.post.call_count, 1)


if __name__ == "__main__":
    unittest.main()
