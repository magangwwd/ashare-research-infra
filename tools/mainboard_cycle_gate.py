"""Fail-closed, offline decision receipt for Task01/Gold mainboard demo.

Evidence gate only: never uploads, dispatches, or authorizes a production switch.
"""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path

ACTIVE_STATES = frozenset({"queued", "in_progress", "waiting", "pending", "requested"})


def evaluate(pointer: dict, manifest: dict, manifest_bytes: bytes,
             gold: dict, run: dict) -> dict:
    """Select one non-destructive next action from already-fetched evidence."""
    problems = []
    actual_sha = hashlib.sha256(manifest_bytes).hexdigest()
    if pointer.get("manifest_sha256") != actual_sha:
        problems.append("TASK01_MANIFEST_SHA_MISMATCH")
    if pointer.get("validated") is not True or manifest.get("validated") is not True:
        problems.append("TASK01_NOT_VALIDATED")
    for field in ("version", "snapshot_as_of", "file_count", "total_bytes"):
        if pointer.get(field) != manifest.get(field):
            problems.append(f"TASK01_{field.upper()}_MISMATCH")
    if not pointer.get("critical_history_complete", False):
        problems.append("TASK01_HISTORY_INCOMPLETE")
    quality = gold.get("validation") or {}
    if quality.get("primary_keys_unique") is not True or quality.get("ohlc_valid") is not True:
        problems.append("GOLD_QUALITY_NOT_VALIDATED")
    try:
        task_date = date.fromisoformat(pointer["snapshot_as_of"])
        gold_date = date.fromisoformat(gold["as_of_date"])
        if task_date < gold_date:
            problems.append("TASK01_BEHIND_GOLD")
    except (KeyError, TypeError, ValueError):
        problems.append("INVALID_AS_OF_DATE")

    if run.get("status") in ACTIVE_STATES:
        decision = "WAIT_ACTIVE_TASK01"
        next_action = "CHECK_SAME_RUN_JOBS_ARTIFACTS_AND_DRIVE_POINTER_AFTER_COMPLETION"
    elif problems:
        decision = "BLOCKED"
        next_action = "DIAGNOSE_TASK01_PUBLICATION_OR_DATA_GAPS_WITHOUT_RETRIGGERING"
    else:
        decision = "SHADOW_VALIDATION_REQUIRED"
        next_action = "VERIFY_FIVE_DAYS_STRATEGY_AND_ROLLBACK_BEFORE_SWITCH"

    return {
        "schema_version": "mainboard-cycle-receipt-v1",
        "decision": decision,
        "issues": sorted(set(problems)),
        "next_action": next_action,
        "task01_run_id": run.get("id"),
        "task01_run_status": run.get("status"),
        "task01_manifest_sha256": actual_sha,
        "task01_as_of": pointer.get("snapshot_as_of"),
        "gold_as_of": gold.get("as_of_date"),
        "production_switch_allowed": False,
        "retrigger_allowed": False,
        "frozen_v1_unchanged": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task01-pointer", type=Path, required=True)
    parser.add_argument("--task01-manifest", type=Path, required=True)
    parser.add_argument("--gold-manifest", type=Path, required=True)
    parser.add_argument("--task01-run", type=Path, required=True,
                        help="Read-only GitHub Actions run JSON exported by the caller")
    args = parser.parse_args()
    raw = args.task01_manifest.read_bytes()
    receipt = evaluate(
        json.loads(args.task01_pointer.read_text(encoding="utf-8")),
        json.loads(raw),
        raw,
        json.loads(args.gold_manifest.read_text(encoding="utf-8")),
        json.loads(args.task01_run.read_text(encoding="utf-8")),
    )
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if receipt["decision"] == "SHADOW_VALIDATION_REQUIRED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
