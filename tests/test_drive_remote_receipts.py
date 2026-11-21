"""Fail-closed regression tests for remote media receipts."""
import hashlib
import unittest

from data_pipeline.drive_batch_plan import BatchPlanError
from data_pipeline.drive_remote_receipts import verify_remote_media_receipts


class RemoteReceiptTests(unittest.TestCase):
    def setUp(self):
        self.payload = b"verified-content"
        self.items = [{"path": "a.parquet", "bytes": len(self.payload),
                       "sha256": hashlib.sha256(self.payload).hexdigest()}]
        self.candidates = [{"path": "a.parquet", "file_id": "file-1"}]
        self.meta = {"id": "file-1", "name": "a.parquet", "parents": ["stage-1"],
                     "trashed": False, "appProperties": {"source_run_id": "run-1"}}

    def check(self, metadata=None, media=None, candidates=None):
        return verify_remote_media_receipts(
            self.items, self.candidates if candidates is None else candidates,
            stage_id="stage-1", source_run_id="run-1",
            read_metadata=lambda _: self.meta if metadata is None else metadata,
            read_media=lambda _: self.payload if media is None else media)

    def test_valid_actual_media(self):
        receipt = self.check()[0]
        self.assertEqual(receipt["sha256"], self.items[0]["sha256"])

    def test_wrong_parent(self):
        with self.assertRaises(BatchPlanError):
            self.check(metadata={**self.meta, "parents": ["other"]})

    def test_wrong_source(self):
        with self.assertRaises(BatchPlanError):
            self.check(metadata={**self.meta, "appProperties": {"source_run_id": "other"}})

    def test_corrupted_media(self):
        with self.assertRaises(BatchPlanError):
            self.check(media=b"tampered")

    def test_wrong_file_id(self):
        with self.assertRaises(BatchPlanError):
            self.check(metadata={**self.meta, "id": "different"})

    def test_duplicate_id(self):
        with self.assertRaises(BatchPlanError):
            self.check(candidates=self.candidates * 2)

    def test_reader_failure(self):
        with self.assertRaises(BatchPlanError):
            verify_remote_media_receipts(
                self.items, self.candidates, stage_id="stage-1", source_run_id="run-1",
                read_metadata=lambda _: (_ for _ in ()).throw(RuntimeError("private detail")),
                read_media=lambda _: self.payload)


if __name__ == "__main__":
    unittest.main()
