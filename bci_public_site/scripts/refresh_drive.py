#!/usr/bin/env python3
"""Read a verified public-research archive from the user's Drive folder.

Runs ONLY on trusted main-branch GitHub Actions, never in PR workflows.
OAuth credentials are held only in memory. No raw medical data is public.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys

FOLDER_ID = "1cXlP5QNatkjiz7tLDjXKgKry5Jklz3A5"
API = "https://www.googleapis.com/drive/v3/files"
NAME_RE = re.compile(r"(?:BCI_\d{8}_complete_public_snapshot_\d+|\d{8}T\d{6}Z_\d+)\.zip\Z")
MAX_SIZE = 25_000_000


def eligible_name(name: str) -> bool:
    return bool(NAME_RE.fullmatch(name or ""))


def choose(files):
    entries = [f for f in files if eligible_name(f.get("name", ""))
               and f.get("mimeType") in ("application/zip", "application/octet-stream")]
    entries.sort(key=lambda f: (f.get("modifiedTime", ""), f["name"]), reverse=True)
    return entries[0] if entries else None


def get_session():
    from google.auth.transport.requests import AuthorizedSession, Request
    from google.oauth2.credentials import Credentials
    raw = os.environ.get("GDRIVE_OAUTH_JSON")
    if not raw:
        raise RuntimeError("GDRIVE_OAUTH_JSON missing")
    try:
        data = json.loads(raw)
        credential = Credentials(
            token=None, refresh_token=data["refresh_token"],
            token_uri="https://oauth2.googleapis.com/token",
            client_id=data["client_id"], client_secret=data["client_secret"],
            scopes=["https://www.googleapis.com/auth/drive"])
        credential.refresh(Request())
        return AuthorizedSession(credential)
    except Exception:
        raise RuntimeError("Drive OAuth credential refresh failed") from None


def download_latest(target: Path):
    session = get_session()
    with session:
        check = session.get(f"{API}/{FOLDER_ID}",
                            params={"fields": "id,mimeType,name", "supportsAllDrives": "true"},
                            timeout=25)
        if check.status_code != 200:
            raise RuntimeError("Drive source folder cannot be read")
        info = check.json()
        if info.get("id") != FOLDER_ID or info.get("mimeType") != "application/vnd.google-apps.folder":
            raise RuntimeError("Drive folder ID mismatch")
        page = None
        files = []
        for _ in range(10):
            params = {"q": f"'{FOLDER_ID}' in parents and trashed = false",
                      "fields": "nextPageToken,files(id,name,mimeType,size,modifiedTime)",
                      "pageSize": 100, "orderBy": "modifiedTime desc"}
            if page:
                params["pageToken"] = page
            response = session.get(API, params=params, timeout=30)
            if response.status_code != 200:
                raise RuntimeError("Drive source listing failed")
            result = response.json()
            files.extend(result.get("files", []))
            page = result.get("nextPageToken")
            if not page:
                break
        candidate = choose(files)
        if not candidate:
            raise RuntimeError("No eligible verified public BCI snapshot in Drive")
        size = int(candidate.get("size") or 0)
        if size <= 0 or size > MAX_SIZE:
            raise RuntimeError("Drive source file size failed safe limit")
        file_id = candidate["id"]
        response = session.get(f"{API}/{file_id}", params={"alt": "media"}, timeout=90)
        if response.status_code != 200 or len(response.content) != size:
            raise RuntimeError("Drive snapshot download incomplete")
        raw = response.content
        if raw[:2] != b"PK":
            raise RuntimeError("Drive snapshot is not a ZIP archive")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        evidence = {"downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
                    "drive_file_id": file_id, "name": candidate["name"],
                    "size": size, "sha256": hashlib.sha256(raw).hexdigest(),
                    "source_folder_id": FOLDER_ID}
        return evidence


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    try:
        result = download_latest(Path(args.output))
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                     encoding="utf-8")
        print("PASS Drive snapshot downloaded and SHA-256 recorded; " +
              "bytes=" + str(result["size"]) + " sha256=" + result["sha256"])
    except Exception as exc:
        print("FAIL Drive snapshot retrieval: " + type(exc).__name__)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
