"""Publish an accepted snapshot to Drive, committing current.json last."""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.parse import urlparse
import uuid

from google.auth.transport.requests import AuthorizedSession, Request
import requests

from .drive_auth import SECRET_NAME, SetupError, folder_id, parse_credentials, storage_quota, verify_folder
from .manifest import atomic_json, read_json, utcnow
from .run_task01 import load_config
from .validate import history_progress, progress_markdown, validate_task01

API = "https://www.googleapis.com/drive/v3/files"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
FOLDER = "application/vnd.google-apps.folder"


class PublishError(Exception):
    """Safe errors with structured HTTP status; never expose response bodies or tokens."""

    def __init__(self, message, *, status_code=None, operation=None):
        super().__init__(message)
        self.status_code = status_code
        self.operation = operation


def digest(path: Path) -> tuple[int, str]:
    sha = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            size += len(block)
            sha.update(block)
    return size, sha.hexdigest()


def json_bytes(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")


def safe_request_operation(method, url, kwargs=None):
    """A fixed diagnostic label; never expose resumable session URLs or tokens."""
    path = urlparse(url).path
    params = (kwargs or {}).get("params") or {}
    if method == "PUT" and path.startswith("/upload/drive/"):
        return "resumable_upload"
    if method == "POST" and url == UPLOAD:
        return "start_resumable_upload"
    if method == "GET" and path.startswith("/drive/v3/files/") and params.get("alt") == "media":
        return "read_file_media"
    if method == "GET" and path == "/drive/v3/files":
        return "list_files"
    if method == "GET" and path.startswith("/drive/v3/files/"):
        return "file_metadata"
    if method == "PATCH":
        return "update_file_or_parent"
    return "other_drive_request"



class Drive:
    def __init__(self, session):
        self.session = session

    def request(self, method, url, *, expected=(200,), **kwargs):
        # Only idempotent requests are retried. Creates use preallocated IDs below.
        for attempt in range(3):
            try:
                response = self.session.request(method, url, timeout=120, **kwargs)
            except requests.RequestException:
                response = None
            if response is not None and response.status_code in expected:
                return response
            retryable = response is None or response.status_code in (429, 500, 502, 503, 504)
            if method not in ("GET", "PUT", "PATCH") or not retryable or attempt == 2:
                status = response.status_code if response is not None else "network"
                raise PublishError(f"Drive request failed ({status}); operation={safe_request_operation(method, url, kwargs)}; inspect the current pointer before retrying.", status_code=status, operation=safe_request_operation(method, url, kwargs)) from None
            time.sleep(2 ** attempt)
        raise PublishError("Drive request failed.")

    def metadata(self, file_id):
        return self.request("GET", f"{API}/{file_id}", params={
            "fields": "id,name,mimeType,size,parents,trashed", "supportsAllDrives": "true"}).json()

    def children(self, parent):
        result, token = [], None
        while True:
            params = {"q": f"'{parent}' in parents and trashed = false", "pageSize": 1000,
                      "fields": "nextPageToken,files(id,name,mimeType,size,parents)",
                      "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
            if token:
                params["pageToken"] = token
            response = self.request("GET", API, params=params).json()
            result.extend(response.get("files", []))
            token = response.get("nextPageToken")
            if not token:
                return result

    def new_id(self):
        return self.request("GET", f"{API}/generateIds", params={"count": 1, "space": "drive"}).json()["ids"][0]

    def create_folder(self, parent, name):
        file_id = self.new_id()
        body = {"id": file_id, "name": name, "mimeType": FOLDER, "parents": [parent]}
        try:
            self.request("POST", API, json=body, params={"supportsAllDrives": "true"})
        except PublishError:
            # Lost create response is safe to reconcile by this exact preallocated ID.
            info = self.metadata(file_id)
            if info.get("name") != name or info.get("mimeType") != FOLDER or parent not in info.get("parents", []):
                raise PublishError("Folder creation could not be verified.") from None
        return file_id

    def folder(self, parent, name):
        existing = [f for f in self.children(parent) if f["name"] == name]
        if len(existing) > 1 or existing and existing[0]["mimeType"] != FOLDER:
            raise PublishError("Ambiguous Drive folder; no existing files were replaced.")
        return existing[0]["id"] if existing else self.create_folder(parent, name)

    def upload(self, parent, name, path):
        """Retry an expired resumable session once, without trusting an unknown commit.

        A 410 on a PUT may mean that the upload session is gone, not that a
        file was committed. Reconcile the preallocated file ID first. Only a
        confirmed 404 permits a new ID/session. All files are still verified
        by publish() before any production pointer changes.
        """
        size, sha = digest(path)
        mime = ("application/json" if path.suffix == ".json" else
                "text/markdown" if path.suffix == ".md" else "application/octet-stream")
        for attempt in range(2):
            if digest(path) != (size, sha):
                raise PublishError("Local file changed during upload retry; production pointer unchanged.")
            file_id = self.new_id()
            try:
                return self._upload_once(parent, name, path, file_id, size)
            except PublishError as error:
                if error.status_code != 410 or error.operation != "resumable_upload":
                    raise
                response = self.request(
                    "GET", f"{API}/{file_id}", expected=(200, 404),
                    params={"fields": "id,name,mimeType,size,parents,trashed",
                            "supportsAllDrives": "true"})
                if response.status_code == 200:
                    info = response.json()
                    if (info.get("id") == file_id and info.get("name") == name
                            and info.get("mimeType") == mime
                            and not info.get("trashed")
                            and parent in info.get("parents", [])
                            and int(info.get("size", -1)) == size
                            and self.remote_digest(file_id) == (size, sha)):
                        return file_id
                    raise PublishError("Expired upload session left an unverified file; production pointer unchanged.") from None
                if attempt:
                    raise PublishError("Resumable session expired twice; production pointer unchanged.") from None
                print("WARNING expired resumable upload session; retrying staged file with a fresh ID")
        raise PublishError("Upload session retry exhausted; production pointer unchanged.")

    def _upload_once(self, parent, name, path, file_id, size):
        mime = "application/json" if path.suffix == ".json" else "text/markdown" if path.suffix == ".md" else "application/octet-stream"
        response = self.request("POST", UPLOAD, expected=(200,),
                                params={"uploadType": "resumable", "supportsAllDrives": "true"},
                                json={"id": file_id, "name": name, "parents": [parent], "mimeType": mime},
                                headers={"X-Upload-Content-Type": mime,
                                         "X-Upload-Content-Length": str(size)})
        location = response.headers.get("Location", "")
        parsed = urlparse(location)
        if parsed.scheme != "https" or parsed.hostname != "www.googleapis.com" or not parsed.path.startswith("/upload/drive/"):
            raise PublishError("Unexpected upload endpoint.")
        # Bounded memory; retry each fixed byte range on the same resumable session.
        with path.open("rb") as source:
            offset = 0
            while offset < size:
                block = source.read(8 * 1024 * 1024)
                end = offset + len(block) - 1
                try:
                    reply = self.request("PUT", location, expected=(200, 201, 308), data=block,
                                         headers={"Content-Length": str(len(block)),
                                                  "Content-Range": f"bytes {offset}-{end}/{size}"})
                except PublishError:
                    # Resolve a lost response before sending the next chunk.
                    probe = self.request("PUT", location, expected=(200, 201, 308),
                                         data=b"", headers={"Content-Length": "0",
                                                           "Content-Range": f"bytes */{size}"})
                    if probe.status_code == 308:
                        if probe.headers.get("Range") != f"bytes=0-{end}":
                            raise PublishError("Upload offset mismatch; production pointer unchanged.")
                    elif end + 1 != size:
                        raise PublishError("Upload completed before all bytes were sent.")
                    reply = probe
                if end + 1 == size and reply.status_code == 308:
                    raise PublishError("Upload was not completed.")
                if end + 1 < size and reply.status_code != 308:
                    raise PublishError("Upload ended before all bytes were sent.")
                offset = end + 1
        if not size:
            self.request("PUT", location, expected=(200, 201), data=b"", headers={"Content-Length": "0"})
        return file_id

    def remote_digest(self, file_id):
        response = self.request("GET", f"{API}/{file_id}", params={"alt": "media", "supportsAllDrives": "true"}, stream=True)
        sha, size = hashlib.sha256(), 0
        try:
            for block in response.iter_content(1024 * 1024):
                sha.update(block)
                size += len(block)
        finally:
            response.close()
        return size, sha.hexdigest()

    def read(self, file_id):
        return self.request("GET", f"{API}/{file_id}", params={"alt": "media", "supportsAllDrives": "true"}).content

    def download(self, file_id, path):
        """Stream a Drive file to disk and return its byte count and SHA256."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".download-" + uuid.uuid4().hex[:8])
        response = self.request("GET", f"{API}/{file_id}",
                                params={"alt": "media", "supportsAllDrives": "true"}, stream=True)
        sha, size = hashlib.sha256(), 0
        try:
            with temporary.open("wb") as output:
                for block in response.iter_content(1024 * 1024):
                    if not block:
                        continue
                    output.write(block)
                    sha.update(block)
                    size += len(block)
            os.replace(temporary, path)
        finally:
            response.close()
            temporary.unlink(missing_ok=True)
        return size, sha.hexdigest()

    def move(self, file_id, source, target):
        self.request("PATCH", f"{API}/{file_id}", params={"addParents": target, "removeParents": source,
                                                          "supportsAllDrives": "true"}, json={})
        if self.metadata(file_id).get("parents") != [target]:
            raise PublishError("Verified version could not be moved to production versions.")

    def commit_pointer(self, root, previous_id, previous_bytes, path):
        # A single media update is the commit. Reconcile a lost HTTP response.
        intended = path.read_bytes()
        if previous_id:
            if self.read(previous_id) != previous_bytes:
                raise PublishError("Production pointer changed concurrently; publication stopped.")
            try:
                self.request("PATCH", f"{UPLOAD}/{previous_id}",
                             params={"uploadType": "media", "supportsAllDrives": "true"},
                             data=intended, headers={"Content-Type": "application/json"})
            except PublishError:
                if self.read(previous_id) != intended:
                    raise PublishError("Pointer update failed; previous production remains current.") from None
            pointer_id = previous_id
        else:
            pointer_id = self.upload(root, "current.json", path)
        if self.read(pointer_id) != intended:
            raise PublishError("Production pointer readback failed.")
        return pointer_id


def inventory(root: Path, config: dict, prepared: Path) -> tuple[list[dict], dict, dict]:
    """Only published layers and explicit safe metadata paths may leave the Runner."""
    current = root / "data/current"
    accepted = read_json(current / "acceptance.json", {})
    if not accepted.get("passed"):
        raise PublishError("No locally accepted production snapshot exists.")
    as_of = date.fromisoformat(accepted["as_of"])
    try:
        acceptance = validate_task01(current, config, as_of)
    except Exception:
        raise PublishError("Local Critical acceptance failed; upload blocked.") from None
    if not acceptance["passed"]:
        raise PublishError("Local Critical acceptance failed; upload blocked.")
    progress = history_progress(current, root / "metadata", config, as_of, acceptance)
    atomic_json(prepared / "metadata/acceptance.json", acceptance)
    atomic_json(prepared / "metadata/history_progress.json", progress)
    (prepared / "metadata/history_progress.md").write_text(progress_markdown(progress), encoding="utf-8")
    pairs = []
    for layer in ("raw", "clean"):
        directory = current / layer
        for source in sorted(directory.rglob("*.parquet")):
            if not source.resolve().is_relative_to(current.resolve()):
                raise PublishError("Snapshot contains an unexpected external data path.")
            pairs.append((source, f"data/{layer}/{source.relative_to(directory).as_posix()}"))
    pairs.append((current / "acceptance.json", "data/acceptance.json"))
    # Credential/client files and arbitrary metadata are never recursively included.
    for name in ("manifest", "schemas", "capabilities"):
        directory = root / "metadata" / name
        for source in sorted(directory.rglob("*.json")):
            if source.is_symlink() or not source.resolve().is_relative_to(directory.resolve()):
                raise PublishError("Metadata contains an external file.")
            pairs.append((source, f"metadata/{name}/{source.relative_to(directory).as_posix()}"))
    for name in ("effective_config.json", "recovery_drill.json", "trading_day_replay.json"):
        source = root / "metadata" / name
        if source.is_file() and not source.is_symlink():
            pairs.append((source, f"metadata/{name}"))
    for source in sorted((root / "docs/datasets").glob("*.md")):
        if source.is_symlink():
            raise PublishError("Dataset documentation contains an external file.")
        pairs.append((source, f"docs/datasets/{source.name}"))
    for source in sorted((prepared / "metadata").glob("*")):
        pairs.append((source, f"metadata/{source.name}"))
    items = []
    for source, relative in pairs:
        size, sha = digest(source)
        items.append({"path": relative, "local_path": str(source), "bytes": size, "sha256": sha})
    if not items or len({i["path"] for i in items}) != len(items):
        raise PublishError("Invalid local inventory.")
    return items, acceptance, progress


def remote_inventory(drive, root_id, prefix=""):
    result = {}
    for item in drive.children(root_id):
        path = prefix + item["name"]
        if item["mimeType"] == FOLDER:
            nested = remote_inventory(drive, item["id"], path + "/")
            if result.keys() & nested.keys():
                raise PublishError("Duplicate remote paths.")
            result.update(nested)
        else:
            if path in result:
                raise PublishError("Duplicate remote paths.")
            result[path] = item
    return result


def verify_previous_snapshot(drive, pointer: dict, expected_manifest: bytes | None = None) -> bytes:
    """Verify that the version referenced by an existing production pointer remains recoverable."""
    version_id = pointer.get("version_folder_id")
    manifest_id = pointer.get("manifest_file_id")
    if not version_id or not manifest_id or not pointer.get("version"):
        raise PublishError("Existing production pointer is missing recovery metadata.")
    info = drive.metadata(version_id)
    if info.get("mimeType") != FOLDER or info.get("trashed"):
        raise PublishError("Previous production version folder is not recoverable.")
    manifest_bytes = drive.read(manifest_id)
    if expected_manifest is not None and manifest_bytes != expected_manifest:
        raise PublishError("Previous production manifest changed during publication.")
    try:
        manifest = json.loads(manifest_bytes)
    except (ValueError, TypeError):
        raise PublishError("Previous production manifest is not valid JSON.") from None
    if not manifest.get("validated") or manifest.get("version") != pointer.get("version"):
        raise PublishError("Previous production manifest does not match the current pointer.")
    return manifest_bytes


def publish(drive, target, items, acceptance, progress, prepared, quota=None):
    if not acceptance.get("passed"):
        raise PublishError("Acceptance is required before publication.")
    total = sum(i["bytes"] for i in items)
    remaining = (quota or {}).get("remaining_bytes")
    if remaining is not None and remaining < total + 1024 * 1024:
        raise PublishError("Drive remaining capacity is insufficient for a new immutable snapshot.")
    pointers = [f for f in drive.children(target) if f["name"] == "current.json"]
    if len(pointers) > 1 or pointers and pointers[0]["mimeType"] == FOLDER:
        raise PublishError("Ambiguous current pointer; publication blocked.")
    old_id = pointers[0]["id"] if pointers else None
    old_bytes = drive.read(old_id) if old_id else None
    old_pointer = None
    old_manifest_bytes = None
    if old_bytes is not None:
        try:
            old_pointer = json.loads(old_bytes)
            if not old_pointer.get("validated") or not old_pointer.get("version_folder_id"):
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise PublishError("Existing current pointer is not an accepted snapshot pointer.") from None
        # A second or later publication must start from a genuinely recoverable old production.
        old_manifest_bytes = verify_previous_snapshot(drive, old_pointer)
    staging = drive.folder(target, "staging")
    versions = drive.folder(target, "versions")
    version = utcnow().replace(":", "").replace("+", "_") + "-" + uuid.uuid4().hex[:8]
    stage = drive.create_folder(staging, version)
    print(f"SUCCESS staging created folder_id={stage} files={len(items)} bytes={total}; current pointer unchanged")
    directories = {"": stage}
    files = []
    for item in items:
        path = Path(item["path"])
        parts = path.parent.parts
        for count in range(1, len(parts) + 1):
            relative = "/".join(parts[:count])
            if relative not in directories:
                parent = "/".join(parts[:count - 1])
                directories[relative] = drive.create_folder(directories[parent], parts[count - 1])
        source = Path(item["local_path"])
        if digest(source) != (item["bytes"], item["sha256"]):
            raise PublishError("Local snapshot changed during upload.")
        file_id = drive.upload(directories[path.parent.as_posix() if parts else ""], path.name, source)
        files.append({k: v for k, v in item.items() if k != "local_path"} | {"file_id": file_id})
        if len(files) % 100 == 0:
            print(f"SUCCESS staged files={len(files)}/{len(items)}; remote verification pending")
    observed = remote_inventory(drive, stage)
    if set(observed) != {i["path"] for i in files}:
        raise PublishError("Remote file inventory does not match the accepted local snapshot.")
    for count, item in enumerate(files, 1):
        remote = observed[item["path"]]
        if remote["id"] != item["file_id"] or int(remote.get("size", -1)) != item["bytes"]:
            raise PublishError("Remote file identity or size mismatch.")
        if drive.remote_digest(item["file_id"]) != (item["bytes"], item["sha256"]):
            raise PublishError("Remote SHA256 verification failed; publication blocked.")
        if count % 100 == 0:
            print(f"SUCCESS remote SHA256 verified files={count}/{len(files)}; current pointer unchanged")
    manifest = {"schema_version": "1.0.0", "version": version, "validated": True,
                "snapshot_as_of": acceptance["as_of"], "published_at": utcnow(),
                "source_run_id": os.environ.get("GITHUB_RUN_ID"), "files": files,
                "file_count": len(files), "total_bytes": total,
                "critical_history_complete": progress.get("core_observed_range_complete", False),
                "full_requested_core_range_complete": progress.get("full_requested_core_range_complete", False),
                "extensions_history_complete": progress.get("extensions_history_complete"),
                "metadata_scope": "Manifest request/gap metadata describes checkpoint work state; this manifest enumerates actual published files."}
    manifest_path = prepared / "production_manifest.json"
    atomic_json(manifest_path, manifest)
    manifest_id = drive.upload(stage, manifest_path.name, manifest_path)
    if drive.remote_digest(manifest_id) != digest(manifest_path):
        raise PublishError("Production manifest readback failed.")
    if set(remote_inventory(drive, stage)) != {*observed, "production_manifest.json"}:
        raise PublishError("Unexpected files appeared before publication.")
    drive.move(stage, staging, versions)
    pointer = {k: manifest[k] for k in ("schema_version", "version", "validated", "snapshot_as_of", "published_at",
                                      "source_run_id", "file_count", "total_bytes", "critical_history_complete",
                                      "full_requested_core_range_complete", "extensions_history_complete")}
    pointer.update(version_folder_id=stage, manifest_file_id=manifest_id,
                   manifest_sha256=digest(manifest_path)[1], previous_version=old_pointer.get("version") if old_pointer else None)
    pointer_path = prepared / "current.json"
    atomic_json(pointer_path, pointer)
    pointer_id = drive.commit_pointer(target, old_id, old_bytes, pointer_path)
    previous_recovery_verified = None
    if old_pointer is not None:
        try:
            verify_previous_snapshot(drive, old_pointer, old_manifest_bytes)
            previous_recovery_verified = True
        except PublishError:
            # The new pointer has already been atomically committed. Report a failed
            # recovery check instead of attempting a dangerous automatic rollback.
            previous_recovery_verified = False
            print("WARNING previous Drive production could not be reverified after pointer switch")
    report = {**pointer, "passed": True, "pointer_file_id": pointer_id, "folder_id": target,
              "previous_version_recovery_verified": previous_recovery_verified,
              "remote_validation": "all file paths, byte sizes and downloaded SHA256 verified", "storage_quota": quota}
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/config.yaml"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--report", type=Path, default=Path("metadata/drive_publish.json"))
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        target = folder_id(args.config)
        with tempfile.TemporaryDirectory(prefix="task01-drive-") as temp:
            prepared = Path(temp)
            items, acceptance, progress = inventory(args.root.resolve(), config, prepared)
            secret = os.environ.get(SECRET_NAME)
            if not secret:
                raise SetupError("GDRIVE_OAUTH_JSON 未配置。")
            credentials = parse_credentials(secret)
            credentials.refresh(Request())
            with AuthorizedSession(credentials) as session:
                verify_folder(session, target)
                quota = storage_quota(session)
                report = publish(Drive(session), target, items, acceptance, progress, prepared, quota)
        atomic_json(args.report, report)
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print("::notice title=Drive publication::" + json.dumps(report))
        print("SUCCESS Drive snapshot published and verified.")
        return 0
    except (PublishError, SetupError) as error:
        print("FAILED " + str(error))
    except Exception:
        print("FAILED Drive snapshot upload or verification did not complete; inspect the current pointer before retrying.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
