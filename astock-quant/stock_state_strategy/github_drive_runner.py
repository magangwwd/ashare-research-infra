"""Run stock_state_strategy with ephemeral GitHub compute and durable Google Drive.

The configured rclone remote `gdrive:` MUST have root_folder_id set to the
existing Google Drive project folder, not the repository or My Drive root.
No writes to GitHub and no rclone sync/delete operations are permitted.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from state_run_contracts import phase_status_path, validate_state_output
from current_ownership_contract import current_config, verified_ownership_prefix

REPO = Path(__file__).resolve().parents[1]
CODE = Path(__file__).resolve().parent
REMOTE = "gdrive:"
TASKS = ("preflight", "tests", "current-state", "forward-baseline",
         "ownership-refresh", "gold-refresh", "backup-repo-data")
DATA_BACKUP = (
    "daily_data", "market_meta", "financial_data", "event_data",
    "lightgbm_return_top3/output", "kronos_return_top3/output",
    "roe_fcf_long_term_investing/output", "combined_top3/output",
)


def run(*argv: str, cwd: Path | None = None) -> None:
    print("RUN", " ".join(argv), flush=True)
    subprocess.run(list(argv), cwd=cwd, check=True)


def copy(src: str | Path, dest: str | Path) -> None:
    run("rclone", "copy", str(src), str(dest), "--transfers", "6",
        "--checkers", "12", "--retries", "3")


def check(src: str | Path, dest: str | Path) -> None:
    run("rclone", "check", str(src), str(dest), "--one-way", "--checkers", "12")


def require_drive() -> None:
    if not os.getenv("RCLONE_CONFIG"):
        raise RuntimeError("Missing RCLONE_CONFIG; Drive secret is not configured.")
    for folder, marker in (("stock_state_strategy", "LOCK_TO_MARKUP_PAPER_STRATEGY.md"),
                           ("down_data", "DATABASE_STATUS.md")):
        result = subprocess.run(
            ["rclone", "lsf", REMOTE + folder, "--files-only", "--max-depth", "1"],
            capture_output=True, text=True, check=True,
        )
        if marker not in result.stdout.splitlines():
            raise RuntimeError("Incorrect Drive remote root: missing " + folder + "/" + marker)


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def link_persistent(name: str, work: Path) -> None:
    target = work / "stock_state_strategy" / name
    target.mkdir(parents=True, exist_ok=True)
    path = CODE / name
    if path.is_symlink():
        raise RuntimeError(f"Unexpected existing symlink: {path}")
    if path.exists():
        raise RuntimeError(f"Tracked or local directory blocks transient link: {path}")
    path.symlink_to(target, target_is_directory=True)


def validate_gold(base: Path, minimum: str = "", full: bool = False) -> str:
    gold = load(base / "gold" / "main_board" / "manifest.json")
    as_of = str(gold["as_of_date"])[:10]
    date.fromisoformat(as_of)
    if minimum and as_of < minimum:
        raise RuntimeError(f"Gold is stale: {as_of} < {minimum}")
    if gold.get("validation", {}).get("primary_keys_unique") is not True:
        raise RuntimeError("Gold manifest has not passed primary key validation")
    if gold.get("validation", {}).get("ohlc_valid") is not True:
        raise RuntimeError("Gold manifest has not passed OHLC validation")
    if full:
        history = load(base / "manifests" / "tushare" / "main_board_history" / "last_run_summary.json")
        states = load(base / "manifests" / "tushare" / "main_board_states" / "last_run_summary.json")
        if history.get("status") != "COMPLETE" or history.get("partial_months"):
            raise RuntimeError("Historical update is not COMPLETE")
        if states.get("status") != "VALIDATED":
            raise RuntimeError("State update is not VALIDATED")
        if str(history.get("ended_range", "")).replace("-", "") < as_of.replace("-", ""):
            raise RuntimeError("History coverage is older than Gold snapshot")
        if str(states.get("end", ""))[:10] < as_of:
            raise RuntimeError("State coverage is older than Gold snapshot")
    return as_of


def publish_data(data: Path) -> None:
    # Commit marker manifests are copied last; failure must not advertise
    # a newer complete snapshot before underlying partitions arrive.
    run("rclone", "copy", str(data), REMOTE + "down_data/data",
        "--exclude", "/gold/main_board/manifest.json",
        "--exclude", "/manifests/tushare/main_board_history/last_run_summary.json",
        "--exclude", "/manifests/tushare/main_board_states/last_run_summary.json",
        "--transfers", "6", "--checkers", "12", "--retries", "3")
    for relative in ("gold/main_board/manifest.json",
                     "manifests/tushare/main_board_history/last_run_summary.json",
                     "manifests/tushare/main_board_states/last_run_summary.json"):
        path = data / relative
        if not path.is_file():
            raise RuntimeError("Missing publication marker: " + relative)
        run("rclone", "copyto", str(path), REMOTE + "down_data/data/" + relative)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("task", choices=TASKS)
    p.add_argument("--as-of", default="auto", help="ISO trading day or auto=Gold manifest")
    p.add_argument("--min-as-of", default="", help="Require Gold data no older than YYYY-MM-DD")
    p.add_argument("--promote-ownership", action="store_true",
                   help="Publish validated ownership/state pointer after isolated byte verification")
    args = p.parse_args()
    if args.promote_ownership and args.task != "ownership-refresh":
        p.error("--promote-ownership requires ownership-refresh")
    if args.min_as_of:
        date.fromisoformat(args.min_as_of)
    if args.as_of != "auto":
        date.fromisoformat(args.as_of)

    if args.task == "tests":
        run(sys.executable, "run_pipeline.py", "tests", cwd=CODE)
        return 0

    require_drive()
    scratch = Path(tempfile.mkdtemp(prefix="stock-state-", dir=os.environ.get("RUNNER_TEMP")))
    work = scratch / "workspace"
    data = work / "down_data" / "data"
    gold_path = data / "gold" / "main_board"
    status = {"task": args.task, "status": "FAILED", "github_sha": os.getenv("GITHUB_SHA"),
              "started_at": datetime.now(timezone.utc).isoformat(), "as_of": None}
    run_id = os.getenv("GITHUB_RUN_ID", "manual") + "-" + os.getenv("GITHUB_RUN_ATTEMPT", "1")
    try:
        if args.task == "backup-repo-data":
            for relative in DATA_BACKUP:
                origin = REPO / relative
                if origin.is_dir():
                    dest = REMOTE + "astock_quant_repo_backup/" + relative
                    copy(origin, dest)
                    check(origin, dest)
            status["status"] = "BACKUP_VERIFIED_NOT_DELETED"
            return 0

        # All computation reads Drive into disposable GitHub-runner storage.
        if args.task == "gold-refresh":
            copy(REMOTE + "down_data/data", data)
            for script in ("download_a_share_main_board.py", "build_main_board_canonical.py",
                           "backfill_main_board_history.py", "backfill_main_board_states.py"):
                run("rclone", "copyto", REMOTE + "down_data/" + script,
                    str(work / "down_data" / script))
            previous = validate_gold(data)
            # Keep the prior complete stock list before rebuilding the current one.
            # IPOs enter the new list; delisted symbols must stay for historical reads.
            previous_instruments = scratch / "previous_instruments.parquet"
            shutil.copy2(gold_path / "instruments.parquet", previous_instruments)
            if not os.getenv("TUSHARE_TOKEN"):
                raise RuntimeError("TUSHARE_TOKEN secret missing; refusing Gold update")
            down = work / "down_data"
            for script in ("download_a_share_main_board.py", "build_main_board_canonical.py",
                           "backfill_main_board_history.py", "backfill_main_board_states.py"):
                run(sys.executable, script, cwd=down)
                if script == "build_main_board_canonical.py":
                    run(sys.executable, str(CODE / "universe_audit.py"),
                        "--previous", str(previous_instruments),
                        "--current", str(gold_path / "instruments.parquet"),
                        "--source", str(data / "gold" / "a_share_main_board.parquet"),
                        "--output", str(gold_path / "universe_changes.json"),
                        cwd=down)
            as_of = validate_gold(data, previous, full=True)
            if args.min_as_of and as_of < args.min_as_of:
                raise RuntimeError(f"Refusing to publish stale refresh: {as_of}")
            publish_data(data)
            status["as_of"] = as_of
            status["status"] = "PUBLISHED_VALIDATED"
            return 0

        copy(REMOTE + "down_data/data/gold/main_board", gold_path)
        as_of = validate_gold(data, args.min_as_of)
        status["as_of"] = as_of
        if args.task == "preflight":
            os.environ["ASTOCK_MARKET_DATA_ROOT"] = str(gold_path)
            run(sys.executable, "data_source_preflight.py",
                "--config", "configs/ownership_state.json", cwd=CODE)
            status["status"] = "PASSED"
            return 0

        for dirname in ("output", "runtime", "artifacts"):
            link_persistent(dirname, work)
        os.environ["ASTOCK_MARKET_DATA_ROOT"] = str(gold_path)
        if args.task == "current-state":
            chosen = as_of if args.as_of == "auto" else args.as_of
            if chosen > as_of:
                raise RuntimeError(f"Requested state day {chosen} is newer than Gold {as_of}")
            pointer_file = scratch / "ownership_current.json"
            run("rclone", "copyto", REMOTE + "stock_state_strategy/current_ownership.json", str(pointer_file))
            prefix = verified_ownership_prefix(load(pointer_file), chosen)
            source = work / "current_ownership"
            copy(REMOTE + prefix + "/processed", source / "processed")
            config = current_config(load(CODE / "configs/ownership_rebuild.json"), gold_path, source, chosen)
            config_path = scratch / "state_current_config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            run(sys.executable, "run_pipeline.py", "current-state", "--as-of", chosen,
                "--config", str(config_path), cwd=CODE)
            local = work / "stock_state_strategy/output" / ("state_definition_" + chosen.replace("-", ""))
            if not local.is_dir() or not any(local.rglob("*")):
                raise RuntimeError("No state output files created")
            # Validate generated evidence before any output publication.
            status["requested_as_of"] = chosen
            status["actual_as_of"] = validate_state_output(local, chosen)
            copy(local, REMOTE + "stock_state_strategy/output/" + local.name)
            check(local, REMOTE + "stock_state_strategy/output/" + local.name)
        elif args.task == "forward-baseline":
            copy(REMOTE + "stock_state_strategy/artifacts/legacy",
                 work / "stock_state_strategy/artifacts/legacy")
            run(sys.executable, "run_pipeline.py", "forward-20260818", cwd=CODE)
            local = work / "stock_state_strategy/output/forward_20260818"
            copy(local, REMOTE + "stock_state_strategy/output/forward_20260818")
            check(local, REMOTE + "stock_state_strategy/output/forward_20260818")
        elif args.task == "ownership-refresh":
            if not os.getenv("TUSHARE_TOKEN"):
                raise RuntimeError("TUSHARE_TOKEN secret missing")
            # Refresh and consume the same isolated version, never legacy/default outputs.
            snapshot = work / "ownership_snapshots" / as_of / run_id
            snapshot.mkdir(parents=True, exist_ok=False)
            config = current_config(load(CODE / "configs/ownership_rebuild.json"),
                                    gold_path, snapshot, as_of)
            config_path = snapshot / "current_config.json"
            config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
            run(sys.executable, "download_ownership.py",
                "--config", str(config_path), cwd=CODE)
            run(sys.executable, "ownership_pipeline.py",
                "--config", str(config_path), "--skip-labels", cwd=CODE)
            run(sys.executable, "run_pipeline.py", "current-state", "--as-of", as_of,
                "--config", str(config_path), cwd=CODE)
            state = work / "stock_state_strategy/output" / ("state_definition_" + as_of.replace("-", ""))
            validate_state_output(state, as_of)
            shutil.copytree(state, snapshot / "state")
            target = REMOTE + "stock_state_strategy/research/ownership/" + as_of + "/" + run_id
            copy(snapshot, target)
            check(snapshot, target)
            status["ownership_snapshot"] = target
            status["publication_scope"] = "ISOLATED_RESEARCH_NOT_PRODUCTION_PROMOTION"
            if args.promote_ownership:
                formal = REMOTE + "stock_state_strategy/output/" + state.name
                copy(state, formal)
                check(state, formal)
                pointer = {"as_of_date": as_of, "snapshot_prefix": target.removeprefix(REMOTE),
                           "verification": "RCLONE_CHECK_PASSED", "run_id": run_id,
                           "github_sha": os.getenv("GITHUB_SHA")}
                verified_ownership_prefix(pointer, as_of)
                pointer_file = scratch / "current_ownership.json"
                pointer_file.write_text(json.dumps(pointer, indent=2), encoding="utf-8")
                # Publication marker is last; failure leaves the previous verified pointer.
                run("rclone", "copyto", str(pointer_file), REMOTE + "stock_state_strategy/current_ownership.json")
                run("rclone", "check", str(scratch), REMOTE + "stock_state_strategy",
                    "--include", "/current_ownership.json", "--one-way")
                status["publication_scope"] = "VERIFIED_CURRENT_OWNERSHIP_AND_STATE"
        status["status"] = "PASSED_AND_SAVED_TO_DRIVE"
        return 0
    finally:
        status["finished_at"] = datetime.now(timezone.utc).isoformat()
        report = scratch / "run_status.json"
        report.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        # Status is diagnostic only; failures must remain failures even if this
        # supplemental upload cannot complete.
        try:
            run("rclone", "copyto", str(report),
                REMOTE + phase_status_path(run_id, args.task))
            # Preserve the legacy latest-status address for existing readers.
            run("rclone", "copyto", str(report),
                REMOTE + "stock_state_strategy/runs/" + run_id + "/run_status.json")
        except Exception as exc:
            print("Could not upload run status:", exc, file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
