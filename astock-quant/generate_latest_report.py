"""Run the daily H20 report refresh workflow.

Default entry:
    python generate_latest_report.py

The workflow updates daily prices, recomputes financial scores without touching
the legacy report.md, refreshes LightGBM daily predictions, refreshes Kronos H20
only when stale, then writes an archived report under financial_data/reports/.
The daily price step inherits update_daily.py's intraday guard unless
--skip-daily or --force-daily is used.
"""

from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
REPORT_DIR = BASE_DIR / "financial_data" / "reports"
KRONOS_H20_CSV = (
    BASE_DIR / "kronos_return_top3" / "output" / "mini_full_h20_lb512_predictions_latest.csv"
)

DAILY_COMMANDS: list[tuple[str, list[str]]] = [
    ("daily prices", [sys.executable, "update_daily.py"]),
    (
        "financial scores",
        [sys.executable, "-m", "financial_rating", "--score-only", "--skip-report"],
    ),
    ("LightGBM predictions", [sys.executable, "-m", "lightgbm_return_top3.daily_report"]),
]

KRONOS_COMMAND = [
    sys.executable,
    "-m",
    "kronos_return_top3",
    "--predict-only",
    "--use-mini",
    "--hold-days",
    "20",
    "--full-universe",
    "--batch-size",
    "32",
    "--output-prefix",
    "mini_full_h20_lb512",
    "--quiet",
]

REPORT_COMMAND = [sys.executable, "generate_quality_h20_report.py"]


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate the latest archived H20 report.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the workflow without running commands that write data.",
    )
    parser.add_argument(
        "--kronos-max-age-days",
        type=int,
        default=14,
        help="Refresh Kronos H20 when signal_date is older than this many calendar days.",
    )
    parser.add_argument(
        "--force-kronos",
        action="store_true",
        help="Always refresh Kronos H20 predictions.",
    )
    parser.add_argument(
        "--skip-kronos",
        action="store_true",
        help="Never refresh Kronos H20 predictions in this run.",
    )
    parser.add_argument("--skip-daily", action="store_true", help="Skip update_daily.py.")
    parser.add_argument(
        "--force-daily",
        action="store_true",
        help="Pass --force to update_daily.py; use only for confirmed historical backfill.",
    )
    parser.add_argument(
        "--skip-score",
        action="store_true",
        help="Skip financial_rating --score-only --skip-report.",
    )
    parser.add_argument(
        "--skip-lightgbm",
        action="store_true",
        help="Skip lightgbm_return_top3.daily_report.",
    )
    return parser


def command_text(command: list[str]) -> str:
    return " ".join(command)


def run_command(label: str, command: list[str], dry_run: bool) -> None:
    print(f"\n==> {label}")
    print(command_text(command))
    if dry_run:
        return

    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    subprocess.run(command, cwd=BASE_DIR, check=True, env=env)


def build_daily_commands(args: argparse.Namespace) -> list[tuple[str, list[str]]]:
    commands: list[tuple[str, list[str]]] = []
    for label, command in DAILY_COMMANDS:
        command_copy = list(command)
        if label == "daily prices" and args.force_daily:
            command_copy.append("--force")
        commands.append((label, command_copy))
    return commands


def read_kronos_signal_date(path: Path) -> date | None:
    if not path.exists():
        return None

    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                value = (row.get("signal_date") or "").strip()
                if value:
                    return date.fromisoformat(value[:10])
                return None
    except (OSError, ValueError, csv.Error):
        return None
    return None


def kronos_should_refresh(args: argparse.Namespace) -> tuple[bool, str]:
    if args.skip_kronos:
        return False, "skipped by --skip-kronos"
    if args.force_kronos:
        return True, "forced by --force-kronos"

    signal_date = read_kronos_signal_date(KRONOS_H20_CSV)
    if signal_date is None:
        return True, f"missing or unreadable {KRONOS_H20_CSV.relative_to(BASE_DIR).as_posix()}"

    age_days = (date.today() - signal_date).days
    if age_days > args.kronos_max_age_days:
        return True, f"signal_date {signal_date.isoformat()} is {age_days} days old"
    return False, f"signal_date {signal_date.isoformat()} is {age_days} days old"


def newest_report() -> Path | None:
    if not REPORT_DIR.exists():
        return None
    reports = list(REPORT_DIR.glob("report_*.md"))
    if not reports:
        return None
    return max(reports, key=lambda path: path.stat().st_mtime)


def main() -> None:
    args = build_arg_parser().parse_args()
    now = datetime.now()

    print(f"[latest-report] started_at={now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"[latest-report] repo={BASE_DIR}")

    planned_commands = [
        (label, command)
        for label, command in build_daily_commands(args)
        if not (
            (label == "daily prices" and args.skip_daily)
            or (label == "financial scores" and args.skip_score)
            or (label == "LightGBM predictions" and args.skip_lightgbm)
        )
    ]

    for label, command in planned_commands:
        run_command(label, command, args.dry_run)

    refresh_kronos, kronos_reason = kronos_should_refresh(args)
    print(f"\n==> Kronos H20 freshness")
    print(kronos_reason)
    if refresh_kronos:
        run_command("Kronos H20 predictions", KRONOS_COMMAND, args.dry_run)
    else:
        print("Kronos H20 refresh skipped.")

    before_reports = set(REPORT_DIR.glob("report_*.md")) if REPORT_DIR.exists() else set()
    run_command("archived H20 report", REPORT_COMMAND, args.dry_run)

    if args.dry_run:
        print("\n[latest-report] dry-run complete; no data or reports were written.")
        return

    after_reports = set(REPORT_DIR.glob("report_*.md")) if REPORT_DIR.exists() else set()
    new_reports = sorted(after_reports - before_reports, key=lambda path: path.stat().st_mtime)
    report = new_reports[-1] if new_reports else newest_report()
    if report is None:
        raise SystemExit("Report command finished, but no archived report was found.")

    rel_report = report.relative_to(BASE_DIR).as_posix()
    print(f"\n[latest-report] latest archived report: {rel_report}")
    print(f"LATEST_REPORT={rel_report}")


if __name__ == "__main__":
    main()
