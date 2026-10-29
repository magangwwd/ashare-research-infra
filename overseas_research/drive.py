"""Publish isolated immutable versions; switch the pointer only after readback."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlparse

FOLDER_ID = "1KfqJ2G0qq9D6bnzMEQNoYKuJhqVBUrua"
API = "https://www.googleapis.com/drive/v3/files"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"


def canonical(doc):
    return (json.dumps(doc, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def authenticate():
    # Reuse the already-configured repository OAuth, never log or change it.
    from google.auth.transport.requests import AuthorizedSession, Request
    from data_pipeline.drive_auth import parse_credentials
    credentials = parse_credentials(os.environ.get("GDRIVE_OAUTH_JSON", ""))
    credentials.refresh(Request())
    return AuthorizedSession(credentials)


def checked(response, allowed=(200, 201)):
    if response.status_code not in allowed:
        raise RuntimeError("drive_http_" + str(response.status_code))
    return response


def lookup(session, name, parent=FOLDER_ID):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise ValueError("invalid_drive_name")
    response = checked(session.get(API, params={
        "q": f"'{parent}' in parents and name = '{name}' and trashed = false",
        "fields": "files(id,name,mimeType),nextPageToken", "pageSize": 100,
        "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}, timeout=30)).json()
    files = response.get("files", [])
    if response.get("nextPageToken") or len(files) > 1:
        raise RuntimeError("ambiguous_drive_name")
    return files[0] if files else None


def read_previous(session):
    pointer = lookup(session, "current.json")
    if not pointer:
        return None, None
    response = checked(session.get(API + "/" + pointer["id"], params={"alt": "media"}, timeout=30))
    doc = response.json()
    if doc.get("schema") != "overseas_pointer_v1" or not re.fullmatch(r"[A-Za-z0-9_-]+", doc.get("snapshot_file_id", "")):
        raise ValueError("invalid_overseas_pointer")
    raw = checked(session.get(API + "/" + doc["snapshot_file_id"], params={"alt": "media"}, timeout=60)).content
    if hashlib.sha256(raw).hexdigest() != doc["snapshot_sha256"]:
        raise ValueError("previous_snapshot_hash_mismatch")
    snapshot = json.loads(raw)
    if snapshot.get("schema") != "overseas_observations_v1":
        raise ValueError("invalid_previous_snapshot")
    return snapshot, {"file_id": pointer["id"], "etag": response.headers.get("ETag"), "pointer": doc}


def upload_verified(session, name, raw, parent):
    if lookup(session, name, parent):
        raise RuntimeError("immutable_file_already_exists")
    init = checked(session.post(UPLOAD, params={"uploadType": "resumable", "supportsAllDrives": "true"},
        json={"name": name, "mimeType": "application/json" if name.endswith(".json") else "application/octet-stream",
              "parents": [parent]},
        headers={"X-Upload-Content-Type": "application/octet-stream", "X-Upload-Content-Length": str(len(raw))}, timeout=30))
    location = init.headers.get("Location", "")
    url = urlparse(location)
    if url.scheme != "https" or url.hostname != "www.googleapis.com":
        raise RuntimeError("untrusted_upload_location")
    result = checked(session.put(location, data=raw, headers={"Content-Type": "application/octet-stream"}, timeout=120)).json()
    file_id = result.get("id", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", file_id):
        raise RuntimeError("missing_uploaded_file_id")
    remote = checked(session.get(API + "/" + file_id, params={"alt": "media"}, timeout=120)).content
    sha = hashlib.sha256(raw).hexdigest()
    if hashlib.sha256(remote).hexdigest() != sha:
        raise RuntimeError("remote_readback_hash_mismatch")
    return {"file_id": file_id, "name": name, "bytes": len(raw), "sha256": sha}


def validate_bundle(root):
    snapshot = json.loads((root / "snapshot.json").read_bytes())
    if snapshot.get("schema") != "overseas_observations_v1" or snapshot.get("quality") not in ("passed", "passed_with_gaps"):
        raise ValueError("blocked_or_unknown_snapshot")
    if not snapshot.get("observations"):
        raise ValueError("no_accepted_observations")
    quality = json.loads((root / 'quality.json').read_bytes())
    for key in ('schema', 'generated_at_utc', 'quality', 'sources', 'limitations'):
        if quality.get(key) != snapshot.get(key):
            raise ValueError('quality_snapshot_mismatch')
    accepted = {s['source_id'] for s in snapshot['sources'] if s['status'] == 'accepted'}
    if any(r.get('source_id') not in accepted or r.get('consensus') is not None for r in snapshot['observations']):
        raise ValueError('unaccepted_source_or_synthetic_consensus')
    files = [("snapshot.json", (root / "snapshot.json").read_bytes()), ("quality.json", (root / "quality.json").read_bytes())]
    for entry in snapshot["raw_files"]:
        name = entry["path"]
        if not re.fullmatch(r"raw/[a-z0-9_]+\.bin", name):
            raise ValueError("unsafe_raw_path")
        path = root / name
        if path.is_symlink():
            raise ValueError("raw_symlink_rejected")
        raw = path.read_bytes()
        if len(raw) != entry["bytes"] or hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise ValueError("local_raw_hash_mismatch")
        files.append((Path(name).name, raw))
    return snapshot, files


def publish(root, session, run_id):
    root = Path(root)
    snapshot, files = validate_bundle(root)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id):
        raise ValueError("invalid_run_id")
    folder = checked(session.get(API + "/" + FOLDER_ID, params={
        "fields": "id,mimeType,capabilities(canAddChildren)", "supportsAllDrives": "true"}, timeout=30)).json()
    if folder.get("id") != FOLDER_ID or folder.get("mimeType") != "application/vnd.google-apps.folder" or not folder.get("capabilities", {}).get("canAddChildren"):
        raise RuntimeError("overseas_folder_write_access_unverified")
    previous, pointer_state = read_previous(session)
    if previous and datetime.fromisoformat(previous["generated_at_utc"]) >= datetime.fromisoformat(snapshot["generated_at_utc"]):
        raise ValueError("older_snapshot_cannot_replace_newer_pointer")
    name = "snapshot_" + datetime.fromisoformat(snapshot["generated_at_utc"]).strftime("%Y%m%dT%H%M%SZ") + "_" + run_id
    if lookup(session, name):
        raise RuntimeError("immutable_version_already_exists")
    version = checked(session.post(API, params={"supportsAllDrives": "true"}, json={
        "name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [FOLDER_ID]}, timeout=30)).json()["id"]
    receipts = []
    for filename, raw in files:
        if filename.endswith('.bin'):
            # Content-addressed originals are uploaded only when bytes change.
            object_name = 'raw_' + hashlib.sha256(raw).hexdigest() + '.bin'
            existing = lookup(session, object_name)
            if existing:
                remote = checked(session.get(API + '/' + existing['id'], params={'alt': 'media'}, timeout=120)).content
                if hashlib.sha256(remote).hexdigest() != hashlib.sha256(raw).hexdigest():
                    raise RuntimeError('existing_raw_object_hash_mismatch')
                uploaded = {'file_id': existing['id'], 'name': object_name, 'bytes': len(raw),
                            'sha256': hashlib.sha256(raw).hexdigest(), 'reused': True}
            else:
                uploaded = upload_verified(session, object_name, raw, FOLDER_ID)
            receipts.append({**uploaded, 'logical_name': filename})
        else:
            receipts.append(upload_verified(session, filename, raw, version))
    receipt = {"schema": "overseas_receipt_v1", "version_folder_id": version, "run_id": run_id,
               "generated_at_utc": snapshot["generated_at_utc"], "files": receipts,
               "status": "all_uploaded_files_sha256_readback_verified"}
    receipt_file = upload_verified(session, "receipt.json", canonical(receipt), version)
    snap_file = next(r for r in receipts if r["name"] == "snapshot.json")
    quality_file = next(r for r in receipts if r["name"] == "quality.json")
    pointer = {"schema": "overseas_pointer_v1", "generated_at_utc": snapshot["generated_at_utc"],
               "quality": snapshot["quality"], "version_folder_id": version,
               "snapshot_file_id": snap_file["file_id"], "snapshot_sha256": snap_file["sha256"],
               "quality_file_id": quality_file["file_id"], "receipt_file_id": receipt_file["file_id"],
               "source_status": {s["source_id"]: s["status"] for s in snapshot["sources"]}}
    raw = canonical(pointer)
    if pointer_state:
        headers = {"Content-Type": "application/json"}
        if pointer_state["etag"]:
            headers["If-Match"] = pointer_state["etag"]
        file_id = pointer_state["file_id"]
        checked(session.patch(UPLOAD + "/" + file_id, params={"uploadType": "media", "supportsAllDrives": "true"},
                              data=raw, headers=headers, timeout=60))
        check = checked(session.get(API + "/" + file_id, params={"alt": "media"}, timeout=60)).content
        if hashlib.sha256(check).hexdigest() != hashlib.sha256(raw).hexdigest():
            raise RuntimeError("pointer_readback_mismatch")
    else:
        file_id = upload_verified(session, "current.json", raw, FOLDER_ID)["file_id"]
    return {**receipt, "pointer_file_id": file_id, "snapshot_file_id": snap_file["file_id"],
            "quality": snapshot["quality"], "source_status": pointer["source_status"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--restore-previous", type=Path)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    try:
        with authenticate() as session:
            if args.restore_previous:
                snapshot, _ = read_previous(session)
                if snapshot:
                    args.restore_previous.parent.mkdir(parents=True, exist_ok=True)
                    args.restore_previous.write_bytes(canonical(snapshot))
                print("OVERSEAS_PREVIOUS_RESTORED" if snapshot else "OVERSEAS_FIRST_CAPTURE_BOOTSTRAP")
            else:
                if not args.input or not args.receipt:
                    raise ValueError("input_and_receipt_required")
                result = publish(args.input, session, os.environ.get("GITHUB_RUN_ID", "local") + "_" + os.environ.get("GITHUB_RUN_ATTEMPT", "1"))
                args.receipt.parent.mkdir(parents=True, exist_ok=True)
                args.receipt.write_bytes(canonical(result))
                print("OVERSEAS_DRIVE_READBACK_VERIFIED " + json.dumps({k: result[k] for k in
                      ("pointer_file_id", "snapshot_file_id", "quality", "source_status")}))
        return 0
    except Exception as exc:
        # OAuth and signed-upload exceptions must never expose tokens or URLs.
        print("OVERSEAS_DRIVE_FAILED error_type=" + type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
