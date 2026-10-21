"""Upload ETF input data and all audit/backtest reports to the dedicated Drive folder."""
from __future__ import annotations
import json, os, mimetypes, hashlib, time
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
    Path("etf_single_rotation/results/strategy_equity_curves.csv"),
    Path("etf_single_rotation/results/strategy_monthly_signals.csv"),
    Path("etf_single_rotation/results/strategy_daily_weights.csv"),
    Path("etf_single_rotation/results/strategy_experiment_manifest.json"),
]

PROGRESS = Path("etf_single_rotation/state/drive_upload_checkpoint.json")
MAX_UPLOAD_ATTEMPTS = 4
TRANSIENT_STATUSES = {429, 500, 502, 503, 504}


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


def main():
    # Validate existence first: never partially publish if a required report
    # is already missing on disk.
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
        if prior.get("sha256") == digest and prior.get("drive_id"):
            print(f"DRIVE_SKIP_VERIFIED {rel} -> {prior['drive_id']}", flush=True)
            continue
        path_tag = hashlib.sha256(rel.encode("utf-8")).hexdigest()[:7]
        target_name = f"{path.stem}_{run_id}_{path_tag}_{digest[:12]}{path.suffix}"
        metadata = {"name": target_name, "parents": [FOLDER_ID]}
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        boundary = "etfrotationboundary"
        body = (
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
            + json.dumps(metadata, separators=(",", ":"))
            + f"\r\n--{boundary}\r\nContent-Type: {mime}\r\n\r\n"
        ).encode() + path.read_bytes() + f"\r\n--{boundary}--".encode()
        for attempt in range(1, MAX_UPLOAD_ATTEMPTS + 1):
            try:
                response = session.post(
                    "https://www.googleapis.com/upload/drive/v3/files",
                    params={"uploadType": "multipart", "fields": "id,name,webViewLink"},
                    headers={"Content-Type": f"multipart/related; boundary={boundary}"},
                    data=body, timeout=180
                )
            except requests.exceptions.RequestException:
                code = "NETWORK_ERROR"
                retry = True
            else:
                code = response.status_code
                retry = code in TRANSIENT_STATUSES
                if code in (200, 201):
                    result = response.json()
                    drive_id = result.get("id")
                    if not drive_id:
                        raise SystemExit("Drive upload returned no file id; cannot verify save.")
                    progress["files"][rel] = {
                        "sha256": digest,
                        "drive_id": drive_id,
                        "drive_name": result.get("name"),
                        "updated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    }
                    _save_progress(progress)
                    print(f"Uploaded {path.name} -> {result.get('name')} ({drive_id})", flush=True)
                    break
            if retry and attempt < MAX_UPLOAD_ATTEMPTS:
                print(f"DRIVE_UPLOAD_RETRY {rel} status={code} attempt={attempt}", flush=True)
                time.sleep(min(2 ** attempt, 12))
                continue
            raise SystemExit(
                f"Drive upload failed for {rel} after attempt {attempt}; HTTP/status {code}. "
                "Partial result remains in Drive, progress preserved in checkpoint."
            )
    print(f"DRIVE_UPLOAD_COMPLETE files={len(FILES)} checkpoint={PROGRESS}", flush=True)


if __name__ == "__main__":
    main()
