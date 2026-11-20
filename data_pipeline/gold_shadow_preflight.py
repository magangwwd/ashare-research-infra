"""Fail-closed, offline contract gate for a future Task01 -> Gold shadow adapter.

Inputs are *copies* of the two production manifests and the Task01 current pointer.
This tool never accesses Drive, downloads Parquet, writes Gold, or changes pointers.
PASS means metadata is eligible for further shadow-data checks, NOT production switch.
"""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import re

_SHA = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_CLEAN = frozenset({
    "daily", "daily_basic", "adj_factor", "suspend_d", "namechange", "trade_cal",
})
_REQUIRED_GOLD = frozenset({"instruments.parquet", "daily_snapshot.parquet"})
_EXPECTED_UNITS = {
    "vol_hand_to_volume_shares": 100,
    "amount_thousand_cny_to_turnover_cny": 1000,
    "shares_ten_thousand_to_shares": 10000,
    "market_cap_ten_thousand_cny_to_cny": 10000,
}


def _day(value):
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError("invalid ISO date")
    return date.fromisoformat(value)


def evaluate(pointer, task_manifest_bytes, gold):
    """Return an auditable verdict. No input can grant a production switch."""
    reasons = []
    def block(code):
        if code not in reasons:
            reasons.append(code)

    try:
        task = json.loads(task_manifest_bytes)
    except (TypeError, ValueError, UnicodeDecodeError):
        task = {}
        block("TASK_MANIFEST_INVALID_JSON")

    if not isinstance(pointer, dict) or not isinstance(task, dict) or not isinstance(gold, dict):
        return {"status": "BLOCKED", "reasons": ["INVALID_DOCUMENT_TYPE"],
                "shadow_preflight_passed": False, "production_switch_allowed": False}

    pointer_sha = pointer.get("manifest_sha256")
    if (not isinstance(pointer_sha, str) or not _SHA.fullmatch(pointer_sha)
            or hashlib.sha256(task_manifest_bytes).hexdigest() != pointer_sha):
        block("TASK_MANIFEST_SHA_MISMATCH")
    if pointer.get("validated") is not True or task.get("validated") is not True:
        block("TASK_NOT_VALIDATED")
    for key in ("version", "snapshot_as_of", "source_run_id", "file_count", "total_bytes"):
        if pointer.get(key) != task.get(key):
            block("TASK_POINTER_MANIFEST_MISMATCH")
    if not isinstance(pointer.get("version_folder_id"), str) or not pointer["version_folder_id"]:
        block("TASK_VERSION_ID_MISSING")
    if not isinstance(pointer.get("manifest_file_id"), str) or not pointer["manifest_file_id"]:
        block("TASK_MANIFEST_ID_MISSING")
    if pointer.get("critical_history_complete") is not True or task.get("critical_history_complete") is not True:
        block("TASK_CRITICAL_HISTORY_INCOMPLETE")
    if (pointer.get("full_requested_core_range_complete") is not True
            or task.get("full_requested_core_range_complete") is not True):
        block("TASK_CORE_RANGE_INCOMPLETE")

    files = task.get("files")
    clean_categories = set()
    if not isinstance(files, list):
        block("TASK_FILE_LIST_INVALID")
    else:
        seen, total = set(), 0
        for item in files:
            if not isinstance(item, dict):
                block("TASK_FILE_ENTRY_INVALID")
                continue
            path, size, digest = item.get("path"), item.get("bytes"), item.get("sha256")
            if (not isinstance(path, str) or not path.startswith("data/")
                    or ".." in path.split("/") or path in seen
                    or not isinstance(size, int) or isinstance(size, bool) or size < 0
                    or not isinstance(digest, str) or not _SHA.fullmatch(digest)
                    or not isinstance(item.get("file_id"), str) or not item["file_id"]):
                block("TASK_FILE_ENTRY_INVALID")
            if isinstance(path, str):
                seen.add(path)
                parts = path.split("/")
                if len(parts) >= 4 and parts[:2] == ["data", "clean"]:
                    clean_categories.add(parts[2])
            if isinstance(size, int) and not isinstance(size, bool) and size >= 0:
                total += size
        if (pointer.get("file_count") != len(files)
                or pointer.get("total_bytes") != total):
            block("TASK_FILE_TOTAL_MISMATCH")
    missing_clean = sorted(_REQUIRED_CLEAN - clean_categories)
    if missing_clean:
        block("TASK_REQUIRED_CLEAN_DATA_MISSING")

    try:
        task_day = _day(pointer.get("snapshot_as_of"))
        gold_day = _day(gold.get("as_of_date"))
        if task_day != gold_day:
            block("AS_OF_DATE_MISMATCH")
    except (ValueError, TypeError):
        block("INVALID_AS_OF_DATE")

    validation = gold.get("validation")
    if (not isinstance(validation, dict)
            or validation.get("primary_keys_unique") is not True
            or validation.get("ohlc_valid") is not True):
        block("GOLD_NOT_VALIDATED")
    gold_files = gold.get("files")
    if not isinstance(gold_files, dict):
        block("GOLD_FILE_LIST_INVALID")
        gold_files = {}
    for name in _REQUIRED_GOLD:
        entry = gold_files.get(name)
        if (not isinstance(entry, dict) or not isinstance(entry.get("rows"), int)
                or isinstance(entry.get("rows"), bool) or entry["rows"] <= 0
                or not isinstance(entry.get("sha256"), str)
                or not _SHA.fullmatch(entry["sha256"])):
            block("GOLD_CORE_MANIFEST_INVALID")
    if gold.get("unit_conversions") != _EXPECTED_UNITS:
        block("GOLD_UNIT_CONTRACT_MISMATCH")

    return {
        "schema_version": 1,
        "status": "PASS_METADATA_ONLY" if not reasons else "BLOCKED",
        "reasons": sorted(reasons),
        "task01_as_of": pointer.get("snapshot_as_of"),
        "gold_as_of": gold.get("as_of_date"),
        "task01_manifest_sha256": hashlib.sha256(task_manifest_bytes).hexdigest(),
        "task01_file_count": pointer.get("file_count"),
        "missing_clean_categories": missing_clean,
        "shadow_preflight_passed": not reasons,
        "production_switch_allowed": False,
        "deletion_allowed": False,
        "parquet_rows_verified": False,
        "strategy_equivalence_verified": False,
        "note": "Metadata only; 5 trading days, Parquet values, frozen V1 and rollback remain unverified.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pointer", required=True, type=Path)
    parser.add_argument("--task-manifest", required=True, type=Path)
    parser.add_argument("--gold-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    report = evaluate(
        json.loads(args.pointer.read_text(encoding="utf-8")),
        args.task_manifest.read_bytes(),
        json.loads(args.gold_manifest.read_text(encoding="utf-8")),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("GOLD_SHADOW_PREFLIGHT", report["status"], ",".join(report["reasons"]))
    return 0 if report["shadow_preflight_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
