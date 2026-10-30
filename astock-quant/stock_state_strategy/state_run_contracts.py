"""Exact-date publication contracts, independent of market/parquet libraries."""
from datetime import date
import json
from pathlib import Path


def require_exact_date(requested: str, observed: str) -> None:
    try:
        expected_day = date.fromisoformat(requested)
        actual_day = date.fromisoformat(observed)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Invalid requested/actual state date") from exc
    if expected_day != actual_day:
        raise RuntimeError(f"State date mismatch: requested {requested}, actual {observed}; refuse publication")


def validate_state_output(directory: Path, requested: str) -> str:
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    observed = summary.get("as_of_date")
    require_exact_date(requested, observed)
    return observed


def phase_status_path(run_id: str, task: str) -> str:
    allowed = {"preflight", "tests", "current-state", "forward-baseline",
               "ownership-refresh", "gold-refresh", "backup-repo-data"}
    if task not in allowed:
        raise ValueError("Unsupported task for phase status")
    return f"stock_state_strategy/runs/{run_id}/{task}/run_status.json"
