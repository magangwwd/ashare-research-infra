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
from .download import CRITICAL, DATASETS
from .manifest import atomic_json, read_dataset, read_json, read_parquet, read_session, utcnow


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
                     config: dict, suspended: set[str] | None = None, open_exchanges: set[str] | None = None) -> dict:
    session = frame.loc[frame["trade_date"] == day] if "trade_date" in frame else pd.DataFrame()
    if session.empty:
        raise ValidationError(f"{dataset}: missing trade_date {day}")
    validate_frame(dataset, session)
    pool = clean.expected_pool(master, day, suspended, open_exchanges)
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
        opened = calendar.loc[(calendar["exchange"].isin(["SSE", "SZSE"])) & (calendar["is_open"] == 1)
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
                                                   suspension_codes(location, day),
                                                   set(calendar.loc[(calendar["calendar_date"] == day) & (calendar["is_open"] == 1), "exchange"]))
                        report["datasets"][dataset].setdefault("sessions", {})[str(day)] = details
                    except Exception as exc:
                        report["datasets"][dataset]["passed"] = False
                        report["errors"].append(str(exc))
    if "security_master" in frames and clean.mainboard_pool(frames["security_master"]).empty:
        report["errors"].append("security_master: no explicitly identified listed SSE/SZSE mainboard A shares")
    if "security_master" in frames and "trade_cal" in frames:
        calendar = frames["trade_cal"]
        for dataset in ("daily", "adj_factor", "daily_basic"):
            checked = 0
            files = list((location / "clean" / dataset).rglob("*.parquet"))
            for directory in {p.parent for p in files}:
                frame = pd.concat([read_parquet(p) for p in sorted(directory.glob("*.parquet"))], ignore_index=True)
                if "trade_date" not in frame:
                    continue
                for day, rows in frame.groupby("trade_date"):
                    try:
                        opened = set(calendar.loc[(calendar["calendar_date"] == day) & (calendar["is_open"] == 1), "exchange"])
                        validate_session(dataset, rows, frames["security_master"], day, config, suspension_codes(location, day), opened)
                        checked += 1
                    except Exception as exc:
                        report["errors"].append(str(exc))
                        report["datasets"][dataset]["passed"] = False
            report["datasets"][dataset]["historical_sessions_checked"] = checked
    if report["errors"]:
        report["passed"] = False
    return report


def session_ranges(selected: set[date], sessions: list[date]) -> list[dict]:
    """Compress adjacent trading sessions, including holidays between endpoints."""
    result = []
    current = None
    for day in sessions:
        if day not in selected:
            current = None
            continue
        if current is None:
            current = {"start_date": str(day), "end_date": str(day), "sessions": 0}
            result.append(current)
        current.update(end_date=str(day), sessions=current["sessions"] + 1)
    return result


def history_progress(location: Path, metadata: Path, config: dict, as_of: date, acceptance: dict) -> dict:
    """Inventory production files; receipts/cursors never prove full history."""
    start = datetime.strptime(config["download"]["history_start"], "%Y%m%d").date()
    capabilities = read_json(metadata / "capabilities/latest.json")
    boundaries = read_json(metadata / "capabilities/history_ranges.json")
    receipts = read_json(metadata / "manifest/requests.json")
    gaps = read_json(metadata / "manifest/gaps.json")
    calendar = read_dataset(location, "clean", "trade_cal")
    master = read_dataset(location, "clean", "security_master")
    sessions = []
    calendar_coverage = {}
    if not calendar.empty and not master.empty:
        for exchange in ("SSE", "SZSE"):
            present = set(calendar.loc[calendar["exchange"].eq(exchange)
                                       & calendar["calendar_date"].between(start, as_of), "calendar_date"])
            expected = list(pd.date_range(min(present), as_of).date) if present else []
            missing = set(expected) - present
            calendar_coverage[exchange] = {"observed_start": str(min(present)) if present else None,
                                           "missing_calendar_days": len(missing),
                                           "missing_ranges": [{"start_date": r["start_date"], "end_date": r["end_date"], "days": r["sessions"]}
                                                              for r in session_ranges(missing, expected)],
                                           "observed_range_complete": bool(present) and not missing}
        opened = calendar.loc[calendar["exchange"].isin(["SSE", "SZSE"])
                              & calendar["is_open"].eq(1)
                              & calendar["calendar_date"].between(start, as_of)]
        sessions = [day for day, group in opened.groupby("calendar_date", sort=True)
                    if clean.expected_pool(master, day, open_exchanges=set(group["exchange"]))]
    report = {"as_of": str(as_of), "checked_at": utcnow(), "requested_start": str(start),
              "source": "actual production Parquet; metadata only supplements file inventory",
              "production_version": location.resolve().name,
              "stored_critical_validation_passed": acceptance.get("passed", False),
              "core_observed_range_complete": False, "full_requested_core_range_complete": False,
              "extensions_history_complete": None,
              "calendar_coverage": calendar_coverage,
              "history_run": read_json(metadata / "manifest/history.json"),
              "intraday": read_json(metadata / "manifest/intraday.json"),
              "datasets": {}, "scan_errors": [],
              "notes": ["日期起止不能证明区间完整；核心缺失日按真实日历和基础证券池计算。",
                        "观察到的提供者边界不等于永久接口起点；边界前范围单独列出。",
                        "扩展数据的请求回执及空响应不是财务/行业全量覆盖证明。",
                        "全局游标及 all_enabled_requests_examined 不作为历史完成判据。",
                        "本报告不验收连续调度、历史修订或 Drive 发布，不代表 Task 01 最终验收。"]}
    core_complete, requested_complete = [], []
    for name, spec in DATASETS.items():
        enabled = spec.tier == "critical" or name == "security_master" or (
            config["datasets"].get(spec.tier, False)
            and (spec.tier != "optional" or name in config["optional_whitelist"]))
        capability = capabilities.get("interfaces", {}).get(name, {})
        item = {"tier": spec.tier, "enabled": enabled,
                "capability_status": capability.get("status", "NOT_AUDITED"),
                "permission_status": capability.get("permission_status", "NOT_AUDITED"),
                "capability_reason": capability.get("reason"), "layers": {}}
        core_dates = set()
        for layer in ("raw", "clean"):
            files = sorted((location / layer / name).rglob("*.parquet"))
            dates: dict[str, set[date]] = {}
            rows = 0
            for file in files:
                try:
                    parquet = pq.ParquetFile(file)
                    columns = [c for c in ("trade_date", "calendar_date", "cal_date", "report_period",
                                           "end_date", "ann_date", "in_date", "start_date", "surv_date")
                               if c in parquet.schema_arrow.names]
                    frame = parquet.read(columns=columns).to_pandas()
                    rows += parquet.metadata.num_rows
                    for column in columns:
                        values = pd.to_datetime(frame[column].dropna().astype(str), format="mixed", errors="coerce")
                        dates.setdefault(column, set()).update(values.dropna().dt.date)
                except Exception as exc:
                    report["scan_errors"].append({"dataset": name, "layer": layer,
                                                  "file": str(file.relative_to(location)), "reason": str(exc)})
            item["layers"][layer] = {"files": len(files), "rows": rows, "date_ranges": {
                c: {"start_date": str(min(ds)), "end_date": str(max(ds)), "distinct_dates": len(ds)}
                for c, ds in dates.items() if ds}}
            if layer == "clean":
                core_dates = dates.get("trade_date", set())
        dataset_receipts = [r for r in receipts.values() if r.get("dataset") == name and r.get("status") == "complete"]
        item["query_receipts"] = {"successful": len(dataset_receipts),
                                  "empty_responses": sum(r.get("rows") == 0 for r in dataset_receipts),
                                  "scope": "request records, not full-history verification"}
        item["gaps"] = [{"params": g.get("params", {}), "reason": g.get("reason"),
                         "missing_stocks": len(g.get("missing_ts_codes", []))}
                        for g in gaps.values() if g.get("dataset") == name]
        if name in ("daily", "adj_factor", "daily_basic"):
            observed = boundaries.get(name, {}).get("first_observed_date")
            floor = max(start, datetime.strptime(observed, "%Y%m%d").date()) if observed else start
            eligible = [d for d in sessions if d >= floor]
            prefix = {d for d in sessions if d < floor}
            missing = set(eligible) - core_dates
            stored_valid = acceptance.get("datasets", {}).get(name, {}).get("passed", False)
            complete = bool(eligible) and not missing and stored_valid and acceptance.get("passed", False)
            item["history"] = {"target_start": str(floor), "boundary_observed": bool(observed),
                               "expected_sessions": len(eligible), "stored_target_sessions": len(set(eligible) & core_dates),
                               "missing_sessions": len(missing), "missing_ranges": session_ranges(missing, eligible),
                               "provider_prefix_sessions": len(prefix), "provider_prefix_ranges": session_ranges(prefix, sessions),
                               "stored_validation_passed": stored_valid, "observed_range_complete": complete}
            core_complete.append(complete)
            requested_complete.append(complete and not prefix)
        item["status"] = ("DISABLED" if not enabled else "CAPABILITY_UNAVAILABLE" if capability.get("status") == "UNAVAILABLE"
                          else "STORED_HISTORY_INCOMPLETE" if name in ("daily", "adj_factor", "daily_basic") and not item["history"]["observed_range_complete"]
                          else "STORED" if item["layers"]["clean"]["rows"] else "NOT_STORED")
        report["datasets"][name] = item
    calendars_complete = len(calendar_coverage) == 2 and all(c["observed_range_complete"] for c in calendar_coverage.values())
    report["core_observed_range_complete"] = bool(core_complete) and all(core_complete) and calendars_complete and not report["scan_errors"]
    report["full_requested_core_range_complete"] = bool(requested_complete) and all(requested_complete) and calendars_complete and not report["scan_errors"]
    intraday_state = read_json(metadata / "manifest/intraday.json")
    report["intraday_history"] = {
        "enabled": bool(config.get("intraday", {}).get("enabled")),
        "available": intraday_state.get("available"),
        "activated": intraday_state.get("activated", False),
        "history_start": config.get("intraday", {}).get("history_start"),
        "request_universe": intraday_state.get("history_request_universe"),
        "complete_requests": intraday_state.get("history_complete_requests"),
        "pending_requests": intraday_state.get("history_pending_requests"),
        "complete_ratio": intraday_state.get("history_complete_ratio"),
        "complete": intraday_state.get("history_complete", False),
        "latest_session_coverage": intraday_state.get("coverage"),
    }
    report["market_history_complete"] = bool(
        report["full_requested_core_range_complete"]
        and (not report["intraday_history"]["enabled"] or report["intraday_history"]["complete"])
    )
    return report


def progress_markdown(report: dict) -> str:
    lines = ["# 历史进度与缺口", "", f"截至日期：{report['as_of']}。实际生产文件扫描；不代表 Task 01 最终验收。", "",
             f"已存核心验收：{report['stored_critical_validation_passed']}；观察范围补齐：{report['core_observed_range_complete']}。", "",
             "| 核心行情 | 目标起点 | 应存交易日 | 已存目标交易日 | 缺失日 | 提供者边界前交易日 |", "| --- | --- | ---: | ---: | ---: | ---: |"]
    for name, item in report["datasets"].items():
        if "history" in item:
            h = item["history"]
            lines.append(f"| {name} | {h['target_start']} | {h['expected_sessions']} | {h['stored_target_sessions']} | {h['missing_sessions']} | {h['provider_prefix_sessions']} |")
    minute = report.get("intraday", {})
    if minute:
        lines += ["", "## 15分钟行情",
                  "",
                  f"- 启用：{minute.get('enabled')}；接口可用：{minute.get('available')}；daily_from_minutes 激活：{minute.get('activated')}",
                  f"- 历史起点：{minute.get('history_start')}；最新分钟交易日：{minute.get('latest_session')}；证券覆盖率：{minute.get('coverage')}",
                  f"- 本轮最新日请求：{minute.get('recent_requests_fetched', 0)}；本轮历史请求：{minute.get('history_requests_fetched', 0)}；历史 bars：{minute.get('history_bars_fetched', 0)}",
                  ""]
    lines += ["", "| 数据集 | 状态 | Raw 行数 | Clean 行数 | Gap 条数 |", "| --- | --- | ---: | ---: | ---: |"]
    for name, item in report["datasets"].items():
        lines.append(f"| {name} | {item['status']} | {item['layers']['raw']['rows']} | {item['layers']['clean']['rows']} | {len(item['gaps'])} |")
    lines += ["", "完整日期范围、缺失区间、权限及 Gap 原因见 history_progress.json。", ""]
    lines += ["- " + note for note in report["notes"]]
    return "\n".join(lines) + "\n"


def validation_as_of(now: datetime) -> date:
    """Use exactly the same completed-session cutoff as Task 01 ingestion."""
    from .run_task01 import resolved_as_of
    return resolved_as_of(now)


def main() -> int:
    parser = argparse.ArgumentParser(description="Task 01 Critical acceptance gate")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--root", default=None)
    parser.add_argument("--as-of", default=None)
    args = parser.parse_args()
    config = yaml.safe_load(Path(args.config).read_text())
    root = Path(args.root or config["storage"]["root"]).resolve()
    from zoneinfo import ZoneInfo
    as_of = date.fromisoformat(args.as_of) if args.as_of else validation_as_of(datetime.now(ZoneInfo(config["schedule"]["timezone"])))
    print(f"TASK01_VALIDATION_DATE_SELECTION as_of={as_of} explicit={bool(args.as_of)}")
    report = validate_task01(root / "data/current", config, as_of)
    atomic_json(root / "metadata/acceptance.json", report)
    progress = history_progress(root / "data/current", root / "metadata", config, as_of, report)
    atomic_json(root / "metadata/history_progress.json", progress)
    (root / "metadata/history_progress.md").write_text(progress_markdown(progress), encoding="utf-8")
    print("SUCCESS" if report["passed"] else "FAILED")
    for error in report["errors"]:
        print(error)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
