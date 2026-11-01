"""Narrow, read-only Drive diagnostic for Task01/Gold shadow Parquet comparison.

Only the fixed Drive files below may be fetched. Never mutate Drive,
upload data, log credentials, or authorize a production switch. Error messages
are fixed-stage codes; raw HTTP/OAuth exceptions are never exposed.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from data_pipeline.drive_auth import parse_credentials

# Previously observed Drive bytes, checked against official Task01 and Gold
# manifest contracts where they cover these files; never trust a changing
# historical Gold partition as an authoritative manifest signature.
FILES = {
    "task_pointer": ("1Z51dXKxxt2J_4jtcW7NcSqsNt3V3qMdB", "5d712e4c30c5ccc9c70ebd6672ebc401df97ba14f5d46d07e877baf915482926"),
    "task_manifest": ("1RzoGA4-RqdqQQSzmaE6M3wkiSh9-GVG1", "118e6afb6febd3993656afce9b46452809e5a05bf412db7c87515a891bc3e9e8"),
    "gold_manifest": ("1wO2UFU4-kQsujMLo9G8rQLoUIeSRaCqn", "3687f018cad6c96bdaf704217900925a773cc90fcb050031d6dd07cf20f33580"),
    "task_oct": ("11J4PMUpBHn-wK5bjXG7mckYbssd9cHwx", "22c1fa25caba35d49fa662f03ffc83f05572039672ecf1250df112dca9990858"),
    "gold_oct": ("1OcZ9Jvg_0MtzccR4n80vidu0S7b2Bsvu", "e084260b2d6829f3e0f439a877a25b94b866fa480dc05e8e057c444b9db23cca"),
}
OUTPUTS = {
    "task_pointer": "current.json",
    "task_manifest": "production_manifest.json",
    "gold_manifest": "gold_manifest.json",
    "task_oct": "task_oct.parquet",
    "gold_oct": "gold_oct.parquet",
}


class ProbeBlocked(Exception):
    """Only fixed, non-secret class labels should be printed by the runner."""


def get_credentials():
    secret = os.environ.get("GDRIVE_OAUTH_JSON")
    if not secret:
        raise ProbeBlocked("MISSING_GDRIVE_OAUTH_SECRET")
    try:
        creds = parse_credentials(secret)
    except Exception:
        raise ProbeBlocked("INVALID_OAUTH_SECRET_FORMAT") from None
    try:
        from google.auth.transport.requests import Request
        creds.refresh(Request())
    except Exception:
        raise ProbeBlocked("OAUTH_REFRESH_FAILED") from None
    return creds


def fetch_verified(session, file_key, directory, *, max_bytes=20_000_000):
    if file_key not in FILES:
        raise ProbeBlocked("INVALID_FILE_KEY")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise ProbeBlocked("INVALID_MAX_BYTES")
    fid, expected_sha = FILES[file_key]
    directory = Path(directory)
    path = directory / OUTPUTS[file_key]
    if path.exists():
        raise ProbeBlocked("OUTPUT_ALREADY_EXISTS_" + file_key.upper())
    hasher, total = hashlib.sha256(), 0
    try:
        response = session.get(
            "https://www.googleapis.com/drive/v3/files/" + fid,
            params={"alt": "media", "supportsAllDrives": "true"},
            stream=True, timeout=(20, 120),
        )
    except Exception:
        raise ProbeBlocked("READ_REQUEST_FAILED_" + file_key.upper()) from None
    try:
        # Do not reveal IDs, response text, headers or access tokens.
        if response.status_code != 200:
            raise ProbeBlocked("READ_HTTP_" + str(response.status_code) + "_" + file_key.upper())
        with path.open("xb") as fh:
            try:
                for chunk in response.iter_content(1024 * 1024):
                    if chunk:
                        total += len(chunk)
                        if total > max_bytes:
                            raise ProbeBlocked("READ_SIZE_LIMIT_" + file_key.upper())
                        hasher.update(chunk)
                        fh.write(chunk)
            except ProbeBlocked:
                raise
            except Exception:
                raise ProbeBlocked("READ_STREAM_FAILED_" + file_key.upper()) from None
        if hasher.hexdigest() != expected_sha:
            raise ProbeBlocked("PINNED_SHA_CHANGED_" + file_key.upper())
        return path
    except ProbeBlocked:
        path.unlink(missing_ok=True)
        raise
    finally:
        try:
            response.close()
        except Exception:
            pass


def probe_snapshot(directory):
    from google.auth.transport.requests import AuthorizedSession

    directory = Path(directory)
    if directory.exists():
        raise ProbeBlocked("OUTPUT_DIRECTORY_ALREADY_EXISTS")
    directory.mkdir(parents=True)
    creds = get_credentials()
    try:
        with AuthorizedSession(creds) as session:
            for key in FILES:
                fetch_verified(session, key, directory)
            for key in ("task_pointer", "gold_manifest"):
                path = directory / OUTPUTS[key]
                path.unlink()
                fetch_verified(session, key, directory)
    except ProbeBlocked:
        raise
    except Exception:
        raise ProbeBlocked("SESSION_FAILURE") from None
    return directory


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--output-dir", type=Path, required=True)
    args = p.parse_args()
    try:
        probe_snapshot(args.output_dir)
    except ProbeBlocked as exc:
        print("BLOCKED", str(exc))
        return 2
    print("PASS READ_ONLY_SHA_PINNED_POINTER_STABLE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
