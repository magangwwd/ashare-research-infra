"""Read-only evidence gate for the Task01 -> Gold demonstration.

Inputs MUST be freshly exported GitHub/Drive evidence from the same audit round.
This offline tool does no networking, publication, dispatch, or production switch.
A green result only authorizes further *shadow analysis*, never a switch.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path


ACTIVE = frozenset({"queued", "in_progress", "pending", "waiting", "requested"})


def assess(pointer: dict, manifest: dict, raw_manifest: bytes,
           gold: dict, run: dict, artifacts: list[dict]) -> dict:
    """Produce a fail-closed machine-readable evidence receipt."""
    issues: list[str] = []
    sha = hashlib.sha256(raw_manifest).hexdigest()

    if pointer.get("manifest_sha256") != sha:
        issues.append("TASK01_MANIFEST_SHA_MISMATCH")
    if pointer.get("validated") is not True or manifest.get("validated") is not True:
        issues.append("TASK01_NOT_VALIDATED")
    for field in ("version", "snapshot_as_of", "source_run_id",
                  "file_count", "total_bytes"):
        if pointer.get(field) != manifest.get(field):
            issues.append(f"TASK01_{field.upper()}_MISMATCH")
    entries = manifest.get("files")
    if not isinstance(entries, list) or len(entries) != manifest.get("file_count"):
        issues.append("TASK01_FILE_COUNT_INCORRECT")
    elif (sum(f.get("bytes", -1) for f in entries)
          != manifest.get("total_bytes")):
        issues.append("TASK01_TOTAL_BYTES_INCORRECT")
    if pointer.get("critical_history_complete") is not True:
        issues.append("TASK01_HISTORY_INCOMPLETE")

    gold_quality = gold.get("validation") or {}
    if (gold_quality.get("primary_keys_unique") is not True
            or gold_quality.get("ohlc_valid") is not True):
        issues.append("GOLD_QUALITY_NOT_VALIDATED")
    try:
        task_date = date.fromisoformat(pointer["snapshot_as_of"])
        gold_date = date.fromisoformat(gold["as_of_date"])
        if task_date < gold_date:
            issues.append("TASK01_BEHIND_GOLD")
    except (KeyError, TypeError, ValueError):
        issues.append("INVALID_AS_OF_DATE")

    status = run.get("status")
    conclusion = run.get("conclusion")
    publication_step = run.get("publication_step_conclusion")
    acceptance_step = run.get("acceptance_step_conclusion")
    if status not in ACTIVE and (status != "completed"
                                 or conclusion != "success"
                                 or publication_step != "success"):
        issues.append("TASK01_PUBLICATION_NOT_SUCCESSFUL")
    if acceptance_step != "success":
        issues.append("TASK01_ACCEPTANCE_NOT_SUCCESSFUL")

    observed_artifacts = {
        a.get("name") for a in artifacts
        if a.get("expired") is False and isinstance(a.get("digest"), str)
        and a["digest"].startswith("sha256:")
    }
    checkpoint_observed = "task01-checkpoint" in observed_artifacts
    acceptance_report_observed = "task01-validation-report" in observed_artifacts
    if not acceptance_report_observed:
        issues.append("ACCEPTANCE_REPORT_ARTIFACT_UNAVAILABLE")

    if status in ACTIVE:
        decision = "WAIT_FOR_ACTIVE_RUN"
        next_action = "RECHECK_SAME_RUN_JOBS_ARTIFACTS_AND_DRIVE_POINTER"
    elif "TASK01_PUBLICATION_NOT_SUCCESSFUL" in issues:
        decision = "BLOCKED_PUBLISH"
        next_action = "VERIFY_SAME_RUN_STAGE_AND_CHECKPOINT_THEN_READONLY_SHA_AUDIT"
    elif issues:
        decision = "BLOCKED_METADATA"
        next_action = "DIAGNOSE_MANIFEST_DATA_AND_SOURCE_CONSISTENCY"
    else:
        decision = "SHADOW_VALIDATION_REQUIRED"
        next_action = "FIVE_TRADING_DAYS_FROZEN_V1_PARITY_PIT_AND_ROLLBACK"

    return {
        "schema_version": "mainboard-loop-receipt-1",
        "decision": decision,
        "issues": sorted(set(issues)),
        "next_action": next_action,
        "task01_run_id": run.get("id"),
        "run_status": status,
        "run_conclusion": conclusion,
        "task01_as_of": pointer.get("snapshot_as_of"),
        "gold_as_of": gold.get("as_of_date"),
        "task01_manifest_sha256": sha,
        "same_run_checkpoint_observed": checkpoint_observed,
        "same_run_acceptance_artifact_observed": acceptance_report_observed,
        "stage_sha_audit_completed": False,
        "production_switch_allowed": False,
        "automatic_retrigger_allowed": False,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--task01-pointer", type=Path, required=True)
    p.add_argument("--task01-manifest", type=Path, required=True)
    p.add_argument("--gold-manifest", type=Path, required=True)
    p.add_argument("--run-evidence", type=Path, required=True,
                   help="Current GitHub run plus verified job-step results.")
    p.add_argument("--artifacts", type=Path, required=True,
                   help="Same-run GitHub Actions artifacts JSON array.")
    args = p.parse_args()
    raw = args.task01_manifest.read_bytes()
    receipt = assess(
        json.loads(args.task01_pointer.read_text(encoding="utf-8")),
        json.loads(raw),
        raw,
        json.loads(args.gold_manifest.read_text(encoding="utf-8")),
        json.loads(args.run_evidence.read_text(encoding="utf-8")),
        json.loads(args.artifacts.read_text(encoding="utf-8")),
    )
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if receipt["decision"] == "SHADOW_VALIDATION_REQUIRED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
