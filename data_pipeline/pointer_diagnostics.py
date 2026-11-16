"""Read-only production pointer inspection; never transcribe Drive IDs by hand.

Usage: python -m data_pipeline.pointer_diagnostics
Requires GDRIVE_OAUTH_JSON and existing config/config.yaml.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from google.auth.transport.requests import AuthorizedSession, Request
from requests.exceptions import ConnectionError, HTTPError, Timeout

from .drive_auth import SECRET_NAME, SetupError, folder_id, parse_credentials
from .drive_publish import Drive, PublishError
from .drive_restore import RecoveryError, load_pointer


class PointerDiagnosticError(Exception):
    """Safe, non-sensitive failure category."""


def _drive_failure(exc: Exception, operation: str) -> PointerDiagnosticError:
    """Classify transport errors without exposing IDs, URLs or credentials."""
    if isinstance(exc, PublishError):
        # Drive.request wraps transport failures in a sanitized PublishError.
        # Parse only the bounded status token, never echo its exception text.
        match = re.match(r"^Drive request failed \((401|403|404|429|500|502|503|504|network)\); operation=", str(exc))
        if match:
            status = match.group(1)
            if status == "404":
                return PointerDiagnosticError(f"{operation}_not_found_or_hidden: 404 does not prove deletion")
            if status in ("401", "403"):
                return PointerDiagnosticError(f"{operation}_permission_denied")
            if status == "network":
                return PointerDiagnosticError(f"{operation}_network_unavailable")
            return PointerDiagnosticError(f"{operation}_service_unavailable")
        return PointerDiagnosticError(f"{operation}_unexpected_error")
    if isinstance(exc, HTTPError):
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        if status == 404:
            return PointerDiagnosticError(f"{operation}_not_found_or_hidden: 404 does not prove deletion")
        if status in (401, 403):
            return PointerDiagnosticError(f"{operation}_permission_denied: HTTP {status}")
        if status == 429 or (isinstance(status, int) and status >= 500):
            return PointerDiagnosticError(f"{operation}_service_unavailable: HTTP {status}")
        return PointerDiagnosticError(f"{operation}_http_error")
    if isinstance(exc, (Timeout, ConnectionError)):
        return PointerDiagnosticError(f"{operation}_network_unavailable")
    return PointerDiagnosticError(f"{operation}_unexpected_error")


def inspect(drive, root_id: str) -> dict:
    pointer, pointer_id = load_pointer(drive, root_id)
    # IDs are used directly from parsed JSON, never copied or reconstructed.
    folder_id_value = pointer["version_folder_id"]
    manifest_id = pointer["manifest_file_id"]
    try:
        folder = drive.metadata(folder_id_value)
        manifest = drive.metadata(manifest_id)
    except Exception as exc:
        raise _drive_failure(exc, "referenced_file_metadata") from None
    if folder.get("id") != folder_id_value or folder.get("mimeType") != "application/vnd.google-apps.folder" or folder.get("trashed"):
        raise PointerDiagnosticError("version_folder_identity_mismatch")
    if (manifest.get("id") != manifest_id
            or manifest.get("name") != "production_manifest.json"
            or manifest.get("parents") != [folder_id_value]
            or manifest.get("trashed")):
        raise PointerDiagnosticError("manifest_identity_or_parent_mismatch")
    try:
        body = drive.read(manifest_id)
    except Exception as exc:
        raise _drive_failure(exc, "manifest_content") from None
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
