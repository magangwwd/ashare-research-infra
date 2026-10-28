"""Read-only production-credential probe for the two independent Drive roots.

Never downloads market data, uploads, modifies pointers, or deletes files.
Only reports allowlisted marker presence and safe status codes.
"""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import subprocess

TASK01_REQUIRED = {"current.json": "file", "versions": "folder", "staging": "folder"}
GOLD_REQUIRED = {"manifest.json": "file", "instruments.parquet": "file",
                 "daily_snapshot.parquet": "file"}
FOLDER_MIME = "application/vnd.google-apps.folder"


def inspect_markers(entries, required):
    """Fail closed on duplicates and incorrect file types."""
    found = {}
    for entry in entries:
        name = entry.get("name", entry.get("Name"))
        if name not in required:
            continue
        is_folder = (entry.get("mimeType") == FOLDER_MIME
                     or entry.get("IsDir") is True)
        kind = "folder" if is_folder else "file"
        found.setdefault(name, []).append(kind)
    return {
        "missing": sorted(set(required) - set(found)),
        "duplicate": sorted(name for name, kinds in found.items() if len(kinds) != 1),
        "type_mismatch": sorted(name for name, kinds in found.items()
                                if any(kind != required[name] for kind in kinds)),
    }



def inspect_task01_snapshot(drive, entries):
    """Validate pointer-to-manifest integrity without downloading market partitions."""
    try:
        pointer_id = next(e["id"] for e in entries if e.get("name") == "current.json")
        pointer = json.loads(drive.read(pointer_id))
        if pointer.get("validated") is not True:
            return {"status": "INVALID_POINTER"}
        version_id = pointer.get("version_folder_id")
        manifest_id = pointer.get("manifest_file_id")
        expected_sha = pointer.get("manifest_sha256")
        if not all(isinstance(v, str) and v for v in (version_id, manifest_id, expected_sha)):
            return {"status": "INVALID_POINTER"}
        if len(expected_sha) != 64 or any(c not in "0123456789abcdef" for c in expected_sha):
            return {"status": "INVALID_POINTER"}
        version = drive.metadata(version_id)
        manifest_meta = drive.metadata(manifest_id)
        if (version.get("mimeType") != FOLDER_MIME or version.get("trashed")
                or manifest_meta.get("name") != "production_manifest.json"
                or version_id not in manifest_meta.get("parents", [])
                or manifest_meta.get("trashed")):
            return {"status": "BROKEN_VERSION_LINK"}
        raw = drive.read(manifest_id)
        if hashlib.sha256(raw).hexdigest() != expected_sha:
            return {"status": "MANIFEST_SHA_MISMATCH"}
        manifest = json.loads(raw)
        as_of = pointer.get("snapshot_as_of")
        date.fromisoformat(as_of)
        if (manifest.get("validated") is not True
                or manifest.get("version") != pointer.get("version")
                or manifest.get("snapshot_as_of") != as_of
                or manifest.get("file_count") != pointer.get("file_count")):
            return {"status": "MANIFEST_POINTER_MISMATCH"}
        return {"status": "PASS", "as_of": as_of}
    except Exception:
        return {"status": "SNAPSHOT_READ_ERROR"}


def inspect_gold_manifest(raw):
    """Check Gold's existing acceptance gates, without exposing raw content."""
    try:
        payload = json.loads(raw)
        as_of = str(payload["as_of_date"])[:10]
        date.fromisoformat(as_of)
        if (payload.get("validation", {}).get("primary_keys_unique") is not True
                or payload.get("validation", {}).get("ohlc_valid") is not True):
            return {"status": "GOLD_NOT_VALIDATED"}
        return {"status": "PASS", "as_of": as_of}
    except Exception:
        return {"status": "GOLD_MANIFEST_ERROR"}


def rclone_read(remote):
    completed = subprocess.run(
        ["rclone", "cat", remote], text=True, capture_output=True,
        check=False, timeout=90,
    )
    if completed.returncode:
        raise RuntimeError("RCLONE_READ_FAILED")
    return completed.stdout


def task01_probe():
    from google.auth.transport.requests import AuthorizedSession, Request
    from data_pipeline.drive_auth import folder_id, parse_credentials
    from data_pipeline.drive_publish import Drive

    secret = os.getenv("GDRIVE_OAUTH_JSON")
    if not secret:
        return {"status": "MISSING_SECRET"}
    try:
        creds = parse_credentials(secret)
        creds.refresh(Request())
        with AuthorizedSession(creds) as session:
            root = folder_id(Path("config/config.yaml"))
            entries = Drive(session).children(root)
            result = inspect_markers(entries, TASK01_REQUIRED)
            snapshot = (inspect_task01_snapshot(Drive(session), entries)
                        if not any(result.values()) else None)
    except Exception:
        # Never expose exception bodies: HTTP errors may include credential material.
        return {"status": "PROBE_ERROR"}
    return ({"status": "MARKER_MISMATCH", "markers": result} if any(result.values())
            else snapshot)


def rclone_list(remote):
    completed = subprocess.run(
        ["rclone", "lsjson", "--max-depth", "1", remote],
        text=True, capture_output=True, check=False, timeout=90,
    )
    if completed.returncode:
        raise RuntimeError("RCLONE_FAILED")
    payload = json.loads(completed.stdout)
    if not isinstance(payload, list):
        raise ValueError("INVALID_LIST")
    return payload


def gold_probe():
    if not os.getenv("RCLONE_CONFIG"):
        return {"status": "MISSING_SECRET"}
    try:
        # Validate the configured project root BEFORE checking Gold.
        strategy = rclone_list("gdrive:stock_state_strategy/")
        down = rclone_list("gdrive:down_data/")
        if ("LOCK_TO_MARKUP_PAPER_STRATEGY.md" not in
                {x.get("Name") for x in strategy}
                or "DATABASE_STATUS.md" not in {x.get("Name") for x in down}):
            return {"status": "WRONG_REMOTE_ROOT"}
        entries = rclone_list("gdrive:down_data/data/gold/main_board/")
        result = inspect_markers(entries, GOLD_REQUIRED)
        snapshot = (inspect_gold_manifest(rclone_read("gdrive:down_data/data/gold/main_board/manifest.json"))
                    if not any(result.values()) else None)
    except Exception:
        return {"status": "PROBE_ERROR"}
    return ({"status": "MARKER_MISMATCH", "markers": result} if any(result.values())
            else snapshot)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args(argv)
    report = {
        "schema_version": 1,
        "task01": task01_probe(),
        "gold": gold_probe(),
        "production_switch_allowed": False,
        "deletion_allowed": False,
        "note": "Pointer/manifest integrity alone does not validate every partition, freshness or strategy equivalence.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("SAFE_PROBE", report["task01"]["status"], report["gold"]["status"])
    return 0 if all(report[key]["status"] == "PASS" for key in ("task01", "gold")) else 2


if __name__ == "__main__":
    raise SystemExit(main())
