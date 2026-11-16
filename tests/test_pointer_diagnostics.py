"""Regression tests: pointer IDs must come from current.json, never a copied value."""
import hashlib
import json

import pytest

from data_pipeline.pointer_diagnostics import PointerDiagnosticError, inspect


class FakeDrive:
    def __init__(self):
        self.pointer = {"version": "v1", "validated": True, "snapshot_as_of": "2026-11-15",
                        "file_count": 1, "total_bytes": 3,
                        "version_folder_id": "folder-O0Il1",
                        "manifest_file_id": "manifest-O0Il1"}
        self.manifest = json.dumps({"version": "v1", "validated": True,
                                    "snapshot_as_of": "2026-11-15", "file_count": 1,
                                    "total_bytes": 3}).encode()
        self.pointer["manifest_sha256"] = hashlib.sha256(self.manifest).hexdigest()
        self.lookups = []

    def children(self, parent):
        return [{"id": "current", "name": "current.json", "mimeType": "application/json"}]

    def read(self, ident):
        self.lookups.append(("read", ident))
        return json.dumps(self.pointer).encode() if ident == "current" else self.manifest

    def metadata(self, ident):
        self.lookups.append(("metadata", ident))
        if ident == self.pointer["version_folder_id"]:
            return {"id": ident, "mimeType": "application/vnd.google-apps.folder"}
        if ident == self.pointer["manifest_file_id"]:
            return {"id": ident, "name": "production_manifest.json",
                    "parents": [self.pointer["version_folder_id"]]}
        raise KeyError(ident)


def test_uses_exact_ids_from_live_pointer():
    drive = FakeDrive()
    result = inspect(drive, "root")
    assert result["passed"] and not result["production_switch_allowed"]
    assert not result["data_files_sha256_verified"]
    assert ("metadata", "manifest-O0Il1") in drive.lookups
    assert ("metadata", "folder-O0Il1") in drive.lookups


def test_missing_id_is_not_misreported_as_deleted():
    drive = FakeDrive()
    original = drive.metadata
    def missing(ident):
        if ident == "manifest-O0Il1":
            raise KeyError(ident)
        return original(ident)
    drive.metadata = missing
    with pytest.raises(PointerDiagnosticError, match="404 alone does not prove deletion"):
        inspect(drive, "root")


def test_wrong_parent_fails_closed():
    drive = FakeDrive()
    original = drive.metadata
    def wrong_parent(ident):
        value = original(ident)
        if ident == "manifest-O0Il1":
            value["parents"] = ["other"]
        return value
    drive.metadata = wrong_parent
    with pytest.raises(PointerDiagnosticError, match="parent_mismatch"):
        inspect(drive, "root")


def test_manifest_checksum_mismatch_fails_closed():
    drive = FakeDrive()
    drive.pointer["manifest_sha256"] = "0" * 64
    with pytest.raises(PointerDiagnosticError, match="sha256_mismatch"):
        inspect(drive, "root")


def test_read_only_entrypoint_does_not_require_write_access():
    from pathlib import Path
    source = (Path(__file__).parents[1] / "data_pipeline" / "pointer_diagnostics.py").read_text(encoding="utf-8")
    assert "verify_folder(" not in source
