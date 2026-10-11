"""Unified command runner for entry, ownership, state, and forward workflows."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
OWNERSHIP_CONFIG = "configs/ownership_state.json"
OWNERSHIP_REBUILD_CONFIG = "configs/ownership_rebuild.json"
FORWARD_CONFIG = "configs/forward_20260818.json"


def run(script: str, *arguments: str) -> None:
    command = [sys.executable, str(ROOT / script), *arguments]
    print(f"Running: {script} {' '.join(arguments)}", flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def run_tests() -> None:
    test_files = [str(path.name) for path in sorted(ROOT.glob("test_*.py"))]
    monitor_test = ROOT / "live_position_monitor" / "test_monitor.py"
    if monitor_test.is_file():
        test_files.append(str(monitor_test.relative_to(ROOT)))
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "no:cacheprovider",
        *test_files,
    ]
    subprocess.run(command, cwd=ROOT, check=True)


def ownership_research(download: bool) -> None:
    if download:
        config = OWNERSHIP_REBUILD_CONFIG
        run("download_ownership.py", "--config", config)
        run("ownership_pipeline.py", "--config", config)
    else:
        # The default config points at the preserved demo02 processed dataset.
        # Do not rebuild it from the merged project.
        config = OWNERSHIP_CONFIG
    run("evaluate_factors.py", "--config", config)
    run("ml_incremental_test.py", "--config", config, "--horizon", "20")
    run("concentration_rule_audit.py", "--config", config)
    run("control_state_strategy.py", "--config", config)
    run("control_decile_audit.py")
    run("control_alpha_overlay.py")


def entry_audit() -> None:
    run("historical_state_audit.py", "--config", "config.json")
    run("v5_executable_tail.py", "--config", "config.json")
    run("v5_feature_information_audit.py", "--config", "config.json")
    run("v5_frozen_composite_audit.py", "--config", "config.json")
    run("v6_control_monitor_entry_audit.py", "--config", "config.json")


def current_state(as_of: str, config: str) -> None:
    output = f"output/state_definition_{as_of.replace('-', '')}"
    run(
        "state_definition_pool.py",
        "--config",
        config,
        "--as-of",
        as_of,
        "--output-dir",
        output,
    )


def forward_20260818() -> None:
    state_output = "output/forward_20260818/state_definition_pool"
    transition_output = "output/forward_20260818/state_transition"
    strategy_output = "output/forward_20260818/lock_to_markup_v1"
    run(
        "state_definition_pool.py",
        "--config",
        FORWARD_CONFIG,
        "--as-of",
        "2026-08-18",
        "--output-dir",
        state_output,
    )
    run(
        "state_transition_backtest.py",
        "--config",
        FORWARD_CONFIG,
        "--output-dir",
        transition_output,
        "--rebuild-state-cache",
        "--state-cache-only",
    )
    run(
        "current_lock_markup_snapshot.py",
        "--as-of",
        "2026-08-18",
        "--state-snapshot",
        f"{state_output}/current_snapshot.parquet",
        "--transition-snapshot",
        f"{transition_output}/current_state_transitions.parquet",
        "--output-dir",
        strategy_output,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "stage",
        choices=(
            "tests",
            "entry-audit",
            "ownership-research",
            "current-state",
            "forward-20260818",
        ),
    )
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--as-of", default="2026-08-11")
    parser.add_argument("--config", default=OWNERSHIP_CONFIG)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.stage == "tests":
        run_tests()
    elif args.stage == "entry-audit":
        entry_audit()
    elif args.stage == "ownership-research":
        ownership_research(args.download)
    elif args.stage == "current-state":
        current_state(args.as_of, args.config)
    elif args.stage == "forward-20260818":
        forward_20260818()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
