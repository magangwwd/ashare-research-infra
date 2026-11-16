"""Read-only production pointer inspection; never transcribe Drive IDs by hand.

Usage: python -m data_pipeline.pointer_diagnostics
Requires GDRIVE_OAUTH_JSON and existing config/config.yaml.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from google.auth.transport.requests import AuthorizedSession, Request

from .drive_auth import SECRET_NAME, SetupError, folder_id, parse_credentials, verify_folder
from .drive_publish import Drive, PublishError
from .drive_restore import RecoveryError, load_pointer


class PointerDiagnosticError(Exception):
    """Safe, non-sensitive failure category."""


def inspect(drive, root_id: str) -> dict:
    pointer, pointer_id = load_pointer(drive, root_id)
    # IDs are used directly from parsed JSON, never copied or reconstructed.
    folder_id_value = pointer["version_folder_id"]
    manifest_id = pointer["manifest_file_id"]
    try:
        folder = drive.metadata(folder_id_value)
        manifest = drive.metadata(manifest_id)
    except Exception:
        raise PointerDiagnosticError(
            "referenced_file_not_accessible: check exact pointer IDs, Drive account, "
            "trash and permissions; 404 alone does not prove deletion."
        ) from None
    if folder.get("id") != folder_id_value or folder.get("mimeType") != "application/vnd.google-apps.folder" or folder.get("trashed"):
        raise PointerDiagnosticError("version_folder_identity_mismatch")
    if (manifest.get("id") != manifest_id
            or manifest.get("name") != "production_manifest.json"
            or manifest.get("parents") != [folder_id_value]
            or manifest.get("trashed")):
        raise PointerDiagnosticError("manifest_identity_or_parent_mismatch")
    try:
        body = drive.read(manifest_id)
    except Exception:
        raise PointerDiagnosticError("manifest_content_unavailable") from None
    if hashlib.sha256(body).hexdigest() != pointer["manifest_sha256"]:
        raise PointerDiagnosticError("manifest_sha256_mismatch")
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        raise PointerDiagnosticError("manifest_invalid_json") from None
    if (payload.get("version") != pointer["version"]
            or payload.get("snapshot_as_of") != pointer["snapshot_as_of"]
            or payload.get("file_count") != pointer["file_count"]
            or payload.get("total_bytes") != pointer["total_bytes"]
            or not payload.get("validated")):
        raise PointerDiagnosticError("manifest_pointer_facts_mismatch")
    # A successful metadata and manifest check does not verify every data file.
    return {"passed": True, "pointer_file_id": pointer_id,
            "version": pointer["version"], "snapshot_as_of": pointer["snapshot_as_of"],
            "manifest_sha256_verified": True, "data_files_sha256_verified": False,
            "production_switch_allowed": False}


def main() -> int:
    try:
        config = Path("config/config.yaml")
        root_id = folder_id(config)
        secret = os.environ.get(SECRET_NAME)
        if not secret:
            raise SetupError("Drive credentials not configured")
        credentials = parse_credentials(secret)
        credentials.refresh(Request())
        with AuthorizedSession(credentials) as session:
            verify_folder(session, root_id)
            result = inspect(Drive(session), root_id)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (PointerDiagnosticError, RecoveryError, PublishError, SetupError) as exc:
        print("FAILED " + str(exc))
    except Exception:
        print("FAILED pointer inspection unavailable; production unchanged")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
