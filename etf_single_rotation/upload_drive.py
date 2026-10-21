"""Upload ETF input data and all audit/backtest reports to the dedicated Drive folder."""
from __future__ import annotations
import json, os, mimetypes
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
def main():
    raw = os.environ.get("GDRIVE_OAUTH_JSON", "")
    try:
        info = json.loads(raw)
        creds = Credentials(token=None, refresh_token=info["refresh_token"],
            token_uri=info.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=info["client_id"], client_secret=info["client_secret"],
            scopes=["https://www.googleapis.com/auth/drive"])
        creds.refresh(Request())
    except Exception:
        raise SystemExit("Drive OAuth unavailable or invalid; results remain in the GitHub Actions artifact.") from None
    session = AuthorizedSession(creds)
    for path in FILES:
        if not path.is_file():
            raise SystemExit(f"Expected output missing: {path}; refusing to report a complete Drive upload.")
        metadata = {
            "name": f"{path.stem}_{__import__('datetime').datetime.now().strftime('%Y%m%d_%H%M%S')}{path.suffix}",
            "parents": [FOLDER_ID],
        }
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        boundary = "etfrotationboundary"
        meta = json.dumps(metadata, separators=(",", ":"))
        body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n"
                f"--{boundary}\r\nContent-Type: {mime}\r\n\r\n").encode() + path.read_bytes() + f"\r\n--{boundary}--".encode()
        response = session.post("https://www.googleapis.com/upload/drive/v3/files",
            params={"uploadType": "multipart", "fields": "id,name,webViewLink"},
            headers={"Content-Type": f"multipart/related; boundary={boundary}"},
            data=body, timeout=180)
        if response.status_code not in (200, 201):
            raise SystemExit(f"Drive upload failed with HTTP {response.status_code}; do not treat upload as successful.")
        result = response.json()
        print(f"Uploaded {path.name} -> {result.get('name')} ({result.get('id')})")
if __name__ == "__main__":
    main()
