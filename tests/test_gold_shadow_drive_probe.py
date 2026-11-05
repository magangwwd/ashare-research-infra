"""No network, OAuth or Drive writes; only mock read-only failure categories."""
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from data_pipeline.gold_shadow_drive_probe import (
    FILES, ProbeBlocked, fetch_verified, get_credentials,
)


class FakeResponse:
    def __init__(self, code=200, chunks=None, leak=None):
        self.status_code = code
        self.chunks = chunks if chunks is not None else [b"valid"]
        self.leak = leak

    def iter_content(self, _):
        if self.leak is not None:
            raise RuntimeError("Authorization Bearer SENSITIVE_TOKEN " + self.leak)
        yield from self.chunks

    def close(self):
        pass


class FakeSession:
    def __init__(self, response=None, failure=None):
        self.response = response or FakeResponse()
        self.failure = failure
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        assert kwargs["params"]["alt"] == "media"
        if self.failure:
            raise RuntimeError("Authorization Bearer SENSITIVE_TOKEN")
        return self.response


class SafeDriveProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fixture = patch.dict(FILES, {
            "task_pointer": ("fixed-nonsecret-id", hashlib.sha256(b"valid").hexdigest())
        })
        self.fixture.start()
        self.addCleanup(self.fixture.stop)

    def test_valid_sha_only_reads(self):
        s = FakeSession()
        file = fetch_verified(s, "task_pointer", self.root)
        self.assertEqual(file.read_bytes(), b"valid")
        self.assertEqual(s.calls, 1)

    def test_http_403_class_without_secrets(self):
        with self.assertRaises(ProbeBlocked) as err:
            fetch_verified(FakeSession(FakeResponse(code=403)), "task_pointer", self.root)
        self.assertEqual(str(err.exception), "READ_HTTP_403_TASK_POINTER")
        self.assertFalse(list(self.root.iterdir()))

    def test_http_404_class(self):
        with self.assertRaisesRegex(ProbeBlocked, "READ_HTTP_404_TASK_POINTER"):
            fetch_verified(FakeSession(FakeResponse(code=404)), "task_pointer", self.root)

    def test_transport_exception_sanitized(self):
        with self.assertRaises(ProbeBlocked) as err:
            fetch_verified(FakeSession(failure=True), "task_pointer", self.root)
        self.assertEqual(str(err.exception), "READ_REQUEST_FAILED_TASK_POINTER")
        self.assertNotIn("SENSITIVE_TOKEN", str(err.exception))

    def test_mid_stream_exception_sanitized_and_partial_deleted(self):
        with self.assertRaises(ProbeBlocked) as err:
            fetch_verified(FakeSession(FakeResponse(leak="sensitive")), "task_pointer", self.root)
        self.assertEqual(str(err.exception), "READ_STREAM_FAILED_TASK_POINTER")
        self.assertFalse(list(self.root.iterdir()))

    def test_digest_change_rejected_without_file_left_behind(self):
        with self.assertRaisesRegex(ProbeBlocked, "PINNED_SHA_CHANGED_TASK_POINTER"):
            fetch_verified(FakeSession(FakeResponse(chunks=[b"changed"])), "task_pointer", self.root)
        self.assertFalse(list(self.root.iterdir()))

    def test_size_limit_fails_closed(self):
        with self.assertRaisesRegex(ProbeBlocked, "READ_SIZE_LIMIT_TASK_POINTER"):
            fetch_verified(FakeSession(), "task_pointer", self.root, max_bytes=3)
        self.assertFalse(list(self.root.iterdir()))

    def test_preexisting_file_not_overwritten(self):
        target = self.root / "current.json"
        target.write_text("must preserve")
        with self.assertRaisesRegex(ProbeBlocked, "OUTPUT_ALREADY_EXISTS_TASK_POINTER"):
            fetch_verified(FakeSession(), "task_pointer", self.root)
        self.assertEqual(target.read_text(), "must preserve")

    def test_missing_secret_is_named_without_value(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(ProbeBlocked, "MISSING_GDRIVE_OAUTH_SECRET"):
                get_credentials()

    def test_malformed_secret_is_sanitized(self):
        with patch.dict("os.environ", {"GDRIVE_OAUTH_JSON": "{malformed secret"}):
            with self.assertRaisesRegex(ProbeBlocked, "INVALID_OAUTH_SECRET_FORMAT"):
                get_credentials()

    def test_invalid_file_key_rejected_without_io(self):
        session = FakeSession()
        with self.assertRaisesRegex(ProbeBlocked, "INVALID_FILE_KEY"):
            fetch_verified(session, "unapproved_file", self.root)
        self.assertEqual(session.calls, 0)


if __name__ == "__main__":
    unittest.main()
