"""One-shot, restartable GitHub ETF research pipeline.

A GitHub Actions run executes ALL predefined stages in order. It does not
periodically rerun the same historical research. Each verified stage stores an
atomic artifact-backed checkpoint; a manually dispatched run may resume from
a matching commit and verified files. Research gate NEVER approves trading.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "etf_single_rotation" / "state" / "run_checkpoint.json"
STATUS = ROOT / "etf_single_rotation" / "results" / "research_pipeline_summary.json"


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def persist(path: Path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".tmp")
    pending.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    pending.replace(path)


@dataclass(frozen=True)
class Stage:
    name: str
    argv: tuple[str, ...]
    outputs: tuple[str, ...]
    max_attempts: int = 1


def build_plan():
    py = sys.executable
    return (
        Stage("data_download", (py, "etf_single_rotation/download_prices.py"),
              ("etf_single_rotation/data/prices.csv",
               "etf_single_rotation/data/trade_calendar.csv"), 2),
        Stage("data_audit", (py, "etf_single_rotation/audit_data.py"),
              ("etf_single_rotation/results/data_quality_summary.json",
               "etf_single_rotation/results/data_quality_gaps.csv")),
        Stage("accounting_tests", (py, "-m", "unittest", "discover", "-s",
              "etf_single_rotation/experiments", "-p", "test_*.py", "-v"), ()),
        Stage("legacy_baseline", (py, "etf_single_rotation/backtest.py", "--baseline-only"),
              ("etf_single_rotation/results/summary.json",)),
        Stage("strategy_exploration", (py, "etf_single_rotation/experiments/run_strategy_experiments.py"),
              ("etf_single_rotation/results/strategy_experiment_manifest.json",
               "etf_single_rotation/results/strategy_daily_weights.csv")),
        Stage("implicit_rebalancing_audit",
              (py, "etf_single_rotation/experiments/audit_execution_model.py"),
              ("etf_single_rotation/results/execution_accounting_summary.json",)),
        Stage("provider_gap_recheck",
              (py, "-m", "etf_single_rotation.experiments.verify_provider_gaps"),
              ("etf_single_rotation/results/provider_gap_recheck_summary.json",), 2),
        Stage("share_cash_self_financing",
              (py, "-m", "etf_single_rotation.experiments.simulate_self_financing"),
              ("etf_single_rotation/results/self_financing/self_financing_manifest.json",
               "etf_single_rotation/results/self_financing/executable_walk_forward_summary.json")),
        Stage("fee_slippage_stress",
              (py, "-m", "etf_single_rotation.experiments.stress_self_financing"),
              ("etf_single_rotation/results/self_financing/cost_stress_manifest.json",
               "etf_single_rotation/results/self_financing/cost_stress_executable_walk_forward.csv")),
        Stage("acceptance_gate", (py, "-m", "etf_single_rotation.research_gate"),
              ("etf_single_rotation/results/research_acceptance_gate.json",)),
        Stage("archive_google_drive", (py, "etf_single_rotation/upload_drive.py"), ()),
    )


def new_state(sha):
    return {
        "schema_version": 1,
        "project": "ETF_SINGLE_ROTATION",
        "source_commit": sha,
        "github_run_id": os.environ.get("GITHUB_RUN_ID", "LOCAL"),
        "github_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", "LOCAL"),
        "started_utc": stamp(),
        "updated_utc": stamp(),
        "pipeline_status": "IN_PROGRESS",
        "stages": {},
        "strategy_acceptance": "NOT_FOR_TRADING",
        "next_action": "RUN_NEXT_PREDECLARED_STAGE",
    }


def stage_complete(record, root: Path, stage: Stage):
    if not record or record.get("status") != "SUCCESS":
        return False
    expected = record.get("output_sha256", {})
    if set(expected) != set(stage.outputs):
        return False
    return all((root / name).is_file() and
               file_digest(root / name) == expected[name] for name in stage.outputs)


def run_plan(stages, root: Path, state_path: Path, source_sha: str, resume=False):
    root, state_path = Path(root).resolve(), Path(state_path).resolve()
    if resume:
        if not state_path.is_file():
            raise ValueError("Resume requested but no checkpoint artifact exists")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if (state.get("project") != "ETF_SINGLE_ROTATION"
                or state.get("schema_version") != 1
                or state.get("source_commit") != source_sha):
            raise ValueError("Resume checkpoint belongs to another project/commit; restart from scratch")
        # Fail rather than silently using corrupt/missing prior-step results.
        for task in stages:
            record = state.get("stages", {}).get(task.name)
            if record and record.get("status") == "SUCCESS":
                if not stage_complete(record, root, task):
                    raise ValueError("Checkpoint output mismatch for " + task.name)
            else:
                break
    else:
        state = new_state(source_sha)
        persist(state_path, state)

    for task in stages:
        old = state["stages"].get(task.name)
        if stage_complete(old, root, task):
            print("PIPELINE_SKIP_VERIFIED " + task.name, flush=True)
            continue
        previous_failures = int(old.get("total_attempts", 0)) if old else 0
        for attempt in range(1, task.max_attempts + 1):
            state["stages"][task.name] = {
                "status": "RUNNING",
                "started_utc": stamp(),
                "total_attempts": previous_failures + attempt,
                "command": list(task.argv),
            }
            state["next_action"] = "EXECUTE_" + task.name.upper()
            state["updated_utc"] = stamp()
            persist(state_path, state)
            print(f"PIPELINE_START {task.name} attempt={attempt}/{task.max_attempts}", flush=True)
            try:
                result = subprocess.run(task.argv, cwd=root, check=False)
                exit_code = result.returncode
            except OSError:
                exit_code = 127
            outputs_ok = all((root / name).is_file() and (root / name).stat().st_size > 0
                             for name in task.outputs)
            if exit_code == 0 and outputs_ok:
                state["stages"][task.name] = {
                    "status": "SUCCESS",
                    "completed_utc": stamp(),
                    "total_attempts": previous_failures + attempt,
                    "output_sha256": {
                        name: file_digest(root / name) for name in task.outputs
                    },
                }
                state["updated_utc"] = stamp()
                state["next_action"] = "RUN_NEXT_PREDECLARED_STAGE"
                persist(state_path, state)
                print("PIPELINE_OK " + task.name, flush=True)
                break
            state["stages"][task.name] = {
                "status": "FAILED",
                "finished_utc": stamp(),
                "total_attempts": previous_failures + attempt,
                "exit_code": exit_code,
                "outputs_present": outputs_ok,
            }
            state["pipeline_status"] = "INTERRUPTED_REVIEW_OR_RESUME"
            state["next_action"] = "RETRY_" + task.name.upper()
            state["updated_utc"] = stamp()
            persist(state_path, state)
            print(f"PIPELINE_FAILED {task.name} exit={exit_code}, outputs_ok={outputs_ok}", flush=True)
            if attempt < task.max_attempts:
                time.sleep(min(10 * attempt, 20))
        else:
            raise RuntimeError(
                f"ETF task {task.name} failed after {task.max_attempts} attempt(s); "
                "immutable evidence stored in GitHub Actions artifact. No fabricated success."
            )
        state["pipeline_status"] = "IN_PROGRESS"

    state["pipeline_status"] = "RESEARCH_RUN_COMPLETED_REVIEW_REQUIRED"
    state["strategy_acceptance"] = "NOT_FOR_TRADING"
    state["next_action"] = "GPT_08AM_REVIEW_UNRESOLVED_RESEARCH_BLOCKERS"
    state["updated_utc"] = stamp()
    persist(state_path, state)
    return state


def main():
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    resume = os.environ.get("ETF_RESUME_RUN_ID", "").strip() != ""
    state = None
    try:
        state = run_plan(build_plan(), ROOT, STATE, sha, resume=resume)
    finally:
        if STATE.is_file():
            current = json.loads(STATE.read_text(encoding="utf-8"))
            persist(STATUS, {
                "project": current.get("project"),
                "source_commit": current.get("source_commit"),
                "pipeline_status": current.get("pipeline_status"),
                "strategy_acceptance": "NOT_FOR_TRADING",
                "github_run_id": current.get("github_run_id"),
                "completed_stages": [
                    k for k, v in current.get("stages", {}).items()
                    if v.get("status") == "SUCCESS"
                ],
                "next_action": current.get("next_action"),
                "last_updated_utc": current.get("updated_utc"),
                "checkpoint_path": str(STATE.relative_to(ROOT)),
            })
            print("ETF_PIPELINE_SUMMARY " + STATUS.read_text("utf-8"), flush=True)
    if state:
        print("ETF_PIPELINE_STATUS " + state["pipeline_status"], flush=True)


if __name__ == "__main__":
    main()
