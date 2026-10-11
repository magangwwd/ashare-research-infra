"""CI fail-closed gate: Git is for code; generated data must live on Drive."""
from __future__ import annotations
import subprocess
import sys
from pathlib import PurePosixPath

GENERATED_DIRS = (
    "daily_data/", "down_data/data/", "financial_data/reports/",
    "financial_data_source_integration/results/", "kronos_return_top3/output/",
    "lightgbm_return_top3/output/", "combined_top3/output/",
    "roe_fcf_long_term_investing/output/", "roe_fcf_long_term_investing/cache/",
    "stock_state_strategy/output/", "stock_state_strategy/runtime/",
    "stock_state_strategy/artifacts/", "stock_state_strategy/archive/",
)
LOCAL_DIRS = (".obsidian/", ".vscode/")
SUFFIXES = {".csv", ".parquet", ".feather", ".duckdb", ".sqlite",
            ".pkl", ".joblib", ".npz", ".safetensors", ".pt", ".ckpt", ".pyc", ".bak"}
MANAGED_OUTPUTS = ("financial_data/report.md",)
DATA_PREFIXES = ("financial_data/", "market_meta/", "event_data/",
                 "kronos_return_top3/finetune_data/", "research_notes/")


def violates(path: str) -> bool:
    p = path.replace("\\", "/")
    leaf = PurePosixPath(p).name
    if p.startswith(GENERATED_DIRS + LOCAL_DIRS):
        return True
    if leaf.startswith(".env") and leaf != ".env.example":
        return True
    if p in MANAGED_OUTPUTS or p in ("filtered_stocks.csv", "industry_map.csv"):
        return True
    if p in (".claude/settings.local.json", ".claude/scheduled_tasks.lock"):
        return True
    if PurePosixPath(p).suffix.lower() in SUFFIXES:
        return True
    if p.startswith("kronos_return_top3/finetune_data/") and leaf == "dataset_summary.json":
        return True
    return False


def main() -> int:
    raw = subprocess.check_output(["git", "ls-files", "-z"])
    paths = [p.decode("utf-8") for p in raw.split(b"\0") if p]
    offenders = [p for p in paths if violates(p)]
    if offenders:
        print("ERROR: generated / private data are tracked in Git:", file=sys.stderr)
        for p in offenders[:50]:
            print("  " + p, file=sys.stderr)
        print(f"Total violations: {len(offenders)}", file=sys.stderr)
        return 1
    print(f"PASS: {len(paths)} tracked files contain no generated market datasets / outputs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
