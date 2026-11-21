"""Read-only Drive media receipt verification. Never grants upload or publish permission."""
from __future__ import annotations

import hashlib

from .drive_batch_plan import BatchPlanError, plan_batches


def verify_remote_media_receipts(items, candidates, *, stage_id, source_run_id,
                                 read_metadata, read_media):
    """Re-read each candidate's exact Drive object and hash its actual bytes.

    Callbacks must be authenticated Drive readers. No cached/local receipt is
    accepted as evidence. Any mismatch aborts the entire reconciliation.
    """
    plan_batches(items)
    if not isinstance(stage_id, str) or not stage_id or not isinstance(source_run_id, str) or not source_run_id:
        raise BatchPlanError("missing stage/source identity")
    if not callable(read_metadata) or not callable(read_media) or not isinstance(candidates, list):
        raise BatchPlanError("invalid remote reader or candidates")
    expected = {item["path"]: item for item in items}
    paths, ids, receipts = set(), set(), []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise BatchPlanError("invalid remote candidate")
        path, file_id = candidate.get("path"), candidate.get("file_id")
        if (not isinstance(path, str) or path not in expected or path in paths
                or not isinstance(file_id, str) or not file_id or file_id in ids):
            raise BatchPlanError("duplicate or unknown remote object")
        paths.add(path)
        ids.add(file_id)
        try:
            meta = read_metadata(file_id)
            data = read_media(file_id)
        except Exception as exc:
            raise BatchPlanError("remote media verification unavailable") from None
        if not isinstance(meta, dict) or meta.get("id") != file_id or meta.get("trashed") is not False:
            raise BatchPlanError("remote object identity mismatch")
        if meta.get("parents") != [stage_id] or meta.get("name") != path:
            raise BatchPlanError("remote stage or path mismatch")
        # Source identity must be bound to the remote object, not to a local receipt.
        props = meta.get("appProperties")
        if not isinstance(props, dict) or props.get("source_run_id") != source_run_id:
            raise BatchPlanError("remote source identity mismatch")
        if not isinstance(data, bytes):
            raise BatchPlanError("remote media bytes unavailable")
        item = expected[path]
        digest = hashlib.sha256(data).hexdigest()
        if len(data) != item["bytes"] or digest != item["sha256"]:
            raise BatchPlanError("remote media SHA or size mismatch")
        receipts.append({"path": path, "file_id": file_id, "stage_id": stage_id,
                         "source_run_id": source_run_id, "bytes": len(data), "sha256": digest})
    return receipts
