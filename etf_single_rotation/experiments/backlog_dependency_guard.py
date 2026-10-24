"""Fail-closed, stdlib-only dependency and mandate gate for ETF research backlog.

This is a review guard, not a trading engine or a task dispatcher.
Historical research completion is NOT a VERIFIED_DONE dependency.
"""
from __future__ import annotations

import json
from pathlib import Path

ACTIVE_STATES = frozenset({"READY", "CLAIMED", "RUNNING", "VERIFYING", "VERIFIED_DONE"})
MANDATE = {
    "symbol_count": 8,
    "max_simultaneous_etf_positions": 1,
    "may_hold_cash": True,
    "historical_max_drawdown_target": 0.25,
    "real_trading": False,
    "github_hourly_cron": False,
}


def check_backlog(backlog: dict) -> list[str]:
    """Return human-readable safety violations without changing any task state."""
    errors: list[str] = []
    mandate = backlog.get("mandate", {})
    for key, expected in MANDATE.items():
        if type(mandate.get(key)) is not type(expected) or mandate.get(key) != expected:
            errors.append(f"Mandate {key} must remain {expected!r}")
    if backlog.get("last_known_research_status") != "NOT_FOR_TRADING":
        errors.append("Trading gate must remain NOT_FOR_TRADING")
    tasks = backlog.get("tasks", [])
    if not isinstance(tasks, list):
        return errors + ["Tasks must be a list"]
    indexed = {}
    for task in tasks:
        if not isinstance(task, dict) or not isinstance(task.get("id"), str):
            errors.append("Every task needs a string id")
            continue
        if task["id"] in indexed:
            errors.append(f"Duplicate task id: {task['id']}")
        indexed[task["id"]] = task
    for task in tasks:
        if not isinstance(task, dict) or not isinstance(task.get("id"), str):
            continue
        dependencies = task.get("depends_on", [])
        if not isinstance(dependencies, list):
            errors.append(f"{task['id']} dependencies must be a list")
            continue
        for dependency in dependencies:
            if dependency not in indexed:
                errors.append(f"{task['id']} references missing dependency {dependency}")
            elif task.get("status") in ACTIVE_STATES and indexed[dependency].get("status") != "VERIFIED_DONE":
                errors.append(
                    f"{task['id']} cannot be {task['status']}: "
                    f"{dependency} is {indexed[dependency].get('status')}, not VERIFIED_DONE"
                )
    return errors


def main() -> None:
    path = Path(__file__).resolve().parents[1] / "research_backlog.json"
    issues = check_backlog(json.loads(path.read_text(encoding="utf-8")))
    if issues:
        raise SystemExit("ETF_BACKLOG_GATE_BLOCKED\n" + "\n".join(issues))
    print("ETF_BACKLOG_GATE_OK NOT_FOR_TRADING; dependency checks fail closed")


if __name__ == "__main__":
    main()
