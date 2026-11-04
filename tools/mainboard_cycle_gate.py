"""Offline, fail-closed Task01/Gold decision receipt; NO Drive writes or network access."""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path

ACTIVE_STATES = frozenset({"queued", "in_progress", "waiting", "pending", "requested"})


def evaluate(pointer: dict, manifest: dict, manifest_bytes: bytes,
             gold: dict, run: dict) -> dict:
    """Consume verified caller-provided run and manifest snapshots. Never authorize go-live."""
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
    except (TypeError, KeyError, ValueError):
        problems.append("INVALID_AS_OF_DATE")
    active = run.get("status") in ACTIVE_STATES
    if not isinstance(run.get("id"), int) or run.get("id") <= 0 or not run.get("status"):
        problems.append("TASK01_RUN_EVIDENCE_MISSING")
    elif not active and (run.get("status") != "completed" or run.get("conclusion") != "success"):
        problems.append("TASK01_RUN_NOT_SUCCESSFUL")
    if active:
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
    parser.add_argument("--task01-run", type=Path, required=True)
    args = parser.parse_args()
    raw = args.task01_manifest.read_bytes()
    result = evaluate(
        json.loads(args.task01_pointer.read_text(encoding="utf-8")),
        json.loads(raw),
        raw,
        json.loads(args.gold_manifest.read_text(encoding="utf-8")),
        json.loads(args.task01_run.read_text(encoding="utf-8")),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if result["decision"] == "SHADOW_VALIDATION_REQUIRED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
