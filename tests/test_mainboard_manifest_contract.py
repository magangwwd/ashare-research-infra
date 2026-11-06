"""Offline contract tests for read-only pointer and manifest verification."""
import hashlib
import json
import unittest

from tools.mainboard_remote_probe import inspect_task01_snapshot, inspect_gold_manifest


class FakeDrive:
    def __init__(self, pointer, manifest, *, broken_parent=False):
        self.pointer = pointer
        self.manifest = manifest
        self.broken_parent = broken_parent

    def read(self, file_id):
        if file_id == "pointer-id":
            return json.dumps(self.pointer).encode("utf-8")
        if file_id == "manifest-id":
            return self.manifest
        raise KeyError(file_id)

    def metadata(self, file_id):
        if file_id == "version-id":
            return {"mimeType": "application/vnd.google-apps.folder", "trashed": False}
        if file_id == "manifest-id":
            return {"name": "production_manifest.json",
                    "parents": ["different-id" if self.broken_parent else "version-id"],
                    "trashed": False}
        raise KeyError(file_id)


def valid_case():
    manifest = {"validated": True, "version": "v1", "snapshot_as_of": "2026-10-28",
                "file_count": 4}
    raw = json.dumps(manifest).encode("utf-8")
    pointer = {"validated": True, "version": "v1", "snapshot_as_of": "2026-10-28",
               "file_count": 4, "version_folder_id": "version-id",
               "manifest_file_id": "manifest-id",
               "manifest_sha256": hashlib.sha256(raw).hexdigest()}
    entries = [{"id": "pointer-id", "name": "current.json"}]
    return FakeDrive(pointer, raw), entries


class SnapshotContractTests(unittest.TestCase):
    def test_valid_task01(self):
        drive, entries = valid_case()
        self.assertEqual(inspect_task01_snapshot(drive, entries),
                         {"status": "PASS", "as_of": "2026-10-28"})

    def test_unvalidated_pointer(self):
        drive, entries = valid_case()
        drive.pointer["validated"] = False
        self.assertEqual(inspect_task01_snapshot(drive, entries)["status"], "INVALID_POINTER")

    def test_invalid_sha_format(self):
        drive, entries = valid_case()
        drive.pointer["manifest_sha256"] = "not-a-sha"
        self.assertEqual(inspect_task01_snapshot(drive, entries)["status"], "INVALID_POINTER")

    def test_corrupt_manifest_sha(self):
        drive, entries = valid_case()
        drive.manifest += b" "
        self.assertEqual(inspect_task01_snapshot(drive, entries)["status"], "MANIFEST_SHA_MISMATCH")

    def test_wrong_manifest_parent(self):
        drive, entries = valid_case()
        drive.broken_parent = True
        self.assertEqual(inspect_task01_snapshot(drive, entries)["status"], "BROKEN_VERSION_LINK")

    def test_manifest_mismatch(self):
        drive, entries = valid_case()
        drive.pointer["file_count"] = 999
        self.assertEqual(inspect_task01_snapshot(drive, entries)["status"], "MANIFEST_POINTER_MISMATCH")

    def test_missing_pointer_entry(self):
        drive, _ = valid_case()
        self.assertEqual(inspect_task01_snapshot(drive, [])["status"], "SNAPSHOT_READ_ERROR")

    def test_gold_valid(self):
        raw = json.dumps({"as_of_date": "2026-10-28",
                          "validation": {"primary_keys_unique": True, "ohlc_valid": True}})
        self.assertEqual(inspect_gold_manifest(raw), {"status": "PASS", "as_of": "2026-10-28"})

    def test_gold_unvalidated(self):
        raw = json.dumps({"as_of_date": "2026-10-28",
                          "validation": {"primary_keys_unique": True, "ohlc_valid": False}})
        self.assertEqual(inspect_gold_manifest(raw)["status"], "GOLD_NOT_VALIDATED")

    def test_gold_invalid(self):
        self.assertEqual(inspect_gold_manifest("secret or malformed json")["status"], "GOLD_MANIFEST_ERROR")


if __name__ == "__main__":
    unittest.main()
