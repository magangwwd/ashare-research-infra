"""Upload ETF input data and all audit/backtest reports to the dedicated Drive folder."""
from __future__ import annotations
import json, os, mimetypes, hashlib
import requests
from datetime import datetime, timezone
from pathlib import Path
from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials

FOLDER_ID = "18K9qbd2dj10fDmNR0PqJmuEmvEw_BWHD"
FILES = [
    Path("etf_single_rotation/data/prices.csv"),
    Path("etf_single_rotation/data/trade_calendar.csv"),
    Path("etf_single_rotation/results/data_quality.csv"),
    Path("etf_single_rotation/results/data_quality_gaps.csv"),
    Path("etf_single_rotation/results/data_quality_summary.json"),
    Path("etf_single_rotation/results/documented_suspension_gaps.csv"),
    Path("etf_single_rotation/results/documented_suspension_summary.json"),
    Path("etf_single_rotation/results/research_acceptance_gate.json"),
    Path("etf_single_rotation/state/run_checkpoint.json"),
    Path("etf_single_rotation/results/equity_curve.csv"),
    Path("etf_single_rotation/results/trades.csv"),
    Path("etf_single_rotation/results/summary.json"),
    Path("etf_single_rotation/results/strategy_comparison.csv"),
    Path("etf_single_rotation/results/walk_forward_oos_comparison.csv"),
    Path("etf_single_rotation/results/walk_forward_folds.csv"),
    Path("etf_single_rotation/results/walk_forward_training_scores.csv"),
    Path("etf_single_rotation/results/walk_forward_portfolio_equity.csv"),
    Path("etf_single_rotation/results/walk_forward_summary.json"),
    Path("etf_single_rotation/results/execution_accounting_audit.csv"),
    Path("etf_single_rotation/results/execution_accounting_implicit_turnover.csv"),
    Path("etf_single_rotation/results/execution_accounting_summary.json"),
    Path("etf_single_rotation/results/provider_gap_recheck.csv"),
    Path("etf_single_rotation/results/provider_gap_recheck_summary.json"),
    Path("etf_single_rotation/results/self_financing/self_financing_strategy_comparison.csv"),
    Path("etf_single_rotation/results/self_financing/self_financing_daily_ledger.csv"),
    Path("etf_single_rotation/results/self_financing/self_financing_trades.csv"),
    Path("etf_single_rotation/results/self_financing/self_financing_equity_curves.csv"),
    Path("etf_single_rotation/results/self_financing/self_financing_vs_exploratory.csv"),
    Path("etf_single_rotation/results/self_financing/walk_forward_folds.csv"),
    Path("etf_single_rotation/results/self_financing/walk_forward_training_scores.csv"),
    Path("etf_single_rotation/results/self_financing/walk_forward_portfolio_equity.csv"),
    Path("etf_single_rotation/results/self_financing/walk_forward_summary.json"),
    Path("etf_single_rotation/results/self_financing/self_financing_manifest.json"),
    Path("etf_single_rotation/results/self_financing/executable_walk_forward_folds.csv"),
    Path("etf_single_rotation/results/self_financing/executable_walk_forward_training_scores.csv"),
    Path("etf_single_rotation/results/self_financing/executable_walk_forward_ledger.csv"),
    Path("etf_single_rotation/results/self_financing/executable_walk_forward_trades.csv"),
    Path("etf_single_rotation/results/self_financing/executable_walk_forward_summary.json"),
    Path("etf_single_rotation/results/self_financing/cost_stress_strategy_comparison.csv"),
    Path("etf_single_rotation/results/self_financing/cost_stress_executable_walk_forward.csv"),
    Path("etf_single_rotation/results/self_financing/cost_stress_manifest.json"),
    Path("etf_single_rotation/results/self_financing/benchmark_self_financing_comparison.csv"),
    Path("etf_single_rotation/results/self_financing/benchmark_self_financing_equity.csv"),
    Path("etf_single_rotation/results/self_financing/benchmark_self_financing_manifest.json"),
    Path("etf_single_rotation/results/strategy_equity_curves.csv"),
    Path("etf_single_rotation/results/strategy_monthly_signals.csv"),
    Path("etf_single_rotation/results/strategy_daily_weights.csv"),
    Path("etf_single_rotation/results/strategy_experiment_manifest.json"),
]

PROGRESS = Path("etf_single_rotation/state/drive_upload_checkpoint.json")


def _file_hash(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _save_progress(progress):
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    stage = PROGRESS.with_suffix(".tmp")
    stage.write_text(json.dumps(progress, indent=2, ensure_ascii=False) + "\n", "utf-8")
    stage.replace(PROGRESS)


def _oauth_session():
    raw = os.environ.get("GDRIVE_OAUTH_JSON", "")
    try:
        info = json.loads(raw)
        creds = Credentials(
            token=None, refresh_token=info["refresh_token"],
            token_uri=info.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=info["client_id"], client_secret=info["client_secret"],
            scopes=["https://www.googleapis.com/auth/drive"]
        )
        creds.refresh(Request())
    except Exception:
        raise SystemExit("Drive OAuth unavailable or invalid; research results remain in GitHub artifact.") from None
    return AuthorizedSession(creds)



def _remote_metadata(session, file_id):
    """Read by ID; never trust a checkpoint or an upload response alone."""
    try:
        response = session.get(
            f"https://www.googleapis.com/drive/v3/files/{file_id}",
            params={"fields": "id,name,size,md5Checksum,parents"},
            timeout=30,
        )
    except requests.exceptions.RequestException as exc:
        raise SystemExit("Drive metadata readback unavailable; refusing unsafe reuse.") from exc
    if response.status_code != 200:
        raise SystemExit(f"Drive metadata readback failed: HTTP {response.status_code}")
    return response.json()


def _verify_metadata(item, path, expected_name):
    """Drive binary file metadata must match local bytes and destination."""
    local_md5 = hashlib.md5(path.read_bytes()).hexdigest()
    if (item.get("name") != expected_name
            or FOLDER_ID not in item.get("parents", [])
            or str(item.get("size")) != str(path.stat().st_size)
            or item.get("md5Checksum") != local_md5):
        raise SystemExit(
            f"Drive file mismatch or missing checksum for {expected_name}; "
            "manual review required, no upload."
        )


def _find_existing(session, path, target_name):
    """Read-only exact-name preflight; ambiguous duplicates always fail closed."""
    escaped = target_name.replace("\\", "\\\\").replace("'", "\\'")
    query = f"'{FOLDER_ID}' in parents and name = '{escaped}' and trashed = false"
    matches, page_token = [], None
    seen_page_tokens = set()
    while True:
        params = {
            "q": query,
            "fields": "nextPageToken,incompleteSearch,files(id,name,size,md5Checksum,parents)",
            "pageSize": 100,
        }
        if page_token:
            params["pageToken"] = page_token
        try:
            response = session.get(
                "https://www.googleapis.com/drive/v3/files",
                params=params, timeout=30,
            )
        except requests.exceptions.RequestException as exc:
            raise SystemExit("Drive preflight unavailable; no upload attempted.") from exc
        if response.status_code != 200:
            raise SystemExit(f"Drive preflight failed: HTTP {response.status_code}")
        payload = response.json()
        # A malformed HTTP 200 response cannot prove the target name is absent.
        # Treat missing/null/wrong-type files as unsafe, never as an empty list.
        if not isinstance(payload, dict) or not isinstance(payload.get("files"), list):
            raise SystemExit("Drive preflight malformed file list; no upload attempted.")
        # A partial Drive search cannot establish that a name is unique.
        if payload.get("incompleteSearch") is True:
            raise SystemExit("Drive preflight incomplete search; no upload attempted.")
        matches.extend(payload.get("files", []))
        if len(matches) > 1:
            raise SystemExit(
                f"Duplicate Drive name {target_name}: {len(matches)} objects; "
                "no automatic reconciliation or deletion."
            )
        next_page_token = payload.get("nextPageToken")
        if next_page_token:
            # Fail closed on broken/cyclic pagination rather than looping forever.
            if (not isinstance(next_page_token, str)
                    or next_page_token in seen_page_tokens
                    or len(seen_page_tokens) >= 50):
                raise SystemExit("Drive preflight pagination loop/limit; no upload attempted.")
            seen_page_tokens.add(next_page_token)
        page_token = next_page_token
        if not page_token:
            break
    if not matches:
        return None
    _verify_metadata(matches[0], path, target_name)
    if not matches[0].get("id"):
        raise SystemExit("Drive preflight returned no file ID.")
    return matches[0]


def main():
    # A lost POST response may mean the server already created the file.
    # Never retry a possibly successful create; preserve both bytes and logs.
    for path in FILES:
        if not path.is_file() or path.stat().st_size == 0:
            raise SystemExit(f"Expected nonempty research artifact missing: {path}")
    session = _oauth_session()
    if PROGRESS.exists():
        progress = json.loads(PROGRESS.read_text("utf-8"))
        if progress.get("schema_version") != 1 or progress.get("folder_id") != FOLDER_ID:
            raise SystemExit("Drive checkpoint incompatible with target folder; refusing unsafe reuse.")
    else:
        progress = {"schema_version": 1, "folder_id": FOLDER_ID, "files": {}}
        _save_progress(progress)
    run_id = os.environ.get("GITHUB_RUN_ID", "local")
    for path in FILES:
        digest = _file_hash(path)
        rel = path.as_posix()
        prior = progress["files"].get(rel, {})
        path_tag = hashlib.sha256(rel.encode("utf-8")).hexdigest()[:7]
        target_name = f"{path.stem}_{run_id}_{path_tag}_{digest[:12]}{path.suffix}"
        if prior.get("sha256") == digest and prior.get("drive_id"):
            prior_name = prior.get("drive_name")
            if not prior_name:
                raise SystemExit(f"Checkpoint lacks Drive name for {rel}; manual review required.")
            # A restored checkpoint can belong to an earlier GitHub run.
            # Never credit an old run's Drive object to the current run.
            if prior_name == target_name:
                existing = _find_existing(session, path, target_name)
                if not existing or existing["id"] != prior["drive_id"]:
                    raise SystemExit(f"Checkpoint/Drive mismatch for {rel}; no reupload.")
                _verify_metadata(_remote_metadata(session, existing["id"]), path, target_name)
                print(f"DRIVE_SKIP_VERIFIED {rel} -> {existing['id']}", flush=True)
                continue
            print(f"DRIVE_STALE_CHECKPOINT {rel}: checking current run target", flush=True)

        existing = _find_existing(session, path, target_name)
        if existing:
            _verify_metadata(_remote_metadata(session, existing["id"]), path, target_name)
            drive_id = existing["id"]
            print(f"DRIVE_REUSE_VERIFIED {rel} -> {drive_id}", flush=True)
        else:
            metadata = {"name": target_name, "parents": [FOLDER_ID]}
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            boundary = "etfrotationboundary"
            body = (
                f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
                + json.dumps(metadata, separators=(",", ":"))
                + f"\r\n--{boundary}\r\nContent-Type: {mime}\r\n\r\n"
            ).encode() + path.read_bytes() + f"\r\n--{boundary}--".encode()
            try:
                response = session.post(
                    "https://www.googleapis.com/upload/drive/v3/files",
                    params={"uploadType": "multipart", "fields": "id,name,webViewLink"},
                    headers={"Content-Type": f"multipart/related; boundary={boundary}"},
                    data=body, timeout=180,
                )
            except requests.exceptions.RequestException as exc:
                raise SystemExit(
                    f"Drive create outcome ambiguous for {rel}; no automatic POST retry."
                ) from exc
            if response.status_code not in (200, 201):
                raise SystemExit(
                    f"Drive create HTTP {response.status_code} for {rel}; "
                    "server outcome may be ambiguous, no automatic POST retry."
                )
            result = response.json()
            drive_id = result.get("id")
            if not drive_id:
                raise SystemExit("Drive upload returned no file ID; no retry.")
            _verify_metadata(_remote_metadata(session, drive_id), path, target_name)
            verified = _find_existing(session, path, target_name)
            if not verified or verified["id"] != drive_id:
                raise SystemExit(
                    "Drive name/ID readback missing or ambiguous; "
                    "preserve remote file and require manual review."
                )
            print(f"DRIVE_CREATED_VERIFIED {rel} -> {drive_id}", flush=True)

        progress["files"][rel] = {
            "sha256": digest,
            "drive_id": drive_id,
            "drive_name": target_name,
            "updated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        _save_progress(progress)
    print(f"DRIVE_UPLOAD_COMPLETE files={len(FILES)} checkpoint={PROGRESS}", flush=True)


if __name__ == "__main__":
    main()
