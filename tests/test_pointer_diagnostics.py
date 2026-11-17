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
            return {"id": ident, "mimeType": "application/vnd.google-apps.folder", "parents": ["root"]}
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
    with pytest.raises(PointerDiagnosticError, match="unexpected_error"):
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

def test_http_errors_are_distinguished_without_leaking_identifiers():
    from requests import Response
    from requests.exceptions import HTTPError, Timeout
    from data_pipeline.pointer_diagnostics import _drive_failure

    for status, category in [(404, "not_found_or_hidden"), (403, "permission_denied"),
                             (401, "permission_denied"), (429, "service_unavailable"),
                             (503, "service_unavailable")]:
        response = Response()
        response.status_code = status
        error = HTTPError("secret-id", response=response)
        message = str(_drive_failure(error, "metadata"))
        assert category in message
        assert "secret-id" not in message
    assert "network_unavailable" in str(_drive_failure(Timeout("secret-id"), "metadata"))


def test_metadata_and_manifest_network_fail_closed():
    from requests.exceptions import Timeout
    drive = FakeDrive()
    drive.metadata = lambda ident: (_ for _ in ()).throw(Timeout("secret"))
    with pytest.raises(PointerDiagnosticError, match="metadata_network_unavailable"):
        inspect(drive, "root")
    drive = FakeDrive()
    drive.read = lambda ident: (_ for _ in ()).throw(Timeout("secret")) if ident != "current" else json.dumps(drive.pointer).encode()
    with pytest.raises(PointerDiagnosticError, match="manifest_content_network_unavailable"):
        inspect(drive, "root")


@pytest.mark.parametrize("token,category", [
    ("401", "permission_denied"), ("403", "permission_denied"),
    ("404", "not_found_or_hidden"), ("429", "service_unavailable"),
    ("500", "service_unavailable"), ("503", "service_unavailable"),
    ("network", "network_unavailable"),
])
@pytest.mark.parametrize("operation", ["metadata", "manifest"])
def test_real_adapter_publisherror_classification(token, category, operation):
    from data_pipeline.drive_publish import PublishError
    drive = FakeDrive()
    secret = "secret-drive-id"
    error = PublishError(f"Drive request failed ({token}); operation=files.get; {secret}")
    def fail(*args):
        raise error
    if operation == "metadata":
        drive.metadata = fail
        expected = "referenced_file_metadata_"
    else:
        original = drive.read
        drive.read = lambda ident: original(ident) if ident == "current" else fail(ident)
        expected = "manifest_content_"
    with pytest.raises(PointerDiagnosticError) as caught:
        inspect(drive, "root")
    assert expected + category in str(caught.value)
    assert secret not in str(caught.value)


def test_unknown_publisherror_and_malformed_pointer_fail_closed():
    from data_pipeline.drive_publish import PublishError
    drive = FakeDrive()
    drive.metadata = lambda ident: (_ for _ in ()).throw(PublishError("unrecognized secret-id"))
    with pytest.raises(PointerDiagnosticError, match="unexpected_error") as caught:
        inspect(drive, "root")
    assert "secret-id" not in str(caught.value)
    drive = FakeDrive()
    drive.pointer["manifest_sha256"] = "0" * 64
    with pytest.raises(PointerDiagnosticError, match="sha256_mismatch"):
        inspect(drive, "root")


@pytest.mark.parametrize("status,category", [("403","permission_denied"),("404","not_found_or_hidden"),("429","service_unavailable"),("network","network_unavailable")])
@pytest.mark.parametrize("phase", ["children", "pointer_read"])
def test_current_pointer_adapter_errors_fail_closed(status, category, phase):
    from data_pipeline.drive_publish import PublishError
    drive = FakeDrive()
    error = PublishError(f"Drive request failed ({status}); operation=files.list; secret-id")
    def fail(*args):
        raise error
    if phase == "children":
        drive.children = fail
    else:
        original = drive.read
        drive.read = lambda ident: fail(ident) if ident == "current" else original(ident)
    with pytest.raises(PointerDiagnosticError, match="current_pointer_" + category) as caught:
        inspect(drive, "root")
    assert "secret-id" not in str(caught.value)


def test_version_folder_wrong_root_fails_closed():
    drive = FakeDrive()
    original = drive.metadata
    def wrong_root(ident):
        item = original(ident)
        if ident == drive.pointer["version_folder_id"]:
            item["parents"] = ["other-root"]
        return item
    drive.metadata = wrong_root
    with pytest.raises(PointerDiagnosticError, match="version_folder_identity_mismatch"):
        inspect(drive, "root")
