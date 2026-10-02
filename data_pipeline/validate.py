"""Small quality gate; never accepts an absent/empty Critical dataset."""
from __future__ import annotations

import argparse
import math
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import yaml

from . import clean
from .download import CRITICAL
from .manifest import atomic_json, read_dataset, read_parquet, read_session, utcnow


class ValidationError(ValueError):
    pass


def validate_frame(dataset: str, frame: pd.DataFrame) -> None:
    if frame.empty:
        raise ValidationError(f"{dataset}: empty data")
    expected = {f.name for f in clean.SCHEMAS[dataset]} | {"schema_version"}
    if set(frame.columns) != expected:
        raise ValidationError(f"{dataset}: fixed schema columns do not match")
    if frame["schema_version"].isna().any() or not frame["schema_version"].eq(clean.schema_version(dataset)).all():
        raise ValidationError(f"{dataset}: unsupported schema version")
    if frame.duplicated(clean.KEYS[dataset]).any():
        raise ValidationError(f"{dataset}: duplicate primary key")
    if dataset in CRITICAL or dataset == "security_master":
        if frame[clean.KEYS[dataset]].isna().any().any():
            raise ValidationError(f"{dataset}: null primary key")
    for f in clean.SCHEMAS[dataset]:
        values = frame[f.name].dropna()
        if f.dtype == "date" and not all(isinstance(x, date) and not isinstance(x, datetime) for x in values):
            raise ValidationError(f"{dataset}.{f.name}: must be Date")
        if f.dtype in ("float64", "int64") and not pd.api.types.is_numeric_dtype(frame[f.name]):
            raise ValidationError(f"{dataset}.{f.name}: must be numeric")
    required = {"stock_basic": ["ts_code", "symbol", "name", "list_date", "list_status"],
                "trade_cal": ["exchange", "calendar_date", "is_open"],
                "security_master": ["ts_code", "exchange", "board", "security_type", "list_date"],
                "daily": ["open", "high", "low", "close", "vol"],
                "adj_factor": ["adj_factor"], "daily_basic": ["close", "total_share", "total_mv"]}
    # Field null density is checked per session in validate_session. Identifiers are strict.
    identity = [c for c in required.get(dataset, []) if c in {"ts_code", "symbol", "name", "market", "list_date", "list_status", "exchange", "board", "security_type", "calendar_date", "is_open"}]
    if identity and frame[identity].isna().any().any():
        raise ValidationError(f"{dataset}: missing essential identity/calendar fields")
    if dataset == "stock_basic" and frame.loc[frame["list_status"] == "L", "market"].isna().any():
        raise ValidationError("stock_basic: listed securities have missing market metadata")


def validate_session(dataset: str, frame: pd.DataFrame, master: pd.DataFrame, day: date,
                     config: dict, suspended: set[str] | None = None) -> dict:
    session = frame.loc[frame["trade_date"] == day] if "trade_date" in frame else pd.DataFrame()
    if session.empty:
        raise ValidationError(f"{dataset}: missing trade_date {day}")
    validate_frame(dataset, session)
    pool = clean.expected_pool(master, day, suspended)
    if not pool:
        raise ValidationError(f"{dataset}: theoretical security pool is empty")
    missing = sorted(pool - set(session["ts_code"]))
    coverage = (len(pool) - len(missing)) / len(pool)
    minimum = min(config["validation"]["min_daily_rows"], math.ceil(len(pool) * config["validation"]["min_coverage"]))
    if len(session) < minimum or coverage < config["validation"]["min_coverage"]:
        raise ValidationError(f"{dataset} {day}: rows={len(session)}, coverage={coverage:.2%}, missing={len(missing)}")
    key_values = {"daily": ["open", "high", "low", "close", "vol"],
                  "adj_factor": ["adj_factor"], "daily_basic": ["close", "total_share", "total_mv"]}[dataset]
    if (session[key_values].isna().mean() > config["validation"]["max_key_null_fraction"]).any():
        raise ValidationError(f"{dataset} {day}: excessive key-field nulls")
    return {"rows": len(session), "expected": len(pool), "coverage": coverage, "missing_ts_codes": missing}


def suspension_codes(location: Path, day: date) -> set[str]:
    frame = read_session(location, "suspend_d", day)
    if frame.empty:
        return set()
    return set(frame.loc[(frame["trade_date"] == day) & frame["suspend_type"].eq("S"), "ts_code"])


def validate_task01(location: Path, config: dict, as_of: date, dates: list[date] | None = None) -> dict:
    report: dict = {"passed": True, "checked_at": utcnow(), "as_of": str(as_of), "datasets": {}, "errors": []}
    frames: dict[str, pd.DataFrame] = {}
    for dataset in (*CRITICAL, "security_master"):
        try:
            files = list((location / "clean" / dataset).rglob("*.parquet"))
            if not files:
                raise ValidationError(f"{dataset}: files missing")
            for file in files:
                stored_schema = pq.ParquetFile(file).schema_arrow
                if not stored_schema.equals(clean.arrow_schema(dataset), check_metadata=False):
                    raise ValidationError(f"{dataset}: Parquet Arrow schema does not match")
                if any(pq.ParquetFile(file).metadata.row_group(0).column(i).compression != "ZSTD"
                       for i in range(len(stored_schema))):
                    raise ValidationError(f"{dataset}: Parquet compression must be ZSTD")
            rows = 0
            for directory in sorted({p.parent for p in files}):
                frame = pd.concat([read_parquet(p) for p in sorted(directory.glob("*.parquet"))], ignore_index=True)
                validate_frame(dataset, frame)
                rows += len(frame)
                if dataset in ("daily", "daily_basic", "adj_factor"):
                    expected_partition = f"year={frame.iloc[0]['trade_date'].year}/month={frame.iloc[0]['trade_date'].month:02d}"
                    if str(directory.relative_to(location / "clean" / dataset)) != expected_partition:
                        raise ValidationError(f"{dataset}: invalid Hive partition")
                    if len({(d.year, d.month) for d in frame["trade_date"]}) != 1:
                        raise ValidationError(f"{dataset}: dates cross partition")
            if dataset not in ("daily", "daily_basic", "adj_factor"):
                frames[dataset] = read_dataset(location, "clean", dataset)
                validate_frame(dataset, frames[dataset])
            report["datasets"][dataset] = {"passed": True, "rows": rows}
        except Exception as exc:
            report["passed"] = False
            report["datasets"][dataset] = {"passed": False, "reason": str(exc)}
            report["errors"].append(str(exc))
    if "trade_cal" in frames:
        calendar = frames["trade_cal"]
        for exchange in ("SSE", "SZSE"):
            if calendar.loc[(calendar["exchange"] == exchange) & (calendar["calendar_date"] == as_of)].empty:
                report["errors"].append(f"trade_cal: missing {exchange} calendar date {as_of}")
        opened = calendar.loc[(calendar["exchange"] == "SSE") & (calendar["is_open"] == 1)
                              & (calendar["calendar_date"] <= as_of), "calendar_date"]
        if opened.empty:
            report["errors"].append("trade_cal: no latest open session")
        else:
            sessions = dates if dates is not None else [opened.max()]
            for dataset in ("daily", "adj_factor", "daily_basic"):
                if not report["datasets"].get(dataset, {}).get("passed") or "security_master" not in frames:
                    continue
                for day in sessions:
                    try:
                        details = validate_session(dataset, read_session(location, dataset, day), frames["security_master"], day, config,
                                                   suspension_codes(location, day))
                        report["datasets"][dataset].setdefault("sessions", {})[str(day)] = details
                    except Exception as exc:
                        report["datasets"][dataset]["passed"] = False
                        report["errors"].append(str(exc))
    if "security_master" in frames and clean.mainboard_pool(frames["security_master"]).empty:
        report["errors"].append("security_master: no explicitly identified listed SSE/SZSE mainboard A shares")
    if report["errors"]:
        report["passed"] = False
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Task 01 Critical acceptance gate")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--root", default=None)
    parser.add_argument("--as-of", default=None)
    args = parser.parse_args()
    config = yaml.safe_load(Path(args.config).read_text())
    root = Path(args.root or config["storage"]["root"]).resolve()
    from zoneinfo import ZoneInfo
    as_of = date.fromisoformat(args.as_of) if args.as_of else datetime.now(ZoneInfo(config["schedule"]["timezone"])).date()
    report = validate_task01(root / "data/current", config, as_of)
    atomic_json(root / "metadata/acceptance.json", report)
    print("SUCCESS" if report["passed"] else "FAILED")
    for error in report["errors"]:
        print(error)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
