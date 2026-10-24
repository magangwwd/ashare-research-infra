import hashlib
import json
from pathlib import Path

import pytest

from data_pipeline import drive_restore as dr


class RecoveryDrive:
    def __init__(self, files, *, corrupt_download=False):
        self.files = files
        self.corrupt_download = corrupt_download

    def children(self, parent):
        if parent == "root":
            return [{"id": "pointer", "name": "current.json", "mimeType": "application/json"}]
        return []

    def read(self, ident):
        return self.files[ident]["body"]

    def metadata(self, ident):
        return {"id": ident, "mimeType": dr.FOLDER, "trashed": False}

    def download(self, ident, path):
        body = self.files[ident]["body"]
        if self.corrupt_download and ident == "data-file":
            body = body + b"x"
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return len(body), hashlib.sha256(body).hexdigest()


def fixture_drive():
    data = b"parquet payload"
    acceptance = b'{"passed": true, "as_of": "2026-10-04"}'
    items = [
        {"path": "data/clean/daily/year=2026/month=09/part-000.parquet",
         "file_id": "data-file", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()},
        {"path": "data/acceptance.json",
         "file_id": "acceptance-file", "bytes": len(acceptance), "sha256": hashlib.sha256(acceptance).hexdigest()},
    ]
    manifest = {
        "schema_version": "1.0.0",
        "version": "2026-10-04T000708.613594_0000-6a756985",
        "validated": True,
        "snapshot_as_of": "2026-10-04",
        "files": items,
        "file_count": len(items),
        "total_bytes": sum(i["bytes"] for i in items),
    }
    manifest_bytes = json.dumps(manifest).encode()
    pointer = {
        "version": manifest["version"],
        "validated": True,
        "snapshot_as_of": manifest["snapshot_as_of"],
        "version_folder_id": "version-folder",
        "manifest_file_id": "manifest-file",
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "file_count": manifest["file_count"],
        "total_bytes": manifest["total_bytes"],
    }
    files = {
        "pointer": {"body": json.dumps(pointer).encode()},
        "manifest-file": {"body": manifest_bytes},
        "data-file": {"body": data},
        "acceptance-file": {"body": acceptance},
    }
    return RecoveryDrive(files), pointer, manifest


def inventory_for(manifest):
    result = {
        item["path"]: {
            "id": item["file_id"],
            "name": Path(item["path"]).name,
            "mimeType": "application/octet-stream",
            "size": str(item["bytes"]),
        }
        for item in manifest["files"]
    }
    result["production_manifest.json"] = {
        "id": "manifest-file",
        "name": "production_manifest.json",
        "mimeType": "application/json",
        "size": "1",
    }
    return result


def test_drive_recovery_downloads_verifies_and_switches_current(tmp_path, monkeypatch):
    drive, pointer, manifest = fixture_drive()
    monkeypatch.setattr(dr, "remote_inventory", lambda *a, **k: inventory_for(manifest))
    monkeypatch.setattr(dr, "validate_task01",
                        lambda location, config, as_of: {"passed": True, "as_of": str(as_of), "errors": []})

    report = dr.restore_snapshot(drive, "root", tmp_path, {})

    assert report["passed"]
    assert report["version"] == pointer["version"]
    assert report["download_sha256_verified"]
    current = tmp_path / "data/current"
    assert current.is_symlink()
    assert (current / "clean/daily/year=2026/month=09/part-000.parquet").read_bytes() == b"parquet payload"
    assert json.loads((current / "acceptance.json").read_text())["passed"] is True
    assert json.loads((tmp_path / "metadata/drive_restore.json").read_text())["source"] == "google_drive_current"


def test_corrupt_download_never_switches_current(tmp_path, monkeypatch):
    drive, _, manifest = fixture_drive()
    drive.corrupt_download = True
    monkeypatch.setattr(dr, "remote_inventory", lambda *a, **k: inventory_for(manifest))
    monkeypatch.setattr(dr, "validate_task01", lambda *a, **k: {"passed": True})

    with pytest.raises(dr.RecoveryError, match="SHA256"):
        dr.restore_snapshot(drive, "root", tmp_path, {})

    assert not (tmp_path / "data/current").exists()


def test_bad_manifest_checksum_blocks_before_download(tmp_path):
    drive, _, _ = fixture_drive()
    pointer = json.loads(drive.files["pointer"]["body"])
    pointer["manifest_sha256"] = "0" * 64
    drive.files["pointer"]["body"] = json.dumps(pointer).encode()

    with pytest.raises(dr.RecoveryError, match="checksum"):
        dr.restore_snapshot(drive, "root", tmp_path, {})

    assert not (tmp_path / "data/current").exists()


@pytest.mark.parametrize("path", ["../secret", "/absolute/file", "data/../../secret", "other/file.json"])
def test_manifest_paths_are_restricted(path):
    with pytest.raises(dr.RecoveryError, match="unsafe path|outside the recovery whitelist"):
        dr.safe_manifest_path(path)


def test_drive_fallback_refuses_to_overwrite_local_current(tmp_path):
    drive, _, _ = fixture_drive()
    current = tmp_path / "data/current"
    current.mkdir(parents=True)
    with pytest.raises(dr.RecoveryError, match="will not overwrite"):
        dr.restore_snapshot(drive, "root", tmp_path, {})
