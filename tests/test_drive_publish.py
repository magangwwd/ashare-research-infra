import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from data_pipeline import drive_publish as dp


class MemoryDrive:
    """Drive fake stores actual transferred bytes and a separate production pointer."""
    def __init__(self, fault=None):
        self.files = {"root": {"id": "root", "name": "Ashare_Quant", "mimeType": dp.FOLDER},
                      "old-version": {"id": "old-version", "name": "old", "mimeType": dp.FOLDER},
                      "old-data": {"id": "old-data", "name": "data.parquet", "parents": ["old-version"],
                                   "mimeType": "application/octet-stream", "size": "3", "body": b"old"},
                      "old-manifest": {"id": "old-manifest", "name": "production_manifest.json",
                                       "parents": ["old-version"], "mimeType": "application/json",
                                       "body": b'{"version":"old","validated":true}'},
                      "pointer": {"id": "pointer", "name": "current.json", "parents": ["root"],
                                  "mimeType": "application/json", "body": dp.json_bytes({
                                      "version": "old", "validated": True, "version_folder_id": "old-version",
                                      "manifest_file_id": "old-manifest"})}}
        self.fault = fault
        self.upload_count = 0

    def children(self, parent):
        return [dict(v) for v in self.files.values() if parent in v.get("parents", [])]

    def metadata(self, ident):
        return dict(self.files[ident])

    def create_folder(self, parent, name):
        ident = f"f{len(self.files)}"
        self.files[ident] = {"id": ident, "name": name, "mimeType": dp.FOLDER, "parents": [parent]}
        return ident

    def folder(self, parent, name):
        exists = [i for i in self.children(parent) if i["name"] == name]
        return exists[0]["id"] if exists else self.create_folder(parent, name)

    def read(self, ident):
        return self.files[ident]["body"]

    def upload(self, parent, name, path):
        self.upload_count += 1
        if self.fault == "interrupted" and self.upload_count == 2:
            raise dp.PublishError("upload interrupted")
        body = path.read_bytes()
        if self.fault == "checksum" and self.upload_count == 1:
            body = bytes([body[0] ^ 1]) + body[1:]
        ident = f"f{len(self.files)}"
        if self.fault == "missing" and self.upload_count == 1:
            return ident
        self.files[ident] = {"id": ident, "name": name, "parents": [parent],
                             "mimeType": "application/octet-stream", "body": body, "size": str(len(body))}
        return ident

    def remote_digest(self, ident):
        if self.fault == "remote-read":
            raise dp.PublishError("remote read failed")
        body = self.read(ident)
        return len(body), hashlib.sha256(body).hexdigest()

    def move(self, ident, source, target):
        self.files[ident]["parents"] = [target]

    def commit_pointer(self, root, old_id, old_bytes, path):
        if self.fault == "pointer":
            raise dp.PublishError("pointer update failed")
        self.files[old_id]["body"] = path.read_bytes()
        return old_id


def snapshot(tmp_path):
    source = tmp_path / "part-000.parquet"
    source.write_bytes(b"validated parquet test payload")
    metadata = tmp_path / "schema.json"
    metadata.write_text('{"schema_version":"1.0.0"}')
    return [{"path": path, "local_path": str(local), "bytes": dp.digest(local)[0], "sha256": dp.digest(local)[1]}
            for path, local in (("data/clean/daily/year=2026/month=09/part-000.parquet", source),
                                ("metadata/schemas/daily.json", metadata))]


@pytest.mark.parametrize("fault", ["interrupted", "missing", "checksum", "remote-read", "pointer"])
def test_failed_publish_preserves_entire_old_production(tmp_path, fault):
    drive = MemoryDrive(fault)
    old = copy.deepcopy(drive.files)
    with pytest.raises(dp.PublishError):
        dp.publish(drive, "root", snapshot(tmp_path), {"passed": True, "as_of": "2026-10-03"}, {}, tmp_path)
    for ident in ("pointer", "old-version", "old-data", "old-manifest"):
        assert drive.files[ident] == old[ident]


def test_success_pointer_references_only_verified_immutable_files(tmp_path):
    drive = MemoryDrive()
    old = copy.deepcopy(drive.files)
    items = snapshot(tmp_path)
    report = dp.publish(drive, "root", items, {"passed": True, "as_of": "2026-10-03"},
                        {"core_observed_range_complete": False, "extensions_history_complete": None}, tmp_path)
    pointer = json.loads(drive.read("pointer"))
    manifest = json.loads(drive.read(pointer["manifest_file_id"]))
    assert report["passed"] and pointer["validated"]
    assert report["previous_version_recovery_verified"] is True
    assert pointer["previous_version"] == "old" and pointer["critical_history_complete"] is False
    assert pointer["extensions_history_complete"] is None
    assert manifest["file_count"] == len(items)
    for item in manifest["files"]:
        assert drive.remote_digest(item["file_id"]) == (item["bytes"], item["sha256"])
        assert "local_path" not in item
    assert drive.files["old-data"] == old["old-data"]
    assert drive.files["old-manifest"] == old["old-manifest"]
    staging = drive.folder("root", "staging")
    assert not drive.children(staging)
    assert drive.files[pointer["version_folder_id"]]["parents"] == [drive.folder("root", "versions")]


@pytest.mark.parametrize("passed,remaining", [(False, 10000000), (True, 1)])
def test_bad_acceptance_or_low_quota_cannot_write_drive(tmp_path, passed, remaining):
    drive = MemoryDrive()
    old = copy.deepcopy(drive.files)
    with pytest.raises(dp.PublishError):
        dp.publish(drive, "root", snapshot(tmp_path), {"passed": passed, "as_of": "2026-10-03"}, {}, tmp_path,
                   {"remaining_bytes": remaining})
    assert drive.files == old


def test_local_mutation_blocks_commit(tmp_path):
    drive = MemoryDrive()
    items = snapshot(tmp_path)
    Path(items[0]["local_path"]).write_bytes(b"changed")
    before = drive.read("pointer")
    with pytest.raises(dp.PublishError, match="changed"):
        dp.publish(drive, "root", items, {"passed": True, "as_of": "2026-10-03"}, {}, tmp_path)
    assert drive.read("pointer") == before


def test_lost_pointer_response_is_reconciled_without_rollback(tmp_path, monkeypatch):
    path = tmp_path / "current.json"
    path.write_bytes(b'{"version":"new"}')
    drive = dp.Drive(None)
    state = {"body": b'{"version":"old"}'}
    monkeypatch.setattr(drive, "read", lambda ident: state["body"])
    def request(*args, **kwargs):
        state["body"] = kwargs["data"]
        raise dp.PublishError("response lost")
    monkeypatch.setattr(drive, "request", request)
    assert drive.commit_pointer("root", "pointer", b'{"version":"old"}', path) == "pointer"
    assert state["body"] == path.read_bytes()


def test_pointer_failure_before_commit_preserves_bytes(tmp_path, monkeypatch):
    path = tmp_path / "current.json"
    path.write_bytes(b'{"version":"new"}')
    drive = dp.Drive(None)
    monkeypatch.setattr(drive, "read", lambda ident: b'{"version":"old"}')
    def fail(*a, **kw):
        raise dp.PublishError("failed")
    monkeypatch.setattr(drive, "request", fail)
    with pytest.raises(dp.PublishError, match="previous production"):
        drive.commit_pointer("root", "pointer", b'{"version":"old"}', path)


def test_http_errors_never_echo_credentials(monkeypatch, tmp_path, capsys):
    def fail(*a, **kw):
        raise RuntimeError("private-refresh-token client-secret")
    monkeypatch.setattr(dp, "inventory", fail)
    assert dp.main(["--report", str(tmp_path / "report.json")]) == 1
    captured = capsys.readouterr()
    assert "FAILED" in captured.out
    assert "private-refresh-token" not in captured.out + captured.err
    assert "client-secret" not in captured.out + captured.err


def test_uploaded_inventory_uses_current_layers_not_unfinished_stage(tmp_path, monkeypatch):
    current = tmp_path / "data/current"
    (current / "clean/daily/year=2026/month=09").mkdir(parents=True)
    (current / "raw/daily/year=2026/month=09").mkdir(parents=True)
    (current / "acceptance.json").write_text('{"passed":true,"as_of":"2026-10-03"}')
    (current / "clean/daily/year=2026/month=09/part-000.parquet").write_bytes(b"accepted")
    unfinished = tmp_path / "data/.versions/failed/raw/daily"
    unfinished.mkdir(parents=True)
    (unfinished / "failed.parquet").write_bytes(b"incomplete")
    (tmp_path / "metadata").mkdir()
    (tmp_path / "metadata/credentials.json").write_text('{"refresh_token":"secret"}')
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    monkeypatch.setattr(dp, "validate_task01", lambda *a: {"passed": True, "as_of": "2026-10-03"})
    monkeypatch.setattr(dp, "history_progress", lambda *a: {})
    monkeypatch.setattr(dp, "progress_markdown", lambda *a: "progress")
    items, _, _ = dp.inventory(tmp_path, {}, prepared)
    assert any(i["path"].startswith("data/clean/") for i in items)
    assert not any("failed" in i["local_path"] or "credentials" in i["path"] for i in items)


def test_resumable_upload_transfers_exact_chunks_and_preallocated_id(tmp_path):
    path = tmp_path / "test.parquet"
    path.write_bytes(b"a" * (8 * 1024 * 1024) + b"remaining bytes")
    received = []
    class Session:
        def request(self, method, url, **kwargs):
            if url.endswith("generateIds"):
                return SimpleNamespace(status_code=200, json=lambda: {"ids": ["allocated"]})
            if method == "POST":
                assert kwargs["json"]["id"] == "allocated"
                assert kwargs["headers"]["X-Upload-Content-Length"] == str(path.stat().st_size)
                return SimpleNamespace(status_code=200, headers={"Location": dp.UPLOAD + "?upload_id=test"})
            received.append(kwargs)
            return SimpleNamespace(status_code=308 if len(received) == 1 else 200)
    assert dp.Drive(Session()).upload("parent", path.name, path) == "allocated"
    assert b"".join(i["data"] for i in received) == path.read_bytes()
    assert received[0]["headers"]["Content-Range"] == f"bytes 0-8388607/{path.stat().st_size}"
    assert received[1]["headers"]["Content-Range"] == f"bytes 8388608-{path.stat().st_size-1}/{path.stat().st_size}"


def test_upload_cannot_send_credentials_to_untrusted_location(tmp_path):
    path = tmp_path / "current.json"
    path.write_text('{"validated":true}')
    class Session:
        def request(self, method, url, **kwargs):
            if method == "GET":
                return SimpleNamespace(status_code=200, json=lambda: {"ids": ["allocated"]})
            assert method == "POST"
            return SimpleNamespace(status_code=200, headers={"Location": "https://example.org/collect"})
    with pytest.raises(dp.PublishError, match="endpoint"):
        dp.Drive(Session()).upload("parent", path.name, path)


def test_inventory_revalidates_real_parquet_before_upload(tmp_path, config, client, as_of):
    from data_pipeline.run_task01 import Pipeline
    assert Pipeline(tmp_path, config, client, as_of).run()["passed"]
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    items, acceptance, _ = dp.inventory(tmp_path, config, prepared)
    assert acceptance["passed"] and any(i["path"].startswith("data/raw/daily/") for i in items)
    previous = (tmp_path / "data/current").resolve()
    partition = next((previous / "clean/daily").rglob("*.parquet"))
    partition.write_bytes(b"corrupted file")
    with pytest.raises(dp.PublishError, match="Local Critical acceptance failed"):
        dp.inventory(tmp_path, config, prepared)


def test_lost_chunk_response_reconciles_confirmed_offset(tmp_path, monkeypatch):
    path = tmp_path / "data.parquet"
    path.write_bytes(b"payload")
    calls = []
    class Session:
        def request(self, method, url, **kwargs):
            if url.endswith("generateIds"):
                return SimpleNamespace(status_code=200, json=lambda: {"ids": ["allocated"]})
            if method == "POST":
                return SimpleNamespace(status_code=200, headers={"Location": dp.UPLOAD + "?upload_id=test"})
            calls.append(kwargs["headers"]["Content-Range"])
            if len(calls) <= 3:
                import requests
                raise requests.ConnectionError("simulated")
            return SimpleNamespace(status_code=200, headers={})
    monkeypatch.setattr(dp.time, "sleep", lambda _: None)
    assert dp.Drive(Session()).upload("parent", path.name, path) == "allocated"
    assert calls[-1] == "bytes */7"


def test_lost_chunk_response_with_wrong_offset_stops(tmp_path, monkeypatch):
    path = tmp_path / "data.parquet"
    path.write_bytes(b"payload")
    class Session:
        def request(self, method, url, **kwargs):
            if url.endswith("generateIds"):
                return SimpleNamespace(status_code=200, json=lambda: {"ids": ["allocated"]})
            if method == "POST":
                return SimpleNamespace(status_code=200, headers={"Location": dp.UPLOAD + "?upload_id=test"})
            if kwargs["headers"]["Content-Range"].startswith("bytes */"):
                return SimpleNamespace(status_code=308, headers={"Range": "bytes=0-2"})
            import requests
            raise requests.ConnectionError("simulated")
    monkeypatch.setattr(dp.time, "sleep", lambda _: None)
    with pytest.raises(dp.PublishError, match="offset mismatch"):
        dp.Drive(Session()).upload("parent", path.name, path)


def test_unconfirmed_final_chunk_stays_failed(tmp_path, monkeypatch):
    path = tmp_path / "data.parquet"
    path.write_bytes(b"payload")
    class Session:
        def request(self, method, url, **kwargs):
            if url.endswith("generateIds"):
                return SimpleNamespace(status_code=200, json=lambda: {"ids": ["allocated"]})
            if method == "POST":
                return SimpleNamespace(status_code=200, headers={"Location": dp.UPLOAD + "?upload_id=test"})
            if kwargs["headers"]["Content-Range"].startswith("bytes */"):
                return SimpleNamespace(status_code=308, headers={"Range": "bytes=0-6"})
            import requests
            raise requests.ConnectionError("simulated")
    monkeypatch.setattr(dp.time, "sleep", lambda _: None)
    with pytest.raises(dp.PublishError, match="not completed"):
        dp.Drive(Session()).upload("parent", path.name, path)


def test_cross_run_resume_reuses_verified_file_and_preserves_pointer(tmp_path):
    drive = MemoryDrive(fault="interrupted")
    items = snapshot(tmp_path)
    before = drive.read("pointer")
    with pytest.raises(dp.PublishError, match="interrupted"):
        dp.publish(drive, "root", items, {"passed": True, "as_of": "2026-10-03"}, {}, tmp_path)
    assert drive.read("pointer") == before
    assert drive.upload_count == 2
    drive.fault = None
    report = dp.publish(drive, "root", items, {"passed": True, "as_of": "2026-10-03"}, {}, tmp_path)
    assert report["passed"]
    # One missing data file + manifest; first data file is not reuploaded.
    assert drive.upload_count == 4
    assert json.loads(drive.read("pointer"))["version"] != "old"


def test_cross_run_resume_rejects_corrupt_existing_file(tmp_path):
    drive = MemoryDrive(fault="interrupted")
    items = snapshot(tmp_path)
    before = drive.read("pointer")
    with pytest.raises(dp.PublishError):
        dp.publish(drive, "root", items, {"passed": True, "as_of": "2026-10-03"}, {}, tmp_path)
    uploaded = [v for v in drive.files.values() if v["name"] == "part-000.parquet"]
    assert len(uploaded) == 1
    uploaded[0]["body"] = b"x" + uploaded[0]["body"][1:]
    drive.fault = None
    with pytest.raises(dp.PublishError, match="SHA256"):
        dp.publish(drive, "root", items, {"passed": True, "as_of": "2026-10-03"}, {}, tmp_path)
    assert drive.read("pointer") == before
    assert drive.upload_count == 2


def test_cross_run_resume_rejects_duplicate_stage_identity(tmp_path):
    drive = MemoryDrive(fault="interrupted")
    items = snapshot(tmp_path)
    with pytest.raises(dp.PublishError):
        dp.publish(drive, "root", items, {"passed": True, "as_of": "2026-10-03"}, {}, tmp_path)
    staging = drive.folder("root", "staging")
    original = next(v for v in drive.files.values() if staging in v.get("parents", []) and v["mimeType"] == dp.FOLDER)
    drive.create_folder(staging, original["name"])
    drive.fault = None
    before = drive.read("pointer")
    with pytest.raises(dp.PublishError, match="Ambiguous resumable"):
        dp.publish(drive, "root", items, {"passed": True, "as_of": "2026-10-03"}, {}, tmp_path)
    assert drive.read("pointer") == before
