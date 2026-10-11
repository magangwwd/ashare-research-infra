"""Read-only checks for the shared Tushare Gold data source (no download)."""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from data_source_paths import ROOT, resolve_project_path

REQUIRED_FILES = ("instruments.parquet", "daily_snapshot.parquet", "trading_calendar.parquet",
                  "name_history.parquet", "suspensions.parquet")
REQUIRED_DIRS = ("daily_prices", "daily_metrics", "adjustment_factors", "daily_limits")


def check_source(config_path: Path, min_as_of: date | None = None, strict_legacy: bool = False) -> dict:
    config_path = config_path.resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    market_key = "market_data_root" if "market_data_root" in config else "data_root"
    if market_key not in config:
        raise ValueError("Configuration lacks market_data_root/data_root")
    root = resolve_project_path(ROOT, str(config[market_key]))
    missing = [str(root / n) for n in REQUIRED_FILES if not (root / n).is_file()]
    missing += [str(root / n) for n in REQUIRED_DIRS if not (root / n).is_dir()]
    manifest_path = root / "manifest.json"
    manifest = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            missing.append(f"invalid manifest.json: {type(exc).__name__}")
    else:
        missing.append(str(manifest_path))
    as_of = manifest.get("as_of_date")
    parsed_date = None
    if as_of:
        try:
            parsed_date = date.fromisoformat(str(as_of)[:10])
        except ValueError:
            missing.append("manifest.as_of_date is not a valid ISO date")
    else:
        missing.append("manifest.as_of_date missing")

    legacy_missing = []
    if strict_legacy:
        for key in ("legacy_v5_label_path", "legacy_control_events_path",
                    "legacy_transition_candidates_path"):
            value = config.get(key)
            if value and not resolve_project_path(ROOT, str(value)).is_file():
                legacy_missing.append(str(value))
        for key in ("processed_data_dir",):
            value = config.get(key)
            if value and not resolve_project_path(ROOT, str(value)).is_dir():
                legacy_missing.append(str(value))
    stale = bool(min_as_of and (parsed_date is None or parsed_date < min_as_of))
    return {
        "status": "PASS" if not missing and not legacy_missing and not stale else "BLOCKED",
        "source": "tushare_gold_main_board",
        "config_file": str(config_path),
        "market_data_root": str(root),
        "manifest_as_of_date": as_of,
        "min_as_of_date": min_as_of.isoformat() if min_as_of else None,
        "data_stale": stale,
        "missing_data": missing,
        "missing_legacy_dependencies": legacy_missing,
        "manifest_schema_version": manifest.get("schema_version"),
        "strategy_execution": "PAPER_ONLY_NO_ENTRY_AUTHORIZATION",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/ownership_state.json")
    parser.add_argument("--min-as-of", help="ISO YYYY-MM-DD, fail if market manifest older")
    parser.add_argument("--strict-legacy", action="store_true",
                        help="Also require frozen historical V1 input artifacts")
    args = parser.parse_args()
    report = check_source(ROOT / args.config if not Path(args.config).is_absolute()
                          else Path(args.config),
                          date.fromisoformat(args.min_as_of) if args.min_as_of else None,
                          args.strict_legacy)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
