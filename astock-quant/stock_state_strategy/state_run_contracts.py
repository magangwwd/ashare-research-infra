"""Exact-date publication contracts, independent of market/parquet libraries."""
from datetime import date
import json
from pathlib import Path
from datetime import datetime, time
from zoneinfo import ZoneInfo


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


def require_current_closed_session(requested: str, calendar: list, observed_at: datetime) -> None:
    """Reject old-day forward creation, including runs after midnight in China."""
    if observed_at.tzinfo is None:
        raise RuntimeError("First observation must have a timezone")
    local = observed_at.astimezone(ZoneInfo("Asia/Shanghai"))
    today = local.date()
    days = [(date.fromisoformat(str(day)[:10]), bool(open_day)) for day, open_day in calendar]
    if not days or min(d for d, _ in days) > today or max(d for d, _ in days) < today:
        raise RuntimeError("Trading calendar does not cover observation day")
    closed = [d for d, is_open in days if is_open and
              (d < today or (d == today and local.time() >= time(15, 5)))]
    if not closed:
        raise RuntimeError("No confirmed closed session")
    require_exact_date(max(closed).isoformat(), requested)
    # A prior-close signal created after the next auction starts has already
    # observed part of its execution session and cannot be forward evidence.
    if (max(closed) < today and any(d == today and is_open for d, is_open in days)
            and local.time() >= time(9, 15)):
        raise RuntimeError("Forward first-observation window closed at next-session auction")
