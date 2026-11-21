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
    verified_file_ids = set()
    for receipt in receipts:
        if not isinstance(receipt, dict):
            raise BatchPlanError("invalid remote receipt")
        path = receipt.get("path")
        if (receipt.get("stage_id") != expected_stage_id or
                receipt.get("source_run_id") != expected_source_run_id):
            raise BatchPlanError("receipt source or stage mismatch")
        if not isinstance(path, str) or path not in expected or path in verified:
            raise BatchPlanError("unknown or duplicate remote receipt")
        item = expected[path]
        if (not isinstance(receipt.get("bytes"), int) or isinstance(receipt.get("bytes"), bool) or
                receipt.get("bytes") != item["bytes"] or
                receipt.get("sha256") != item["sha256"] or
                not isinstance(receipt.get("file_id"), str) or
                not receipt["file_id"]):
            raise BatchPlanError("remote receipt mismatch; do not resume")
        if receipt["file_id"] in verified_file_ids:
            raise BatchPlanError("same remote file ID used for multiple paths")
        verified_file_ids.add(receipt["file_id"])
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


def validate_persisted_receipt_ledger(ledger, *, inventory_sha256, stage_id,
                                      source_run_id, pointer_sha256):
    """Validate durable ledger identity only; receipts remain untrusted until remote re-read.

    Never use ledger entries as verified remote receipts without fresh media SHA.
    The immutable pointer snapshot prevents silent reuse after production moves.
    """
    if not isinstance(ledger, dict) or ledger.get("schema_version") != 1:
        raise BatchPlanError("unsupported ledger schema")
    for name, expected in (("inventory_sha256", inventory_sha256),
                           ("stage_id", stage_id),
                           ("source_run_id", source_run_id),
                           ("pointer_sha256", pointer_sha256)):
        if not isinstance(expected, str) or not expected or ledger.get(name) != expected:
            raise BatchPlanError("ledger identity or pointer changed")
    for name in ("inventory_sha256", "pointer_sha256"):
        value = ledger[name]
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise BatchPlanError("invalid ledger SHA")
    if ledger.get("production_switch_allowed") is not False:
        raise BatchPlanError("ledger cannot authorize production")
    receipts = ledger.get("receipts")
    if not isinstance(receipts, list):
        raise BatchPlanError("ledger receipts must be a list")
    seen = set()
    file_ids = set()
    for receipt in receipts:
        if not isinstance(receipt, dict):
            raise BatchPlanError("invalid persisted receipt")
        path = receipt.get("path")
        if not isinstance(path, str) or not path or path in seen:
            raise BatchPlanError("duplicate or invalid persisted path")
        if (receipt.get("stage_id") != stage_id or
                receipt.get("source_run_id") != source_run_id):
            raise BatchPlanError("persisted receipt identity mismatch")
        file_id = receipt.get("file_id")
        if not isinstance(file_id, str) or not file_id or file_id in file_ids:
            raise BatchPlanError("missing or duplicate persisted remote file ID")
        file_ids.add(file_id)
        seen.add(path)
    return {"ledger_identity_valid": True,
            "untrusted_receipt_count": len(receipts),
            "remote_sha_revalidation_required": True,
            "production_switch_allowed": False}


def build_verified_receipt_ledger(items, receipts, *, stage_id, source_run_id,
                                  pointer_sha256):
    """Build a portable ledger snapshot, not an authorization to skip remote SHA reads.

    A caller must persist this with a durable conditional write (CAS) and a
    single-writer lease scoped to stage/source. A local runner file is NOT
    durable across GitHub Actions jobs. Do not use this to switch production.
    """
    plan = plan_batches(items)
    if (not isinstance(pointer_sha256, str) or len(pointer_sha256) != 64 or
            any(c not in "0123456789abcdef" for c in pointer_sha256)):
        raise BatchPlanError("invalid production pointer fingerprint")
    reconcile_verified_batches(
        items, receipts, expected_inventory_sha256=plan["inventory_sha256"],
        expected_stage_id=stage_id, expected_source_run_id=source_run_id)
    ledger = {
        "schema_version": 1,
        "inventory_sha256": plan["inventory_sha256"],
        "pointer_sha256": pointer_sha256,
        "stage_id": stage_id,
        "source_run_id": source_run_id,
        "production_switch_allowed": False,
        "receipts": [dict(receipt) for receipt in receipts],
    }
    validate_persisted_receipt_ledger(
        ledger, inventory_sha256=plan["inventory_sha256"],
        stage_id=stage_id, source_run_id=source_run_id,
        pointer_sha256=pointer_sha256)
    return ledger


def plan_resume_from_ledger(items, ledger, fresh_remote_receipts, *,
                            stage_id, source_run_id, pointer_sha256,
                            checkpoint, now_epoch, batch_size=500):
    """Read-only fail-closed recovery: persisted receipts are NEVER proof.

    Only fresh SHA-verified Drive media receipts may remove inventory entries
    from the next upload plan. An expired checkpoint blocks recovery entirely.
    """
    inventory = plan_batches(items, batch_size=batch_size)
    validate_resume_checkpoint(
        checkpoint, inventory_sha256=inventory["inventory_sha256"],
        stage_id=stage_id, source_run_id=source_run_id, now_epoch=now_epoch)
    validate_persisted_receipt_ledger(
        ledger, inventory_sha256=inventory["inventory_sha256"],
        stage_id=stage_id, source_run_id=source_run_id,
        pointer_sha256=pointer_sha256)
    if not isinstance(fresh_remote_receipts, list):
        raise BatchPlanError("fresh remote receipts required")
    # A durable ledger is only a hint. Even its listed files must be read
    # from Drive again; absent receipts are treated as missing.
    result = reconcile_verified_batches(
        items, fresh_remote_receipts,
        expected_inventory_sha256=inventory["inventory_sha256"],
        expected_stage_id=stage_id, expected_source_run_id=source_run_id,
        batch_size=batch_size)
    result["persisted_receipt_count_untrusted"] = len(ledger["receipts"])
    result["fresh_remote_sha_verified_count"] = len(fresh_remote_receipts)
    result["production_switch_allowed"] = False
    return result

def persist_local_ledger_exclusive(path, ledger, *, inventory_sha256, stage_id,
                                   source_run_id, pointer_sha256):
    """Atomically persist a LOCAL snapshot under an exclusive nonblocking lock.

    This is a runner-local safety primitive, NOT cross-run durability or a
    distributed Drive lease. Never use it to authorize production publication.
    """
    import os
    import tempfile
    from pathlib import Path

    validate_persisted_receipt_ledger(
        ledger, inventory_sha256=inventory_sha256, stage_id=stage_id,
        source_run_id=source_run_id, pointer_sha256=pointer_sha256)
    target = Path(path)
    if not target.parent.is_dir() or target.is_symlink():
        raise BatchPlanError("unsafe local ledger destination")
    lock_path = target.with_name(target.name + ".lock")
    try:
        lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise BatchPlanError("ledger writer already active; fail closed") from exc
    temporary = None
    try:
        with os.fdopen(lock_fd, "w") as lock:
            lock.write(str(os.getpid()))
            lock.flush()
            os.fsync(lock.fileno())
        fd, temporary = tempfile.mkstemp(prefix=".ledger-", dir=str(target.parent))
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(ledger, output, sort_keys=True, separators=(",", ":"))
            output.flush()
            os.fsync(output.fileno())
        if target.is_symlink():
            raise BatchPlanError("ledger destination changed to symlink")
        os.replace(temporary, target)
        temporary = None
        directory_fd = os.open(str(target.parent), os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary is not None:
            os.unlink(temporary)
        os.unlink(lock_path)
    return {"local_snapshot_written": True, "cross_run_durable": False,
            "production_switch_allowed": False}
