"""Run selected legacy research jobs with *ephemeral* data and Google Drive outputs.

Never commit data to Git. GitHub Actions permissions must remain contents:read.
Remote `gdrive:` must be rooted at the Drive project directory.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REMOTE = "gdrive:"
DEFAULT_ARCHIVE = "astock_quant_repo_backup"
TASKS = {
    "market-features": [sys.executable, "-m", "market_meta.build_features"],
    "event-features": [sys.executable, "-m", "event_data.build_features"],
    "financial-score": [sys.executable, "-m", "financial_rating", "--score-only", "--skip-report"],
    "lightgbm-daily": [sys.executable, "-m", "lightgbm_return_top3.daily_report"],
    "lightgbm-backtest": [sys.executable, "-m", "lightgbm_return_top3.model_backtest", "--test"],
    "kronos-predict": [sys.executable, "-m", "kronos_return_top3",
                       "--predict-only", "--use-mini", "--hold-days", "20",
                       "--full-universe", "--batch-size", "16",
                       "--output-prefix", "mini_full_h20_lb512", "--quiet"],
    "combined-top3": [sys.executable, "-m", "combined_top3", "--test"],
    "roe-fcf": [sys.executable, "roe_fcf_long_term_investing/backtest_roe_fcf.py"],
    "daily-report": [sys.executable, "generate_latest_report.py"],
}
# These are the only data paths read/written by the legacy task wrapper.
# All are copied FROM Drive at job start; after a successful run, result
# directories are copied back. No git commit, action artifact, or rclone sync.
INPUT_DIRS = (
    "daily_data", "market_meta", "event_data", "financial_data",
    "lightgbm_return_top3/output", "kronos_return_top3/output",
    "combined_top3/output", "roe_fcf_long_term_investing/output",
    "roe_fcf_long_term_investing/cache", "kronos_return_top3/finetune_data",
)
INPUT_FILES = ("filtered_stocks.csv", "industry_map.csv")
WRITE_DIRS = (
    "market_meta", "event_data", "financial_data",
    "lightgbm_return_top3/output", "kronos_return_top3/output",
    "combined_top3/output", "roe_fcf_long_term_investing/output",
    "roe_fcf_long_term_investing/cache", "kronos_return_top3/finetune_data",
)
EXTRA_OUTPUT = ("research_notes", "financial_data_source_integration/results")


def execute(*args: str, cwd: Path = REPO, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=capture, text=capture)


def safe_prefix(prefix: str) -> str:
    parts = Path(prefix).parts
    if not prefix or prefix.startswith(("/", "\\")) or ".." in parts or ":" in prefix:
        raise ValueError("Invalid Drive prefix. Specify a project-relative folder.")
    return prefix.strip("/")


def exists_remote(path: str) -> bool:
    result = subprocess.run(["rclone", "lsf", path, "--max-depth", "1"],
                            cwd=REPO, capture_output=True, text=True)
    return result.returncode == 0


def fetch(remote: str, relative: str, required: bool = False) -> None:
    dst = REPO / relative
    source = remote + relative
    if not exists_remote(source):
        if required:
            raise RuntimeError(f"Required Google Drive input missing: {source}")
        return
    if relative.endswith(".csv"):
        dst.parent.mkdir(parents=True, exist_ok=True)
        execute("rclone", "copyto", source, str(dst))
    else:
        dst.mkdir(parents=True, exist_ok=True)
        # CSV, JSON, and reports only; don't mix backed-up Python with checked-out source.
        execute("rclone", "copy", source, str(dst), "--include", "*.csv",
                "--include", "*.csv.bak", "--include", "*.json",
                "--include", "*.md", "--include", "*.pkl", "--include", "*.parquet")


def publish(remote: str, relative: str, include_reports: bool = True) -> None:
    folder = REPO / relative
    if not folder.is_dir():
        return
    patterns = ["*.csv", "*.csv.bak", "*.json", "*.parquet", "*.pkl", "*.joblib", "*.npz"]
    if include_reports:
        patterns.append("*.md")
    args = ["rclone", "copy", str(folder), remote + relative]
    for pattern in patterns:
        args += ["--include", pattern]
    execute(*args, "--transfers", "4", "--checkers", "8")
    check_args = ["rclone", "check", str(folder), remote + relative, "--one-way"]
    for pattern in patterns:
        check_args += ["--include", pattern]
    execute(*check_args)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", choices=sorted(TASKS))
    parser.add_argument("--drive-prefix", default=DEFAULT_ARCHIVE)
    args = parser.parse_args()
    if not os.environ.get("RCLONE_CONFIG"):
        raise RuntimeError("Missing RCLONE_CONFIG. Configure GitHub Actions Drive Secret.")
    prefix = safe_prefix(args.drive_prefix)
    remote = REMOTE + prefix + "/"
    execute("rclone", "listremotes")
    # A mandatory data file prevents a missing or wrong remote from causing
    # an empty-input computation (especially important after Git CSV cleanup).
    for name in INPUT_FILES:
        fetch(remote, name, required=True)
    fetch(remote, "daily_data", required=True)
    for name in INPUT_DIRS:
        if name != "daily_data":
            fetch(remote, name)
    result = {"job": args.job, "status": "FAILED", "started_at": datetime.now(timezone.utc).isoformat(),
              "github_sha": os.environ.get("GITHUB_SHA"), "drive_prefix": prefix}
    try:
        execute(*TASKS[args.job])
        result["status"] = "SUCCESS"
        return 0
    finally:
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        # Failures are reported, but data are only published when processing succeeded.
        if result["status"] == "SUCCESS":
            for name in WRITE_DIRS:
                publish(remote, name, include_reports=name != "kronos_return_top3/finetune_data")
            for name in EXTRA_OUTPUT:
                publish(remote, name)
            # Older legacy daily updater produces data in daily_data.
            if args.job == "daily-report":
                publish(remote, "daily_data", include_reports=False)
        else:
            print("Job did not pass; no new data published to Drive.", file=sys.stderr)
        if result["status"] == "SUCCESS":
            result["status"] = "PUBLISHED"
        report = REPO / ".drive-run-result.json"
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            execute("rclone", "copyto", str(report),
                    REMOTE + "stock_state_strategy/runs/legacy-" +
                    os.environ.get("GITHUB_RUN_ID", "manual") + "/status.json")
        finally:
            report.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
