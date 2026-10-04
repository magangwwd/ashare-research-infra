"""Restore the latest accepted Task 01 production snapshot from Google Drive."""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
import uuid

from google.auth.transport.requests import AuthorizedSession, Request

from .drive_auth import SECRET_NAME, SetupError, folder_id, parse_credentials, verify_folder
from .drive_publish import Drive, FOLDER, PublishError, remote_inventory
from .manifest import atomic_json, read_json, utcnow
from .run_task01 import load_config
from .validate import validate_task01


class RecoveryError(Exception):
    """Safe recovery failures; messages must not include credentials or HTTP bodies."""


_ALLOWED_EXACT = {
    "data/acceptance.json",
    "metadata/acceptance.json",
    "metadata/history_progress.json",
    "metadata/history_progress.md",
    "metadata/effective_config.json",
    "metadata/recovery_drill.json",
    "metadata/trading_day_replay.json",
}
_ALLOWED_PREFIXES = (
    "data/raw/",
    "data/clean/",
    "metadata/manifest/",
    "metadata/schemas/",
    "metadata/capabilities/",
    "docs/datasets/",
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def safe_manifest_path(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise RecoveryError("Production manifest contains an unsafe path.")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise RecoveryError("Production manifest contains an unsafe path.")
    normalized = path.as_posix()
    if normalized not in _ALLOWED_EXACT and not normalized.startswith(_ALLOWED_PREFIXES):
        raise RecoveryError("Production manifest contains a path outside the recovery whitelist.")
    return path


def local_destination(version_root: Path, support_root: Path, remote_path: str) -> Path:
    path = safe_manifest_path(remote_path)
    parts = path.parts
    if parts[:2] == ("data", "raw"):
        return version_root / "raw" / Path(*parts[2:])
    if parts[:2] == ("data", "clean"):
        return version_root / "clean" / Path(*parts[2:])
    if remote_path == "data/acceptance.json":
        return version_root / "acceptance.json"
    return support_root / Path(*parts)


def load_pointer(drive: Drive, target: str) -> tuple[dict, str]:
    pointers = [item for item in drive.children(target) if item["name"] == "current.json"]
    if len(pointers) != 1 or pointers[0]["mimeType"] == FOLDER:
        raise RecoveryError("Drive current.json is missing or ambiguous.")
    try:
        pointer = json.loads(drive.read(pointers[0]["id"]))
    except (ValueError, TypeError):
        raise RecoveryError("Drive current.json is not valid JSON.") from None
    required = ("version", "validated", "snapshot_as_of", "version_folder_id",
                "manifest_file_id", "manifest_sha256", "file_count", "total_bytes")
    if any(key not in pointer for key in required) or not pointer.get("validated"):
        raise RecoveryError("Drive current.json is not an accepted production pointer.")
    if not re.fullmatch(r"[A-Za-z0-9_.:+-]+", str(pointer["version"])):
        raise RecoveryError("Drive current.json contains an invalid version.")
    return pointer, pointers[0]["id"]


def load_manifest(drive: Drive, pointer: dict) -> tuple[dict, bytes]:
    version = drive.metadata(pointer["version_folder_id"])
    if version.get("mimeType") != FOLDER or version.get("trashed"):
        raise RecoveryError("Drive production version folder is unavailable.")
    raw = drive.read(pointer["manifest_file_id"])
    if sha256_bytes(raw) != pointer["manifest_sha256"]:
        raise RecoveryError("Drive production manifest checksum does not match current.json.")
    try:
        manifest = json.loads(raw)
    except (ValueError, TypeError):
        raise RecoveryError("Drive production manifest is not valid JSON.") from None
    if (not manifest.get("validated")
            or manifest.get("version") != pointer["version"]
            or manifest.get("snapshot_as_of") != pointer["snapshot_as_of"]
            or manifest.get("file_count") != pointer["file_count"]
            or manifest.get("total_bytes") != pointer["total_bytes"]):
        raise RecoveryError("Drive production manifest does not match current.json.")
    files = manifest.get("files")
    if not isinstance(files, list) or len(files) != manifest["file_count"]:
        raise RecoveryError("Drive production manifest file inventory is invalid.")
    seen = set()
    total = 0
    for item in files:
        if not isinstance(item, dict):
            raise RecoveryError("Drive production manifest file inventory is invalid.")
        for key in ("path", "file_id", "bytes", "sha256"):
            if key not in item:
                raise RecoveryError("Drive production manifest file inventory is incomplete.")
        path = safe_manifest_path(item["path"]).as_posix()
        if path in seen:
            raise RecoveryError("Drive production manifest contains duplicate paths.")
        seen.add(path)
        if not isinstance(item["bytes"], int) or item["bytes"] < 0:
            raise RecoveryError("Drive production manifest contains an invalid file size.")
        if not isinstance(item["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
            raise RecoveryError("Drive production manifest contains an invalid checksum.")
        total += item["bytes"]
    if total != manifest["total_bytes"]:
        raise RecoveryError("Drive production manifest byte total is inconsistent.")
    return manifest, raw


def verify_remote_inventory(drive: Drive, pointer: dict, manifest: dict) -> None:
    observed = remote_inventory(drive, pointer["version_folder_id"])
    expected = {item["path"]: item for item in manifest["files"]}
    if set(observed) != {*expected, "production_manifest.json"}:
        raise RecoveryError("Drive production folder inventory does not match its manifest.")
    for path, item in expected.items():
        remote = observed[path]
        if remote["id"] != item["file_id"] or int(remote.get("size", -1)) != item["bytes"]:
            raise RecoveryError("Drive production file identity or size does not match its manifest.")
    manifest_remote = observed["production_manifest.json"]
    if manifest_remote["id"] != pointer["manifest_file_id"]:
        raise RecoveryError("Drive production manifest file identity does not match current.json.")


def _promote_support_files(support_root: Path, root: Path) -> None:
    for top in ("metadata", "docs"):
        source = support_root / top
        if not source.exists():
            continue
        for file in sorted(p for p in source.rglob("*") if p.is_file()):
            relative = file.relative_to(support_root)
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(destination.name + ".restore-" + uuid.uuid4().hex[:8])
            shutil.copyfile(file, temporary)
            os.replace(temporary, destination)


def restore_snapshot(drive: Drive, target: str, root: Path, config: dict) -> dict:
    root = root.resolve()
    current = root / "data/current"
    if current.exists() or current.is_symlink():
        raise RecoveryError("Local data/current already exists; Drive fallback will not overwrite local production.")

    pointer, pointer_id = load_pointer(drive, target)
    manifest, _ = load_manifest(drive, pointer)
    verify_remote_inventory(drive, pointer, manifest)

    versions = root / "data/.versions"
    versions.mkdir(parents=True, exist_ok=True)
    safe_version = re.sub(r"[^A-Za-z0-9_.-]+", "-", pointer["version"])
    local_name = "drive-" + safe_version + "-" + uuid.uuid4().hex[:8]
    final_version = versions / local_name

    work = root / (".drive-restore-" + uuid.uuid4().hex[:8])
    version_temp = work / "version"
    support_temp = work / "support"
    version_temp.mkdir(parents=True)
    support_temp.mkdir(parents=True)
    downloaded_bytes = 0
    try:
        for count, item in enumerate(manifest["files"], 1):
            destination = local_destination(version_temp, support_temp, item["path"])
            size, checksum = drive.download(item["file_id"], destination)
            if size != item["bytes"] or checksum != item["sha256"]:
                raise RecoveryError("A downloaded Drive production file failed size or SHA256 verification.")
            downloaded_bytes += size
            if count % 100 == 0:
                print(f"SUCCESS Drive recovery verified files={count}/{manifest['file_count']}")

        if downloaded_bytes != manifest["total_bytes"]:
            raise RecoveryError("Recovered byte total does not match the production manifest.")
        accepted = read_json(version_temp / "acceptance.json", {})
        if not accepted.get("passed") or accepted.get("as_of") != pointer["snapshot_as_of"]:
            raise RecoveryError("Recovered acceptance evidence does not match the Drive production pointer.")
        as_of = date.fromisoformat(pointer["snapshot_as_of"])
        try:
            validation = validate_task01(version_temp, config, as_of)
        except Exception:
            raise RecoveryError("Recovered Drive snapshot failed local Critical validation.") from None
        if not validation.get("passed"):
            raise RecoveryError("Recovered Drive snapshot failed local Critical validation.")

        os.replace(version_temp, final_version)
        _promote_support_files(support_temp, root)
        active = root / "metadata/manifest/active.json"
        active.unlink(missing_ok=True)
        atomic_json(root / "metadata/current.json", {
            "version": local_name,
            "remote_version": pointer["version"],
            "restored_at": utcnow(),
            "source": "google_drive_current",
        })

        link = root / "data/.current.restore"
        link.unlink(missing_ok=True)
        link.symlink_to(f".versions/{local_name}")
        os.replace(link, current)

        report = {
            "passed": True,
            "source": "google_drive_current",
            "version": pointer["version"],
            "snapshot_as_of": pointer["snapshot_as_of"],
            "pointer_file_id": pointer_id,
            "version_folder_id": pointer["version_folder_id"],
            "manifest_file_id": pointer["manifest_file_id"],
            "file_count": manifest["file_count"],
            "total_bytes": manifest["total_bytes"],
            "remote_inventory_verified": True,
            "download_sha256_verified": True,
            "local_critical_validation_passed": True,
            "restored_at": utcnow(),
        }
        atomic_json(root / "metadata/drive_restore.json", report)
        return report
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/config.yaml"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        target = folder_id(args.config)
        secret = os.environ.get(SECRET_NAME)
        if not secret:
            raise SetupError("GDRIVE_OAUTH_JSON 未配置。")
        credentials = parse_credentials(secret)
        credentials.refresh(Request())
        with AuthorizedSession(credentials) as session:
            verify_folder(session, target)
            report = restore_snapshot(Drive(session), target, args.root, config)
        report_path = args.report or (args.root / "metadata/drive_restore.json")
        atomic_json(report_path, report)
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print("::notice title=Drive recovery::" + json.dumps(report))
        print("SUCCESS Drive production restored and locally revalidated.")
        return 0
    except (RecoveryError, PublishError, SetupError) as error:
        print("FAILED " + str(error))
    except Exception:
        print("FAILED Drive recovery did not complete; local production was not switched.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
