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
