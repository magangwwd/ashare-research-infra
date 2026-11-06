"""Fail-closed, metadata-only inventory gate for Task01 and Gold Drive roots.

Inputs are JSON results from *actual* Drive folder listings, never file contents.
This gate cannot establish freshness, checksums, or strategy compatibility.
It has no Drive write/delete operations and never treats staging as production.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

REQUIRED = {
    "task01": ("current.json", "versions", "staging"),
    "gold": ("manifest.json", "instruments.parquet", "daily_snapshot.parquet"),
}
FOLDER = "application/vnd.google-apps.folder"


def entries(payload: object) -> list[dict]:
    if isinstance(payload, dict):
        payload = payload.get("results", payload.get("files"))
    if not isinstance(payload, list) or any(not isinstance(x, dict) for x in payload):
        raise ValueError("Expected a complete Drive folder listing as JSON array or results/files")
    return payload


def inspect(label: str, payload: object) -> dict:
    records = entries(payload)
    by_name: dict[str, list[dict]] = {}
    for item in records:
        name = item.get("title", item.get("name"))
        if not isinstance(name, str) or not name:
            raise ValueError("Drive listing contains a record without a name")
        by_name.setdefault(name, []).append(item)
    missing = [name for name in REQUIRED[label] if name not in by_name]
    duplicate = sorted(name for name in REQUIRED[label] if len(by_name.get(name, [])) > 1)
    type_mismatch = []
    for name in REQUIRED[label]:
        for item in by_name.get(name, []):
            mime = item.get("mime_type", item.get("mimeType", ""))
            is_folder = item.get("file_or_folder") == "folder" or mime == FOLDER
            expected_folder = label == "task01" and name in ("versions", "staging")
            if is_folder != expected_folder:
                type_mismatch.append(name)
    return {
        "root": label,
        "entry_count": len(records),
        "missing": missing,
        "duplicate": duplicate,
        "type_mismatch": sorted(set(type_mismatch)),
        "metadata_gate": "BLOCKED" if missing or duplicate or type_mismatch else "PASS",
    }


def audit(task01: object, gold: object) -> dict:
    roots = [inspect("task01", task01), inspect("gold", gold)]
    passed = all(root["metadata_gate"] == "PASS" for root in roots)
    return {
        "schema_version": 1,
        "status": "READY_FOR_CONTENT_VERIFICATION" if passed else "BLOCKED_MISSING_OR_INVALID_PRODUCTION_FILES",
        "production_switch_allowed": False,
        "deletion_allowed": False,
        "note": "Metadata alone never proves current.json validation, SHA256, as_of, Gold history, or strategy equivalence.",
        "roots": roots,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task01-listing", type=Path, required=True)
    parser.add_argument("--gold-listing", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    task01 = json.loads(args.task01_listing.read_text(encoding="utf-8"))
    gold = json.loads(args.gold_listing.read_text(encoding="utf-8"))
    report = audit(task01, gold)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(report["status"])
    return 0 if report["status"] == "READY_FOR_CONTENT_VERIFICATION" else 2


if __name__ == "__main__":
    raise SystemExit(main())
