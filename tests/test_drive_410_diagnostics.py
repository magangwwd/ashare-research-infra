"""Offline tests: Drive HTTP failures reveal only fixed operation labels."""
import unittest
from unittest.mock import Mock

from data_pipeline.drive_publish import Drive, PublishError, safe_request_operation


class SafeDriveDiagnosticTests(unittest.TestCase):
    def test_410_upload_session_never_leaks_url_or_token(self):
        session = Mock()
        session.request.return_value.status_code = 410
        secret_url = "https://www.googleapis.com/upload/drive/v3/files?upload_id=PRIVATE_SESSION_TOKEN"
        with self.assertRaises(PublishError) as caught:
            Drive(session).request(
                "PUT", secret_url, expected=(200, 201, 308), data=b"test",
                headers={"Content-Range": "bytes 0-3/4"},
            )
        message = str(caught.exception)
        self.assertIn("Drive request failed (410)", message)
        self.assertIn("operation=resumable_upload", message)
        self.assertNotIn("PRIVATE_SESSION_TOKEN", message)
        self.assertNotIn("upload_id=", message)
        self.assertEqual(session.request.call_count, 1)

    def test_410_media_read_classified_separately(self):
        session = Mock()
        session.request.return_value.status_code = 410
        with self.assertRaises(PublishError) as caught:
            Drive(session).request(
                "GET", "https://www.googleapis.com/drive/v3/files/private-file-id",
                params={"alt": "media", "supportsAllDrives": "true"},
            )
        self.assertIn("operation=read_file_media", str(caught.exception))
        self.assertNotIn("private-file-id", str(caught.exception))

    def test_410_listing_classified(self):
        self.assertEqual(safe_request_operation("GET",
                         "https://www.googleapis.com/drive/v3/files", {"params": {"q": "private"}}),
                         "list_files")

    def test_410_upload_start_classified(self):
        self.assertEqual(safe_request_operation("POST",
                         "https://www.googleapis.com/upload/drive/v3/files"),
                         "start_resumable_upload")

    def test_other_url_is_not_echoed(self):
        session = Mock()
        session.request.return_value.status_code = 403
        with self.assertRaises(PublishError) as caught:
            Drive(session).request("POST", "https://www.googleapis.com/other/private?secret=123")
        self.assertIn("operation=other_drive_request", str(caught.exception))
        self.assertNotIn("secret=123", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
