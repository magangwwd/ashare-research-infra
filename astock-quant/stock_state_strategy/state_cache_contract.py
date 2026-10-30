"""Fingerprint and byte-verify state caches; old caches require explicit rebuild."""
import hashlib
import json
from pathlib import Path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(context: dict, files: dict[str, Path]) -> str:
    value = {"schema": 1, "context": context,
             "files": {name: file_sha256(path) for name, path in sorted(files.items())}}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def verify_cache(path: Path, expected: str) -> None:
    metadata = path.with_suffix(".meta.json")
    try:
        record = json.loads(metadata.read_text())
        valid = (record.get("fingerprint") == expected
                 and record.get("output_sha256") == file_sha256(path))
    except (OSError, ValueError):
        valid = False
    if not valid:
        raise RuntimeError("Unverified/stale state cache; explicit --rebuild-state-cache required")


def record_cache(path: Path, signature: str) -> None:
    metadata = path.with_suffix(".meta.json")
    temporary = metadata.with_suffix(".json.tmp")
    temporary.write_text(json.dumps({"fingerprint": signature, "output_sha256": file_sha256(path)}))
    temporary.replace(metadata)
