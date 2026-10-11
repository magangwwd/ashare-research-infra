"""Generate an as-of LOCK_MARKUP_SINGLE_PAPER_V1 state snapshot.

This is a research/paper artifact only.  It combines the strict current state
pool, the point-in-time state transition cache, and demo01's frozen relative
ranking fields.  No broker connectivity or real-order action is present.
"""

from __future__ import annotations

import argparse
import gc
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest_mfe_mae import (  # noqa: E402
    load_config,
    load_panel,
    read_year_partitions,
    resolve_from,
)
from ml_cprr_v2 import add_daily_features  # noqa: E402
from ml_cprr_v3 import FEATURES  # noqa: E402
from v5_executable_tail import add_point_in_time_states  # noqa: E402
from v5_feature_information_audit import (  # noqa: E402
    MIN_AVERAGE_TURNOVER_CNY,
    MIN_LISTING_OBSERVATIONS,
    MIN_RECENT_OBSERVATIONS,
    recent_observation_counts,
)
from v6_control_monitor_entry_audit import add_v5_scores  # noqa: E402


STATE_CN = {
    "accumulation": "吸筹",
    "lock": "锁仓",
    "markup": "拉升",
    "distribution": "派发",
    "uncertain": "不适合定义",
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


def build_current_demo01_scores(as_of: pd.Timestamp) -> tuple[pd.DataFrame, dict[str, Any]]:
    config = load_config(ROOT / "config.json")
    data_root = resolve_from(ROOT, str(config["data_root"]))
    feature_start = pd.Timestamp(str(config["feature_start"]))
    panel, _, _, panel_audit = load_panel(data_root, feature_start)
    start_year = feature_start.year
    end_year = int(panel["trade_date"].dt.year.max())
    turnover = read_year_partitions(
        data_root,
        "daily_metrics",
        start_year,
        end_year,
        ["instrument_id", "trade_date", "turnover_rate_pct"],
    )
    turnover["trade_date"] = pd.to_datetime(turnover["trade_date"])
    panel = panel.merge(
        turnover,
        on=["instrument_id", "trade_date"],
        how="left",
        validate="one_to_one",
    )
    del turnover
    panel, state_audit = add_point_in_time_states(panel, data_root)
    panel = panel.sort_values(["instrument_id", "trade_idx"]).reset_index(drop=True)
    group = panel.groupby("instrument_id", sort=False)
    panel["listing_observations"] = (group.cumcount() + 1).astype("int32")
    panel["average_turnover_cny_20"] = group["turnover_cny"].transform(
        lambda values: values.rolling(20, min_periods=20).mean()
    )
    panel["recent_observations_20"] = recent_observation_counts(panel)
    panel = add_daily_features(panel)

    current = panel[panel["trade_date"].eq(as_of)].copy()
    finite_features = np.isfinite(
        current[FEATURES].to_numpy(dtype="float32")
    ).all(axis=1)
    eligible = (
        current["listing_observations"].ge(MIN_LISTING_OBSERVATIONS)
        & current["average_turnover_cny_20"].ge(MIN_AVERAGE_TURNOVER_CNY)
        & current["recent_observations_20"].ge(MIN_RECENT_OBSERVATIONS)
        & ~current["historical_is_st"].fillna(True)
        & finite_features
    )
    current = current[eligible].copy()
    current = current.rename(columns={"trade_date": "signal_date"})
    current = add_v5_scores(current)

    calibration_path = resolve_from(
        ROOT,
        str(
            config.get(
                "legacy_transition_candidates_path",
                "output/state_transition_backtest/candidate_rows.parquet",
            )
        ),
    )
    calibration = pd.read_parquet(
        calibration_path,
        columns=[
            "signal_date",
            "net_r_percentile",
            "tail_loss_r_percentile",
            "estimated_pl_ratio",
            "estimated_expectancy_r",
        ],
    )
    calibration["signal_date"] = pd.to_datetime(calibration["signal_date"])
    calibration = calibration[
        calibration["signal_date"].dt.year.ge(2025)
        & calibration["estimated_pl_ratio"].notna()
        & calibration["estimated_expectancy_r"].notna()
    ].copy()
    calibration["reward_bin"] = np.ceil(
        calibration["net_r_percentile"] * 10
    ).clip(1, 10).astype(int)
    calibration["safety_bin"] = np.ceil(
        calibration["tail_loss_r_percentile"] * 10
    ).clip(1, 10).astype(int)
    spread = calibration.groupby(["reward_bin", "safety_bin"], observed=True).agg(
        pl_min=("estimated_pl_ratio", "min"),
        pl_max=("estimated_pl_ratio", "max"),
        expectancy_min=("estimated_expectancy_r", "min"),
        expectancy_max=("estimated_expectancy_r", "max"),
    )
    if (
        (spread["pl_max"] - spread["pl_min"]).max() > 1e-10
        or (spread["expectancy_max"] - spread["expectancy_min"]).max() > 1e-10
    ):
        raise RuntimeError("2025-2026 frozen calibration is not constant within bins")
    lookup = calibration.groupby(
        ["reward_bin", "safety_bin"], observed=True
    ).agg(
        estimated_pl_ratio=("estimated_pl_ratio", "median"),
        estimated_expectancy_r=("estimated_expectancy_r", "median"),
    ).reset_index()
    current["reward_bin"] = np.ceil(current["net_r_percentile"] * 10).clip(
        1, 10
    ).astype(int)
    current["safety_bin"] = np.ceil(
        current["tail_loss_r_percentile"] * 10
    ).clip(1, 10).astype(int)
    current = current.merge(
        lookup,
        on=["reward_bin", "safety_bin"],
        how="left",
        validate="many_to_one",
    )
    current["strict_gate_shadow"] = (
        current["estimated_pl_ratio"].ge(1.2)
        & current["net_r_percentile"].ge(0.9)
        & current["estimated_expectancy_r"].gt(0)
    )
    current["stop_pct"] = (1.5 * current["atr20_pct"]).clip(0.04, 0.08)
    current["target_position_weight"] = np.minimum(0.15, 0.005 / current["stop_pct"])
    keep = [
        "instrument_id",
        "signal_date",
        "close",
        "atr20_pct",
        "stop_pct",
        "target_position_weight",
        "net_r_percentile",
        "tail_loss_r_percentile",
        "estimated_pl_ratio",
        "estimated_expectancy_r",
        "strict_gate_shadow",
        "reward_bin",
        "safety_bin",
    ]
    result = current[keep].copy()
    audit = {
        "as_of_date": str(as_of.date()),
        "panel_date_max": panel_audit["date_max"],
        "eligible_ranking_rows": int(len(result)),
        "calibration_source": str(calibration_path),
        "calibration_cells": int(len(lookup)),
        "point_in_time_state_audit": state_audit,
    }
    del panel, current, calibration
    gc.collect()
    return result, audit


def render_report(summary: dict[str, Any], candidates: pd.DataFrame, top_a: pd.DataFrame) -> str:
    candidate_view = candidates[
        [
            "vendor_symbol",
            "name_zh",
            "industry_vendor",
            "state_cn",
            "confidence",
            "state_score",
            "state_margin",
            "previous_defined_date",
            "transition_gap_days",
            "net_r_percentile",
            "estimated_pl_ratio",
            "atr20_pct",
            "stop_pct",
            "target_position_weight",
            "strict_gate_shadow",
            "decision",
        ]
    ].copy()
    top_view = top_a[
        [
            "vendor_symbol",
            "name_zh",
            "industry_vendor",
            "state_cn",
            "confidence",
            "state_score",
            "state_margin",
            "stable_days_5",
        ]
    ].copy()
    return "\n".join(
        [
            "# 锁仓转拉升 V1 当前状态快照",
            "",
            f"数据截止：{summary['as_of_date']}收盘。策略状态：{summary['strategy_status']}。",
            "",
            "## 当日V1状态候选",
            "",
            candidate_view.to_markdown(index=False, floatfmt=".4f")
            if len(candidate_view)
            else "当日没有同时满足A级与锁仓转拉升的股票。",
            "",
            "## A级状态靠前股票",
            "",
            top_view.to_markdown(index=False, floatfmt=".2f") if len(top_view) else "无。",
            "",
            "## 审计结论",
            "",
            f"- 在市主板 {summary['listed_universe']} 只；A级 {summary['a_pool_count']} 只，"
            f"B级 {summary['b_pool_count']} 只，删除 {summary['removed_count']} 只。",
            f"- 当日锁仓转拉升且属于严格A级的股票 {summary['v1_candidate_count']} 只；"
            f"冻结排序字段完整 {summary['ranking_complete_count']} 只。",
            "- estimated_pl_ratio只用于同日相对排序；严格1.2门槛是影子字段，不是V1入场过滤器。",
            "- 本文件只生成研究/模拟状态，不连接券商，不构成实盘买入授权。",
            "",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--state-snapshot", type=Path, required=True)
    parser.add_argument("--transition-snapshot", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    as_of = pd.Timestamp(args.as_of).normalize()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    state = pd.read_parquet(args.state_snapshot.resolve())
    transitions = pd.read_parquet(args.transition_snapshot.resolve())
    transitions = transitions[
        [
            "instrument_id",
            "previous_defined_state",
            "previous_defined_date",
            "transition_gap_days",
            "transition",
        ]
    ]
    combined = state.merge(
        transitions, on="instrument_id", how="left", validate="one_to_one"
    )
    combined["state_cn"] = combined["defined_state"].map(STATE_CN)
    scores, ranking_audit = build_current_demo01_scores(as_of)
    combined = combined.merge(scores, on="instrument_id", how="left", validate="one_to_one")

    signal_gate = (
        combined["defined_state"].eq("markup")
        & combined["pool_status"].eq("A_STRATEGY_CANDIDATE")
        & combined["transition"].eq("lock_to_markup")
        & combined["previous_defined_state"].eq("lock")
        & combined["transition_gap_days"].between(1, 45)
        & combined["state_score"].ge(65.0)
        & combined["state_margin"].ge(10.0)
        & combined["score_coverage"].ge(0.8)
        & combined["stable_days_5"].ge(3)
        & combined["confidence"].ge(80.0)
        & combined["average_turnover_cny_20"].ge(100_000_000.0)
    )
    candidates = combined[signal_gate].copy()
    ranking_complete = candidates[
        ["estimated_pl_ratio", "net_r_percentile", "atr20_pct"]
    ].notna().all(axis=1)
    candidates["decision"] = np.where(
        ranking_complete,
        "SHADOW_SIGNAL_NO_LIVE_AUTHORIZATION",
        "NO_ENTRY_MISSING_FROZEN_RANKING",
    )
    candidates = candidates.sort_values(
        ["estimated_pl_ratio", "net_r_percentile", "instrument_id"],
        ascending=[False, False, True],
        kind="stable",
        na_position="last",
    ).reset_index(drop=True)
    candidates["candidate_rank"] = np.arange(1, len(candidates) + 1)

    a_pool = combined[combined["pool_status"].eq("A_STRATEGY_CANDIDATE")].sort_values(
        ["confidence", "state_score", "instrument_id"],
        ascending=[False, False, True],
        kind="stable",
    )
    # Show both actionable state families.  A simple global head can otherwise
    # be filled entirely by markup rows and hide every current lock candidate.
    top_a = pd.concat(
        [
            a_pool[a_pool["defined_state"].eq("markup")].head(15),
            a_pool[a_pool["defined_state"].eq("lock")].head(15),
        ],
        ignore_index=True,
    )
    counts = combined["pool_status"].value_counts()
    summary = {
        "as_of_date": str(as_of.date()),
        "strategy_id": "LOCK_MARKUP_SINGLE_PAPER_V1",
        "strategy_status": "PAPER_ONLY_NO_ENTRY_AUTHORIZATION",
        "listed_universe": int(len(combined)),
        "a_pool_count": int(counts.get("A_STRATEGY_CANDIDATE", 0)),
        "b_pool_count": int(counts.get("B_MONITOR_ONLY", 0)),
        "removed_count": int(counts.get("REMOVED", 0)),
        "defined_state_counts": {
            str(key): int(value)
            for key, value in combined["defined_state"].value_counts().items()
        },
        "v1_candidate_count": int(len(candidates)),
        "ranking_complete_count": int(ranking_complete.sum()),
        "ranking_audit": ranking_audit,
        "candidate_symbols": candidates["vendor_symbol"].tolist(),
    }
    combined.to_parquet(output_dir / "current_stock_states.parquet", index=False)
    combined.to_csv(
        output_dir / "current_stock_states.csv", index=False, encoding="utf-8-sig"
    )
    candidates.to_csv(
        output_dir / "v1_signal_candidates.csv", index=False, encoding="utf-8-sig"
    )
    top_a.to_csv(output_dir / "top_a_states.csv", index=False, encoding="utf-8-sig")
    (output_dir / "summary.json").write_text(
        json.dumps(json_value(summary), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "report.md").write_text(
        render_report(summary, candidates, top_a), encoding="utf-8"
    )
    print(json.dumps(json_value(summary), ensure_ascii=False, indent=2))
    if len(candidates):
        print(candidates.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
