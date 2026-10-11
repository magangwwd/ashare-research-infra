from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from backtest_mfe_mae import load_config, read_year_partitions, resolve_from


EXPECTED_STATE_TABLES = {
    "name_history": {
        "default": "name_history.parquet",
        "required": {"instrument_id", "effective_date", "name_zh", "is_st"},
        "purpose": "历史简称与逐时点 ST/*ST 状态",
    },
    "suspensions": {
        "default": "suspensions.parquet",
        "required": {"instrument_id", "suspend_date", "resume_date"},
        "purpose": "停牌、复牌及行情缺口归因",
    },
    "daily_limits": {
        "default": "daily_limits",
        "required": {"instrument_id", "trade_date", "up_limit", "down_limit"},
        "purpose": "逐日精确涨停价和跌停价",
    },
}


def json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(json_value(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def writable_output_dir(preferred: Path, fallback_name: str) -> Path:
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        probe = preferred / ".codex_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return preferred
    except OSError as exc:
        fallback = Path(tempfile.gettempdir()) / fallback_name
        fallback.mkdir(parents=True, exist_ok=True)
        print(
            f"Output directory is not writable ({exc}); using {fallback}",
            flush=True,
        )
        return fallback


def parquet_files(path: Path) -> list[Path]:
    if path.is_file() and path.suffix.lower() == ".parquet":
        return [path]
    if path.is_dir():
        return sorted(path.rglob("*.parquet"))
    return []


def inspect_state_table(path: Path, required: set[str]) -> dict[str, Any]:
    files = parquet_files(path)
    if not files:
        return {
            "path": str(path),
            "exists": False,
            "files": 0,
            "columns": [],
            "missing_columns": sorted(required),
            "ready": False,
        }
    columns = set(pq.read_schema(files[-1]).names)
    missing = sorted(required - columns)
    return {
        "path": str(path),
        "exists": True,
        "files": len(files),
        "sample_file": str(files[-1]),
        "columns": sorted(columns),
        "missing_columns": missing,
        "ready": not missing,
    }


def find_history_manifest(data_root: Path) -> Path:
    # data_root normally ends in data/gold/main_board.
    data_dir = data_root.parent.parent
    return (
        data_dir
        / "manifests"
        / "tushare"
        / "main_board_history"
        / "last_run_summary.json"
    )


def load_history_manifest(data_root: Path) -> dict[str, Any]:
    path = find_history_manifest(data_root)
    if not path.exists():
        return {"path": str(path), "exists": False, "status": None}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {"path": str(path), "exists": True, **payload}


def build_gap_events(
    prices: pd.DataFrame,
    calendar: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    open_days = (
        calendar.loc[calendar["is_open"], ["calendar_date"]]
        .drop_duplicates()
        .sort_values("calendar_date")
        .reset_index(drop=True)
    )
    open_days["trade_idx"] = np.arange(len(open_days), dtype="int32")
    date_to_idx = open_days.set_index("calendar_date")["trade_idx"]
    frame = prices[["instrument_id", "trade_date"]].copy()
    frame["trade_idx"] = frame["trade_date"].map(date_to_idx)
    missing_calendar = int(frame["trade_idx"].isna().sum())
    frame = frame.loc[frame["trade_idx"].notna()].copy()
    frame["trade_idx"] = frame["trade_idx"].astype("int32")
    frame = frame.sort_values(["instrument_id", "trade_idx"])
    frame["previous_trade_idx"] = frame.groupby(
        "instrument_id", sort=False
    )["trade_idx"].shift(1)
    frame["gap_sessions"] = (
        frame["trade_idx"] - frame["previous_trade_idx"] - 1
    )
    gaps = frame.loc[frame["gap_sessions"] > 0].copy()
    if gaps.empty:
        events = pd.DataFrame(
            columns=[
                "instrument_id",
                "previous_date",
                "next_date",
                "missing_open_sessions",
            ]
        )
    else:
        idx_to_date = open_days.set_index("trade_idx")["calendar_date"]
        gaps["previous_date"] = gaps["previous_trade_idx"].astype("int32").map(
            idx_to_date
        )
        gaps = gaps.rename(
            columns={"trade_date": "next_date", "gap_sessions": "missing_open_sessions"}
        )
        events = gaps[
            [
                "instrument_id",
                "previous_date",
                "next_date",
                "missing_open_sessions",
            ]
        ].sort_values("missing_open_sessions", ascending=False)
    audit = {
        "price_dates_missing_from_calendar": missing_calendar,
        "gap_events": int(len(events)),
        "affected_instruments": int(events["instrument_id"].nunique()),
        "missing_open_sessions_total": int(events["missing_open_sessions"].sum()),
        "largest_gap_sessions": (
            int(events["missing_open_sessions"].max()) if len(events) else 0
        ),
        "interpretation": (
            "缺口可能是停牌、退市前后边界或上游缺数；没有停复牌事件表时不能精确归因"
        ),
    }
    return events, audit


def audit_limit_status(panel: pd.DataFrame) -> dict[str, Any]:
    status = pd.to_numeric(panel["limit_status"], errors="coerce")
    known = status.isin(range(7))
    one_word = status.isin([3, 6])
    flat_bar = (
        np.isclose(panel["open"], panel["high"], equal_nan=False)
        & np.isclose(panel["open"], panel["low"], equal_nan=False)
        & np.isclose(panel["open"], panel["close"], equal_nan=False)
    )
    counts = status.value_counts(dropna=False).sort_index()
    return {
        "rows": int(len(panel)),
        "missing": int(status.isna().sum()),
        "invalid_value_rows": int((~known & status.notna()).sum()),
        "coverage_rate": float(known.mean()) if len(panel) else None,
        "value_counts": {str(key): int(value) for key, value in counts.items()},
        "one_word_rows": int(one_word.sum()),
        "one_word_nonflat_ohlc_rows": int((one_word & ~flat_bar).sum()),
        "limitation": (
            "limit_status 是收盘分类，不是精确涨跌停价，也不能还原排队成交"
        ),
    }


def load_state_frame(path: Path, columns: list[str]) -> pd.DataFrame:
    files = parquet_files(path)
    if not files:
        return pd.DataFrame(columns=columns)
    return pd.concat(
        [pd.read_parquet(file, columns=columns) for file in files],
        ignore_index=True,
    )


def audit_exact_daily_limits(
    panel: pd.DataFrame,
    path: Path,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    limits = load_state_frame(
        path,
        ["instrument_id", "trade_date", "up_limit", "down_limit"],
    )
    limits["trade_date"] = pd.to_datetime(limits["trade_date"])
    if not panel.empty:
        limits = limits.loc[
            (limits["trade_date"] >= panel["trade_date"].min())
            & (limits["trade_date"] <= panel["trade_date"].max())
        ].copy()
    duplicate_keys = int(limits.duplicated(["instrument_id", "trade_date"]).sum())
    merged = panel.merge(
        limits,
        on=["instrument_id", "trade_date"],
        how="left",
        validate="one_to_one" if not duplicate_keys else "many_to_many",
    )
    exact_known = (
        merged["up_limit"].notna()
        & merged["down_limit"].notna()
        & (merged["up_limit"] > 0)
        & (merged["down_limit"] > 0)
        & (merged["down_limit"] <= merged["up_limit"])
    )
    flat_bar = (
        np.isclose(merged["open"], merged["high"], atol=0.0051, rtol=0)
        & np.isclose(merged["open"], merged["low"], atol=0.0051, rtol=0)
        & np.isclose(merged["open"], merged["close"], atol=0.0051, rtol=0)
    )
    one_word_up = exact_known & flat_bar & np.isclose(
        merged["open"], merged["up_limit"], atol=0.0051, rtol=0
    )
    one_word_down = exact_known & flat_bar & np.isclose(
        merged["open"], merged["down_limit"], atol=0.0051, rtol=0
    )
    provider_status = pd.to_numeric(merged["limit_status"], errors="coerce")
    provider_one_word_up = (provider_status == 3).fillna(False)
    provider_one_word_down = (provider_status == 6).fillna(False)
    audit = {
        "rows": int(len(limits)),
        "duplicate_keys": duplicate_keys,
        "price_rows": int(len(panel)),
        "missing_or_invalid_price_rows": int((~exact_known).sum()),
        "coverage_rate": float(exact_known.mean()) if len(merged) else None,
        "one_word_up_rows": int(one_word_up.sum()),
        "one_word_down_rows": int(one_word_down.sum()),
        "provider_one_word_up_mismatches": int((provider_one_word_up != one_word_up).sum()),
        "provider_one_word_down_mismatches": int((provider_one_word_down != one_word_down).sum()),
        "truth_rule": "OHLC flat and equal to exact daily up/down limit within CNY 0.0051",
    }
    merged["exact_limit_known"] = exact_known
    merged["exact_one_word_up"] = one_word_up
    merged["exact_one_word_down"] = one_word_down
    return merged, audit


def audit_name_history(prices: pd.DataFrame, path: Path) -> dict[str, Any]:
    history = pd.read_parquet(path)
    for column in ("effective_date", "end_date"):
        history[column] = pd.to_datetime(history[column])
    history = history.sort_values(["instrument_id", "effective_date", "end_date"])
    history["previous_end"] = history.groupby("instrument_id", sort=False)[
        "end_date"
    ].shift()
    overlapping = history["previous_end"].notna() & (
        history["effective_date"] <= history["previous_end"]
    )
    duplicate_keys = int(
        history.duplicated(["instrument_id", "effective_date", "name_zh"]).sum()
    )
    covered_rows = 0
    st_rows = 0
    history_groups = {
        instrument_id: group
        for instrument_id, group in history.groupby("instrument_id", sort=False)
    }
    for instrument_id, group in prices.groupby("instrument_id", sort=False):
        states = history_groups.get(instrument_id)
        if states is None or states.empty:
            continue
        dates = group["trade_date"].to_numpy(dtype="datetime64[ns]")
        starts = states["effective_date"].to_numpy(dtype="datetime64[ns]")
        indices = np.searchsorted(starts, dates, side="right") - 1
        has_start = indices >= 0
        safe_indices = np.maximum(indices, 0)
        ends = states["end_date"].to_numpy(dtype="datetime64[ns]")[safe_indices]
        active = has_start & (np.isnat(ends) | (dates <= ends))
        covered_rows += int(active.sum())
        st_values = states["is_st"].fillna(False).to_numpy(dtype=bool)[safe_indices]
        st_rows += int((active & st_values).sum())
    return {
        "rows": int(len(history)),
        "instruments": int(history["instrument_id"].nunique()),
        "duplicate_keys": duplicate_keys,
        "overlapping_intervals": int(overlapping.sum()),
        "price_rows": int(len(prices)),
        "covered_price_rows": covered_rows,
        "missing_price_rows": int(len(prices) - covered_rows),
        "coverage_rate": float(covered_rows / len(prices)) if len(prices) else None,
        "historical_st_price_rows": st_rows,
    }


def audit_gap_coverage(
    gaps: pd.DataFrame,
    suspensions_path: Path,
    calendar: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    suspensions = pd.read_parquet(suspensions_path)
    suspensions["suspend_date"] = pd.to_datetime(suspensions["suspend_date"])
    suspensions["resume_date"] = pd.to_datetime(suspensions["resume_date"])
    by_instrument = {
        instrument_id: group
        for instrument_id, group in suspensions.groupby("instrument_id", sort=False)
    }
    dates = np.array(
        sorted(calendar.loc[calendar["is_open"], "calendar_date"].unique()),
        dtype="datetime64[ns]",
    )
    covered_counts: list[int] = []
    unknown_counts: list[int] = []
    for row in gaps.itertuples(index=False):
        previous_idx = int(np.searchsorted(dates, np.datetime64(row.previous_date)))
        next_idx = int(np.searchsorted(dates, np.datetime64(row.next_date)))
        missing_indices = set(range(previous_idx + 1, next_idx))
        covered: set[int] = set()
        intervals = by_instrument.get(row.instrument_id)
        if intervals is not None:
            for interval in intervals.itertuples(index=False):
                suspend_idx = int(
                    np.searchsorted(dates, np.datetime64(interval.suspend_date))
                )
                resume_idx = (
                    len(dates)
                    if pd.isna(interval.resume_date)
                    else int(np.searchsorted(dates, np.datetime64(interval.resume_date)))
                )
                if suspend_idx < next_idx and resume_idx > previous_idx + 1:
                    covered.update(
                        range(
                            max(suspend_idx, previous_idx + 1),
                            min(resume_idx, next_idx),
                        )
                    )
        covered_count = len(missing_indices & covered)
        covered_counts.append(covered_count)
        unknown_counts.append(len(missing_indices) - covered_count)
    annotated = gaps.copy()
    annotated["suspension_covered_sessions"] = covered_counts
    annotated["unknown_sessions"] = unknown_counts
    unknown = annotated["unknown_sessions"] > 0
    return annotated, {
        "interval_rows": int(len(suspensions)),
        "fully_explained_gap_events": int((~unknown).sum()),
        "partially_or_unexplained_gap_events": int(unknown.sum()),
        "suspension_covered_sessions": int(sum(covered_counts)),
        "unknown_sessions": int(sum(unknown_counts)),
    }


def write_report(path: Path, summary: dict[str, Any]) -> None:
    ready = bool(summary["ready_for_v5_labels"])
    status = "READY" if ready else "BLOCKED"
    rows = [
        "# 历史交易状态数据审计",
        "",
        "## 结论",
        "",
        f"**{status}：{'可以生成严格V5标签' if ready else '不能生成可验收的严格V5标签'}。**",
        "",
        f"数据截至 `{summary['data_as_of']}`；审计区间从 `{summary['start_date']}` 开始。",
        "",
    ]
    if summary["blockers"]:
        rows.extend(["阻断项：", ""])
        rows.extend(f"- {item}" for item in summary["blockers"])
        rows.append("")
    rows.extend(
        [
            "## 状态表",
            "",
            "| 数据表 | 用途 | 文件数 | 缺失字段 | 状态 |",
            "|---|---|---:|---|---|",
        ]
    )
    for name, spec in EXPECTED_STATE_TABLES.items():
        audit = summary["state_tables"][name]
        missing = ", ".join(audit["missing_columns"]) or "—"
        rows.append(
            f"| `{name}` | {spec['purpose']} | {audit['files']} | {missing} | "
            f"{'可用' if audit['ready'] else '缺失'} |"
        )
    limit = summary["limit_status"]
    exact = summary["exact_daily_limits"]
    names = summary["name_history"]
    gaps = summary["price_gaps"]
    gap_coverage = summary["price_gap_coverage"]
    rows.extend(
        [
            "",
            "## 当前日线状态质量",
            "",
            f"- 行情与 `limit_status` 合并行数：{limit['rows']:,}。",
            f"- `limit_status` 覆盖率：{limit['coverage_rate']:.4%}；非法值：{limit['invalid_value_rows']:,}。",
            f"- 一字涨跌停记录：{limit['one_word_rows']:,}；OHLC非平价：{limit['one_word_nonflat_ohlc_rows']:,}。",
            f"- 精确涨跌停价覆盖率：{exact['coverage_rate']:.4%}；缺失或无效：{exact['missing_or_invalid_price_rows']:,}行。",
            f"- 精确识别一字涨停：{exact.get('one_word_up_rows', 0):,}行；一字跌停：{exact.get('one_word_down_rows', 0):,}行。",
            f"- 历史简称/ST状态覆盖率：{names['coverage_rate']:.4%}；历史ST行情行：{names.get('historical_st_price_rows', 0):,}。",
            f"- 相邻行情间存在交易日缺口：{gaps['gap_events']:,}段，涉及{gaps['affected_instruments']:,}只股票，共{gaps['missing_open_sessions_total']:,}个开市日。",
            f"- 停牌区间完整解释：{gap_coverage['fully_explained_gap_events']:,}段；未知开市日：{gap_coverage['unknown_sessions']:,}。",
            f"- 当前简称识别为 ST/*ST 的证券：{summary['instruments']['current_st_names']:,}只；该字段不能代表历史时点。",
            "",
            "## 处理原则",
            "",
            "缺口不填零。严格V5标签以逐时点名称/ST、精确每日涨跌停价和已验证停复牌区间为状态真值；daily_metrics.limit_status仅保留为辅助对照。",
        ]
    )
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit point-in-time A-share states")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--start-date", default="2020-01-01")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--name-history", default=None)
    parser.add_argument("--suspensions", default=None)
    parser.add_argument("--daily-limits", default=None)
    parser.add_argument("--fail-on-blocker", action="store_true")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    config = load_config(resolve_from(script_dir, args.config))
    data_root = resolve_from(script_dir, str(config["data_root"]))
    preferred_output_dir = (
        Path(args.output_dir).resolve()
        if args.output_dir
        else resolve_from(script_dir, str(config["output_dir"])) / "data_state_audit"
    )
    output_dir = writable_output_dir(
        preferred_output_dir, "demo01_data_state_audit_latest"
    )
    start = pd.Timestamp(args.start_date)

    manifest = load_history_manifest(data_root)
    end_year = int(pd.Timestamp(manifest.get("ended_range", pd.Timestamp.now())).year)
    prices = read_year_partitions(
        data_root,
        "daily_prices",
        start.year,
        end_year,
        [
            "instrument_id",
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "is_quality_valid",
        ],
    )
    metrics = read_year_partitions(
        data_root,
        "daily_metrics",
        start.year,
        end_year,
        ["instrument_id", "trade_date", "limit_status"],
    )
    calendar = pd.read_parquet(data_root / "trading_calendar.parquet")
    instruments = pd.read_parquet(data_root / "instruments.parquet")
    for frame in (prices, metrics):
        frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    calendar["calendar_date"] = pd.to_datetime(calendar["calendar_date"])
    prices = prices.loc[prices["trade_date"] >= start].copy()
    metrics = metrics.loc[metrics["trade_date"] >= start].copy()

    price_duplicate_keys = int(
        prices.duplicated(["instrument_id", "trade_date"]).sum()
    )
    metric_duplicate_keys = int(
        metrics.duplicated(["instrument_id", "trade_date"]).sum()
    )
    panel = prices.merge(
        metrics,
        on=["instrument_id", "trade_date"],
        how="left",
        validate="one_to_one" if not metric_duplicate_keys else "many_to_many",
    )
    panel.loc[
        panel["limit_status"].isna(),
        ["instrument_id", "trade_date", "open", "high", "low", "close"],
    ].to_csv(
        output_dir / "missing_limit_status.csv", index=False, encoding="utf-8-sig"
    )
    gap_events, gap_audit = build_gap_events(prices, calendar)
    gap_events.head(500).to_csv(
        output_dir / "largest_price_gaps.csv", index=False, encoding="utf-8-sig"
    )

    state_paths = {
        "name_history": Path(args.name_history).resolve() if args.name_history else data_root / EXPECTED_STATE_TABLES["name_history"]["default"],
        "suspensions": Path(args.suspensions).resolve() if args.suspensions else data_root / EXPECTED_STATE_TABLES["suspensions"]["default"],
        "daily_limits": Path(args.daily_limits).resolve() if args.daily_limits else data_root / EXPECTED_STATE_TABLES["daily_limits"]["default"],
    }
    state_tables = {
        name: inspect_state_table(path, EXPECTED_STATE_TABLES[name]["required"])
        for name, path in state_paths.items()
    }

    exact_limit_audit: dict[str, Any] = {
        "missing_or_invalid_price_rows": len(panel),
        "coverage_rate": 0.0,
    }
    if state_tables["daily_limits"]["ready"]:
        exact_panel, exact_limit_audit = audit_exact_daily_limits(
            panel, state_paths["daily_limits"]
        )
        exact_panel.loc[
            ~exact_panel["exact_limit_known"],
            ["instrument_id", "trade_date", "open", "high", "low", "close"],
        ].to_csv(
            output_dir / "missing_exact_daily_limits.csv",
            index=False,
            encoding="utf-8-sig",
        )
        del exact_panel

    name_history_audit: dict[str, Any] = {
        "missing_price_rows": len(prices),
        "coverage_rate": 0.0,
        "duplicate_keys": 0,
        "overlapping_intervals": 0,
    }
    if state_tables["name_history"]["ready"]:
        name_history_audit = audit_name_history(
            prices, state_paths["name_history"]
        )

    gap_coverage_audit: dict[str, Any] = {
        "fully_explained_gap_events": 0,
        "partially_or_unexplained_gap_events": len(gap_events),
        "suspension_covered_sessions": 0,
        "unknown_sessions": int(gap_events["missing_open_sessions"].sum()),
    }
    if state_tables["suspensions"]["ready"]:
        annotated_gaps, gap_coverage_audit = audit_gap_coverage(
            gap_events,
            state_paths["suspensions"],
            calendar,
        )
        annotated_gaps.loc[annotated_gaps["unknown_sessions"] > 0].to_csv(
            output_dir / "unexplained_price_gaps.csv",
            index=False,
            encoding="utf-8-sig",
        )

    blockers: list[str] = []
    if manifest.get("status") != "COMPLETE":
        blockers.append("历史行情 manifest 不是 COMPLETE")
    if price_duplicate_keys:
        blockers.append(f"daily_prices 有 {price_duplicate_keys:,} 个重复主键")
    if metric_duplicate_keys:
        blockers.append(f"daily_metrics 有 {metric_duplicate_keys:,} 个重复主键")
    for name, spec in EXPECTED_STATE_TABLES.items():
        audit = state_tables[name]
        if not audit["ready"]:
            blockers.append(f"缺少{spec['purpose']}表 `{name}`")

    limit_audit = audit_limit_status(panel)
    if exact_limit_audit["missing_or_invalid_price_rows"]:
        blockers.append(
            f"有 {exact_limit_audit['missing_or_invalid_price_rows']:,} 行行情缺少有效精确涨跌停价"
        )
    if exact_limit_audit.get("duplicate_keys"):
        blockers.append(
            f"daily_limits 有 {exact_limit_audit['duplicate_keys']:,} 个重复主键"
        )
    if name_history_audit["missing_price_rows"]:
        blockers.append(
            f"有 {name_history_audit['missing_price_rows']:,} 行行情无法匹配历史简称/ST状态"
        )
    if name_history_audit["duplicate_keys"]:
        blockers.append(
            f"name_history 有 {name_history_audit['duplicate_keys']:,} 个重复状态键"
        )
    if name_history_audit["overlapping_intervals"]:
        blockers.append(
            f"name_history 有 {name_history_audit['overlapping_intervals']:,} 个重叠区间"
        )
    if gap_coverage_audit["unknown_sessions"]:
        blockers.append(
            f"行情缺口仍有 {gap_coverage_audit['unknown_sessions']:,} 个开市日无法由停牌区间解释"
        )

    latest_names = instruments["name_zh"].fillna("").astype(str)
    summary = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "decision": "READY" if not blockers else "BLOCKED",
        "ready_for_v5_labels": not blockers,
        "blockers": blockers,
        "start_date": str(start.date()),
        "data_as_of": manifest.get("ended_range"),
        "history_manifest": manifest,
        "state_tables": state_tables,
        "prices": {
            "rows": int(len(prices)),
            "valid_quality_rows": int(prices["is_quality_valid"].fillna(False).sum()),
            "duplicate_keys": price_duplicate_keys,
            "date_min": str(prices["trade_date"].min().date()),
            "date_max": str(prices["trade_date"].max().date()),
        },
        "metrics": {
            "rows": int(len(metrics)),
            "duplicate_keys": metric_duplicate_keys,
        },
        "limit_status": limit_audit,
        "exact_daily_limits": exact_limit_audit,
        "name_history": name_history_audit,
        "price_gaps": gap_audit,
        "price_gap_coverage": gap_coverage_audit,
        "instruments": {
            "rows": int(len(instruments)),
            "current_st_names": int(latest_names.str.contains("ST", case=False).sum()),
            "warning": "name_zh is an as-of snapshot, not a historical state",
        },
    }
    write_json(output_dir / "summary.json", summary)
    write_report(output_dir / "report.md", summary)
    print(
        f"Historical state audit: {summary['decision']} -> {output_dir}",
        flush=True,
    )
    if blockers and args.fail_on_blocker:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
