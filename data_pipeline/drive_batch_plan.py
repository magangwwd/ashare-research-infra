"""Deterministic, read-only batch planning for a future guarded Task01 publisher.

This module NEVER uploads, mutates Drive or commits a production pointer.
A production runner must separately verify source/run identity, checkpoint
expiry, every staged SHA, stable pointer and the existing publication gates.
"""
from __future__ import annotations

import hashlib
import json


class BatchPlanError(ValueError):
    pass


def plan_batches(items, *, batch_size=500):
    """Partition immutable inventory without losing, duplicating or reordering paths."""
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or not 1 <= batch_size <= 500:
        raise BatchPlanError("batch_size must be between 1 and 500")
    if not isinstance(items, list):
        raise BatchPlanError("inventory must be a list")
    seen = set()
    normalized = []
    for item in items:
        if not isinstance(item, dict):
            raise BatchPlanError("invalid inventory entry")
        path, size, sha = item.get("path"), item.get("bytes"), item.get("sha256")
        if not isinstance(path, str) or not path or path.startswith("/") or "\\" in path or any(
            segment in ("", ".", "..") for segment in path.split("/")
        ):
            raise BatchPlanError("unsafe inventory path")
        if path in seen:
            raise BatchPlanError("duplicate inventory path")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise BatchPlanError("invalid inventory size")
        if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            raise BatchPlanError("invalid inventory SHA256")
        seen.add(path)
        normalized.append({"path": path, "bytes": size, "sha256": sha})
    fingerprint = hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        "schema_version": 1,
        "inventory_sha256": fingerprint,
        "file_count": len(normalized),
        "total_bytes": sum(i["bytes"] for i in normalized),
        "batch_size": batch_size,
        "batches": [
            {"index": n // batch_size, "file_count": len(chunk),
             "total_bytes": sum(i["bytes"] for i in chunk),
             "paths": [i["path"] for i in chunk]}
            for n in range(0, len(normalized), batch_size)
            for chunk in [normalized[n:n + batch_size]]
        ],
        "production_switch_allowed": False,
    }


def reconcile_verified_batches(items, receipts, *, expected_inventory_sha256,
                               expected_stage_id, expected_source_run_id, batch_size=500):
    """Plan missing uploads using independently verified remote SHA receipts.

    Receipts MUST come from fresh Drive media reads of the exact stage and
    source snapshot, not an untrusted local checkpoint or filename match.
    This is read-only: it does not upload or authorize a pointer commit.
    """
    plan = plan_batches(items, batch_size=batch_size)
    if expected_inventory_sha256 != plan["inventory_sha256"]:
        raise BatchPlanError("inventory fingerprint changed; do not resume")
    if not isinstance(expected_stage_id, str) or not expected_stage_id or not isinstance(expected_source_run_id, str) or not expected_source_run_id:
        raise BatchPlanError("missing stage or source identity")
    if not isinstance(receipts, list):
        raise BatchPlanError("remote receipts must be a list")
    expected = {i["path"]: i for i in items}
    verified = set()
    for receipt in receipts:
        if not isinstance(receipt, dict):
            raise BatchPlanError("invalid remote receipt")
        path = receipt.get("path")
        if (receipt.get("stage_id") != expected_stage_id or
                receipt.get("source_run_id") != expected_source_run_id):
            raise BatchPlanError("receipt source or stage mismatch")
        if path not in expected or path in verified:
            raise BatchPlanError("unknown or duplicate remote receipt")
        item = expected[path]
        if (receipt.get("bytes") != item["bytes"] or
                receipt.get("sha256") != item["sha256"] or
                not isinstance(receipt.get("file_id"), str) or
                not receipt["file_id"]):
            raise BatchPlanError("remote receipt mismatch; do not resume")
        verified.add(path)
    missing = [i for i in items if i["path"] not in verified]
    result = plan_batches(missing, batch_size=batch_size)
    result["source_inventory_sha256"] = plan["inventory_sha256"]
    result["verified_file_count"] = len(verified)
    result["missing_file_count"] = len(missing)
    result["production_switch_allowed"] = False
    return result


def validate_resume_checkpoint(checkpoint, *, inventory_sha256, stage_id,
                               source_run_id, now_epoch):
    """Fail-closed validation of persisted metadata; never treat it as SHA proof.

    Caller must still re-read every remote file and verify its bytes/SHA256.
    No production pointer is ever authorized by this validation.
    """
    if not isinstance(checkpoint, dict) or checkpoint.get("schema_version") != 1:
        raise BatchPlanError("unsupported checkpoint schema")
    if (not isinstance(inventory_sha256, str) or len(inventory_sha256) != 64 or
            any(c not in "0123456789abcdef" for c in inventory_sha256)):
        raise BatchPlanError("invalid expected inventory SHA")
    if not isinstance(stage_id, str) or not stage_id or not isinstance(source_run_id, str) or not source_run_id:
        raise BatchPlanError("invalid expected stage/source")
    if (checkpoint.get("inventory_sha256") != inventory_sha256 or
            checkpoint.get("stage_id") != stage_id or
            checkpoint.get("source_run_id") != source_run_id):
        raise BatchPlanError("checkpoint identity mismatch")
    expiry = checkpoint.get("expires_at_epoch")
    if (not isinstance(now_epoch, (int, float)) or isinstance(now_epoch, bool) or
            not isinstance(expiry, (int, float)) or isinstance(expiry, bool) or
            not now_epoch < expiry):
        raise BatchPlanError("checkpoint expired or time invalid")
    if checkpoint.get("production_switch_allowed") is not False:
        raise BatchPlanError("checkpoint must prohibit production switch")
    return {"checkpoint_valid": True, "remote_sha_revalidation_required": True,
            "production_switch_allowed": False}
