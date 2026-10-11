"""Audit demo02 Top20 control monitor as a demo01 V5 entry condition.

This is a historical rule audit, not a new untouched holdout.  Demo02 control
scores and demo01 V5 features have both already been inspected through
2026-08-11.  The only clean confirmation period starts after that date.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backtest_mfe_mae import load_config, resolve_from
from v5_feature_information_audit import (
    json_value,
    load_samples,
    newey_west_mean_test,
    writable_output_dir,
    write_json,
)
from v5_frozen_composite_audit import FROZEN_COMPOSITES, directed_composite_score


CONTROL_TOP_FRACTION = 0.20
CONTROL_MAX_AGE_DAYS = 120
MIN_CONTROL_COHORT = 20
REWARD_TOP_FRACTION = 0.10
MIN_ESTIMATED_PL_RATIO = 1.20
CALIBRATION_SHRINKAGE = 200.0
CALIBRATION_EMBARGO_DAYS = 45
RISK_PER_TRADE = 0.005
MAX_POSITION_WEIGHT = 0.15
DEVELOPMENT_YEARS = (2023, 2024)
DIAGNOSTIC_YEARS = (2025, 2026)


def build_control_updates(
    scored_events: pd.DataFrame,
    instruments: pd.DataFrame,
    minimum_cohort: int = MIN_CONTROL_COHORT,
) -> pd.DataFrame:
    required = {
        "ann_date",
        "vendor_symbol",
        "discovery_selected_behavior_score",
    }
    missing = required.difference(scored_events.columns)
    if missing:
        raise ValueError(f"control events missing columns: {sorted(missing)}")
    frame = scored_events[list(required)].dropna().copy()
    frame["ann_date"] = pd.to_datetime(frame["ann_date"])
    frame = frame.sort_values(
        ["ann_date", "discovery_selected_behavior_score", "vendor_symbol"],
        ascending=[True, False, True],
        kind="stable",
    ).drop_duplicates(["ann_date", "vendor_symbol"], keep="first")
    parts = []
    for _, group in frame.groupby("ann_date", sort=True):
        if len(group) < minimum_cohort:
            continue
        count = max(1, int(math.ceil(len(group) * CONTROL_TOP_FRACTION)))
        ranked = group.copy()
        ranked["control_top20"] = False
        ranked.iloc[:count, ranked.columns.get_loc("control_top20")] = True
        ranked["control_cohort_rows"] = len(group)
        ranked["control_rank_percentile"] = ranked[
            "discovery_selected_behavior_score"
        ].rank(pct=True, method="average")
        parts.append(ranked)
    if not parts:
        raise RuntimeError("No control announcement cohorts meet the minimum size")
    updates = pd.concat(parts, ignore_index=True)
    mapping = instruments[["vendor_symbol", "instrument_id"]].drop_duplicates(
        "vendor_symbol"
    )
    updates = updates.merge(
        mapping, on="vendor_symbol", how="inner", validate="many_to_one"
    )
    return updates.sort_values(["instrument_id", "ann_date"], kind="stable").reset_index(
        drop=True
    )


def attach_control_pool(
    samples: pd.DataFrame,
    updates: pd.DataFrame,
    maximum_age_days: int = CONTROL_MAX_AGE_DAYS,
) -> pd.DataFrame:
    result = samples.copy()
    result["control_announcement_date"] = pd.NaT
    result["control_score"] = np.nan
    result["control_rank_percentile"] = np.nan
    result["control_age_days"] = np.nan
    result["control_pool_top20"] = False
    update_groups = updates.groupby("instrument_id", sort=False).indices
    for instrument_id, positions in result.groupby("instrument_id", sort=False).indices.items():
        update_positions = update_groups.get(instrument_id)
        if update_positions is None:
            continue
        sample_pos = np.asarray(positions, dtype=np.int64)
        update_pos = np.asarray(update_positions, dtype=np.int64)
        signal_dates = result.loc[sample_pos, "signal_date"].to_numpy(
            dtype="datetime64[ns]"
        )
        disclosure_dates = updates.loc[update_pos, "ann_date"].to_numpy(
            dtype="datetime64[ns]"
        )
        indices = np.searchsorted(disclosure_dates, signal_dates, side="right") - 1
        valid = indices >= 0
        if not valid.any():
            continue
        target_pos = sample_pos[valid]
        chosen_updates = update_pos[indices[valid]]
        chosen_dates = updates.loc[chosen_updates, "ann_date"].to_numpy(
            dtype="datetime64[ns]"
        )
        ages = (signal_dates[valid] - chosen_dates).astype("timedelta64[D]").astype(float)
        fresh = ages <= maximum_age_days
        result.loc[target_pos, "control_announcement_date"] = chosen_dates
        result.loc[target_pos, "control_score"] = updates.loc[
            chosen_updates, "discovery_selected_behavior_score"
        ].to_numpy(dtype=float)
        result.loc[target_pos, "control_rank_percentile"] = updates.loc[
            chosen_updates, "control_rank_percentile"
        ].to_numpy(dtype=float)
        result.loc[target_pos, "control_age_days"] = ages
        result.loc[target_pos, "control_pool_top20"] = (
            updates.loc[chosen_updates, "control_top20"].to_numpy(dtype=bool) & fresh
        )
    return result


def add_v5_scores(samples: pd.DataFrame) -> pd.DataFrame:
    result = samples.copy()
    for target, items in FROZEN_COMPOSITES.items():
        columns = [feature for feature, _ in items]
        ranks = result.groupby("signal_date", sort=False)[columns].rank(
            pct=True, method="average"
        )
        for feature, direction in items:
            if direction == "LOW":
                ranks[feature] = 1.0 - ranks[feature]
            elif direction != "HIGH":
                raise ValueError(f"Unsupported direction: {direction}")
        score = ranks.mean(axis=1)
        result[f"{target}_composite"] = score
        result[f"{target}_percentile"] = result.groupby(
            "signal_date", sort=False
        )[f"{target}_composite"].rank(pct=True, method="average")
    return result


def _calibration_table(train: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, float]]:
    work = train.copy()
    work["reward_bin"] = np.ceil(work["net_r_percentile"] * 10).clip(1, 10).astype(int)
    work["safety_bin"] = np.ceil(work["tail_loss_r_percentile"] * 10).clip(1, 10).astype(int)
    work["upside_component"] = work["v5_net_r"].clip(lower=0.0)
    work["downside_component"] = (-work["v5_net_r"]).clip(lower=0.0)
    global_values = {
        "upside": float(work["upside_component"].mean()),
        "downside": float(work["downside_component"].mean()),
        "tail": float(work["v5_tail_loss_r"].mean()),
    }
    table = work.groupby(["reward_bin", "safety_bin"], observed=True).agg(
        rows=("v5_net_r", "size"),
        upside_sum=("upside_component", "sum"),
        downside_sum=("downside_component", "sum"),
        tail_sum=("v5_tail_loss_r", "sum"),
    ).reset_index()
    denominator = table["rows"] + CALIBRATION_SHRINKAGE
    table["estimated_upside_r"] = (
        table["upside_sum"] + CALIBRATION_SHRINKAGE * global_values["upside"]
    ) / denominator
    table["estimated_downside_r"] = (
        table["downside_sum"] + CALIBRATION_SHRINKAGE * global_values["downside"]
    ) / denominator
    table["estimated_tail_loss_r"] = (
        table["tail_sum"] + CALIBRATION_SHRINKAGE * global_values["tail"]
    ) / denominator
    return table[
        [
            "reward_bin",
            "safety_bin",
            "rows",
            "estimated_upside_r",
            "estimated_downside_r",
            "estimated_tail_loss_r",
        ]
    ], global_values


def add_walkforward_pl_estimates(samples: pd.DataFrame) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    result = samples.copy()
    for column in (
        "estimated_upside_r",
        "estimated_downside_r",
        "estimated_tail_loss_r",
        "estimated_pl_ratio",
        "estimated_expectancy_r",
    ):
        result[column] = np.nan
    result["reward_bin"] = np.ceil(result["net_r_percentile"] * 10).clip(1, 10).astype(int)
    result["safety_bin"] = np.ceil(result["tail_loss_r_percentile"] * 10).clip(1, 10).astype(int)
    audits = []
    for evaluation_year in (2023, 2024, 2025, 2026):
        calibration_year = 2024 if evaluation_year >= 2025 else evaluation_year - 1
        cutoff = pd.Timestamp(f"{calibration_year}-12-31") - pd.Timedelta(
            days=CALIBRATION_EMBARGO_DAYS
        )
        train = result[
            (result["signal_date"].dt.year >= 2022)
            & (result["signal_date"] <= cutoff)
        ]
        evaluation_mask = result["signal_date"].dt.year == evaluation_year
        if train.empty or not evaluation_mask.any():
            continue
        table, global_values = _calibration_table(train)
        evaluation = result.loc[evaluation_mask, ["reward_bin", "safety_bin"]].copy()
        evaluation["_position"] = np.flatnonzero(evaluation_mask)
        evaluation = evaluation.merge(
            table,
            on=["reward_bin", "safety_bin"],
            how="left",
            validate="many_to_one",
        )
        positions = evaluation["_position"].to_numpy(dtype=np.int64)
        for column in (
            "estimated_upside_r",
            "estimated_downside_r",
            "estimated_tail_loss_r",
        ):
            values = evaluation[column].fillna(
                global_values[
                    {
                        "estimated_upside_r": "upside",
                        "estimated_downside_r": "downside",
                        "estimated_tail_loss_r": "tail",
                    }[column]
                ]
            )
            result.iloc[positions, result.columns.get_loc(column)] = values.to_numpy(
                dtype=float
            )
        audits.append(
            {
                "evaluation_year": evaluation_year,
                "calibration_cutoff": str(cutoff.date()),
                "training_rows": int(len(train)),
                "calibration_cells": int(len(table)),
                "global_upside_r": global_values["upside"],
                "global_downside_r": global_values["downside"],
                "global_tail_loss_r": global_values["tail"],
            }
        )
    result["estimated_pl_ratio"] = result["estimated_upside_r"] / result[
        "estimated_downside_r"
    ].clip(lower=1e-6)
    result["estimated_expectancy_r"] = (
        result["estimated_upside_r"] - result["estimated_downside_r"]
    )
    result["demo01_pl_gate"] = (
        (result["net_r_percentile"] >= 1.0 - REWARD_TOP_FRACTION)
        & (result["estimated_pl_ratio"] >= MIN_ESTIMATED_PL_RATIO)
        & (result["estimated_expectancy_r"] > 0)
    )
    result["reward_top10_gate"] = (
        result["net_r_percentile"] >= 1.0 - REWARD_TOP_FRACTION
    )
    result["tail_safe_top10_gate"] = (
        result["tail_loss_r_percentile"] >= 1.0 - REWARD_TOP_FRACTION
    )
    result["dual_top10_gate"] = (
        result["reward_top10_gate"] & result["tail_safe_top10_gate"]
    )
    return result, audits


def trade_statistics(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "trades": 0,
            "mean_net_return": math.nan,
            "mean_net_r": math.nan,
            "win_rate": math.nan,
            "payoff_ratio": math.nan,
            "profit_factor": math.nan,
            "mean_tail_loss_r": math.nan,
            "p95_tail_loss_r": math.nan,
        }
    returns = frame["v5_net_return"].to_numpy(dtype=float)
    r_values = frame["v5_net_r"].to_numpy(dtype=float)
    wins = r_values[r_values > 0]
    losses = r_values[r_values < 0]
    gross_win = float(wins.sum()) if len(wins) else 0.0
    gross_loss = float(-losses.sum()) if len(losses) else 0.0
    payoff = (
        float(wins.mean() / -losses.mean())
        if len(wins) and len(losses)
        else math.nan
    )
    return {
        "trades": int(len(frame)),
        "mean_net_return": float(returns.mean()),
        "mean_net_r": float(r_values.mean()),
        "win_rate": float((returns > 0).mean()),
        "payoff_ratio": payoff,
        "profit_factor": gross_win / gross_loss if gross_loss > 0 else math.inf,
        "mean_tail_loss_r": float(frame["v5_tail_loss_r"].mean()),
        "p95_tail_loss_r": float(frame["v5_tail_loss_r"].quantile(0.95)),
    }


def cohort_metrics(samples: pd.DataFrame) -> pd.DataFrame:
    records = []
    masks = {
        "reward_top10": samples["reward_top10_gate"],
        "reward_top10_plus_control_top20": (
            samples["reward_top10_gate"] & samples["control_pool_top20"]
        ),
        "tail_safe_top10": samples["tail_safe_top10_gate"],
        "tail_safe_top10_plus_control_top20": (
            samples["tail_safe_top10_gate"] & samples["control_pool_top20"]
        ),
        "dual_top10": samples["dual_top10_gate"],
        "dual_top10_plus_control_top20": (
            samples["dual_top10_gate"] & samples["control_pool_top20"]
        ),
        "demo01_pl_gate": samples["demo01_pl_gate"],
        "demo01_pl_gate_plus_control_top20": samples["control_entry_gate"],
    }
    periods = {
        "development_2023_2024": samples["signal_date"].dt.year.isin(DEVELOPMENT_YEARS),
        "diagnostic_2025_2026": samples["signal_date"].dt.year.isin(DIAGNOSTIC_YEARS),
    }
    for period, period_mask in periods.items():
        for strategy, strategy_mask in masks.items():
            stats = trade_statistics(samples[period_mask & strategy_mask])
            records.append({"period": period, "strategy": strategy, **stats})
        for year in (*DEVELOPMENT_YEARS, *DIAGNOSTIC_YEARS):
            if year not in (DEVELOPMENT_YEARS if period.startswith("development") else DIAGNOSTIC_YEARS):
                continue
            year_mask = samples["signal_date"].dt.year == year
            for strategy, strategy_mask in masks.items():
                stats = trade_statistics(samples[year_mask & strategy_mask])
                records.append(
                    {"period": str(year), "strategy": strategy, **stats}
                )
    return pd.DataFrame(records)


def matched_daily_comparison(
    samples: pd.DataFrame,
    baseline_column: str,
    overlay_column: str,
    comparison_name: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows = []
    for date, group in samples.groupby("signal_date", sort=True):
        baseline = group[group[baseline_column]].sort_values(
            ["estimated_pl_ratio", "net_r_percentile", "instrument_id"],
            ascending=[False, False, True],
            kind="stable",
        )
        overlay = group[group[overlay_column]].sort_values(
            ["estimated_pl_ratio", "net_r_percentile", "instrument_id"],
            ascending=[False, False, True],
            kind="stable",
        )
        if overlay.empty:
            continue
        matched = baseline.head(len(overlay))
        rows.append(
            {
                "signal_date": date,
                "comparison": comparison_name,
                "year": int(date.year),
                "overlay_rows": int(len(overlay)),
                "overlay_mean_net_r": float(overlay["v5_net_r"].mean()),
                "matched_baseline_mean_net_r": float(matched["v5_net_r"].mean()),
                "overlay_mean_net_return": float(overlay["v5_net_return"].mean()),
                "matched_baseline_mean_net_return": float(
                    matched["v5_net_return"].mean()
                ),
            }
        )
    daily = pd.DataFrame(rows)
    if daily.empty:
        return daily, {"days": 0}
    daily["net_r_difference"] = (
        daily["overlay_mean_net_r"] - daily["matched_baseline_mean_net_r"]
    )
    daily["net_return_difference"] = (
        daily["overlay_mean_net_return"]
        - daily["matched_baseline_mean_net_return"]
    )
    summary: dict[str, Any] = {}
    for name, years in {
        "development_2023_2024": DEVELOPMENT_YEARS,
        "diagnostic_2025_2026": DIAGNOSTIC_YEARS,
    }.items():
        period = daily[daily["year"].isin(years)]
        r_mean, r_t, r_p = newey_west_mean_test(period["net_r_difference"])
        ret_mean, ret_t, ret_p = newey_west_mean_test(
            period["net_return_difference"]
        )
        summary[name] = {
            "days": int(len(period)),
            "mean_net_r_difference": r_mean,
            "net_r_hac_t": r_t,
            "net_r_hac_p": r_p,
            "mean_net_return_difference": ret_mean,
            "net_return_hac_t": ret_t,
            "net_return_hac_p": ret_p,
            "overlay_better_day_rate": float(
                (period["net_r_difference"] > 0).mean()
            ) if len(period) else math.nan,
        }
    return daily, summary


def simulate_single_position(
    samples: pd.DataFrame, eligibility_column: str
) -> tuple[pd.DataFrame, dict[str, Any], pd.DataFrame]:
    eligible = samples[
        samples[eligibility_column] & (samples["signal_date"].dt.year >= 2023)
    ].copy()
    trades = []
    next_free_idx = -1
    equity = 1.0
    peak = 1.0
    maximum_drawdown = 0.0
    for date, group in eligible.groupby("signal_date", sort=True):
        available = group[group["trade_idx"] >= next_free_idx]
        if available.empty:
            continue
        chosen = available.sort_values(
            ["estimated_pl_ratio", "net_r_percentile", "instrument_id"],
            ascending=[False, False, True],
            kind="stable",
        ).iloc[0]
        weight = min(
            MAX_POSITION_WEIGHT,
            RISK_PER_TRADE / max(float(chosen["v5_stop_pct"]), 1e-6),
        )
        portfolio_return = weight * float(chosen["v5_net_return"])
        equity *= 1.0 + portfolio_return
        peak = max(peak, equity)
        maximum_drawdown = min(maximum_drawdown, equity / peak - 1.0)
        next_free_idx = int(chosen["v5_exit_idx"])
        trades.append(
            {
                "signal_date": date,
                "instrument_id": chosen["instrument_id"],
                "trade_idx": int(chosen["trade_idx"]),
                "exit_idx": int(chosen["v5_exit_idx"]),
                "estimated_pl_ratio": float(chosen["estimated_pl_ratio"]),
                "control_age_days": float(chosen["control_age_days"])
                if np.isfinite(chosen["control_age_days"])
                else math.nan,
                "position_weight": weight,
                "stock_net_return": float(chosen["v5_net_return"]),
                "net_r": float(chosen["v5_net_r"]),
                "portfolio_return": portfolio_return,
                "equity": equity,
            }
        )
    trade_frame = pd.DataFrame(trades)
    stats = trade_statistics(
        eligible.loc[
            eligible.set_index(["signal_date", "instrument_id"]).index.isin(
                trade_frame.set_index(["signal_date", "instrument_id"]).index
            )
        ]
    ) if not trade_frame.empty else trade_statistics(pd.DataFrame())
    summary = {
        **stats,
        "total_return": equity - 1.0,
        "maximum_closed_trade_drawdown": maximum_drawdown,
    }
    yearly_rows = []
    if not trade_frame.empty:
        for year, group in trade_frame.groupby(trade_frame["signal_date"].dt.year):
            yearly_rows.append(
                {
                    "year": int(year),
                    "trades": int(len(group)),
                    "return": float(np.prod(1.0 + group["portfolio_return"]) - 1.0),
                    "mean_net_r": float(group["net_r"].mean()),
                }
            )
    return trade_frame, summary, pd.DataFrame(yearly_rows)


def render_report(summary: dict[str, Any], cohort: pd.DataFrame, yearly: pd.DataFrame) -> str:
    decision = summary["decision"]
    return "\n".join(
        [
            "# V6控盘Top20监控池 × V5特征盈亏比审计",
            "",
            f"**结论：{decision['status']}**",
            "",
            decision["detail"],
            "",
            "## 冻结入场规则",
            "",
            "- demo02控盘行为评分同公告日Top20%，公告日起生效；下一次低评分公告会移出，最长保留120天。",
            "- demo01沿用V5冻结净R与尾部风险等权组合，不训练新模型。",
            "- 只使用当时之前且留出45天标签成熟期的数据，将两个组合十分位映射为估计盈利R和亏损R。",
            "- 收益特征位于同日Top10%、估计盈亏比不低于1.2且估计期望R为正，才算demo01入场达标。",
            "- 主测试在上述条件上增加控盘Top20监控池条件。",
            "",
            "## 候选级结果",
            "",
            cohort.to_markdown(index=False, floatfmt=".4f"),
            "",
            "## 单持仓逐年结果",
            "",
            yearly.to_markdown(index=False, floatfmt=".4f"),
            "",
            "## 解释限制",
            "",
            "2025—2026已经被demo01和demo02先前研究查看，当前只能称历史诊断，不能称新的独立留出验证。",
            "即使历史结果通过，也必须等待2026-08-11之后的新数据作只读前向验证。",
            "",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json")
    parser.add_argument(
        "--control-events",
        default=None,
        help="Optional scored control-event parquet; defaults to the configured legacy artifact.",
    )
    parser.add_argument(
        "--demo02-dir",
        default=None,
        help="Deprecated compatibility path to a legacy demo02 directory.",
    )
    parser.add_argument("--output-dir", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parent
    config = load_config(resolve_from(root, args.config))
    data_root = resolve_from(root, str(config["data_root"]))
    output_root = resolve_from(root, str(config["output_dir"]))
    output_dir = writable_output_dir(
        Path(args.output_dir).resolve()
        if args.output_dir
        else output_root / "v6_control_monitor_entry_audit"
    )
    label_path = resolve_from(
        root,
        str(
            config.get(
                "legacy_v5_label_path",
                "output/v5_tail_label_audit/v5_tail_labels.parquet",
            )
        ),
    )

    samples, sample_audit = load_samples(
        data_root, pd.Timestamp(str(config["feature_start"])), label_path
    )
    label_extra = pd.read_parquet(
        label_path,
        columns=[
            "instrument_id",
            "trade_date",
            "v5_net_return",
            "v5_stop_pct",
            "v5_exit_idx",
        ],
    ).rename(columns={"trade_date": "signal_date"})
    label_extra["signal_date"] = pd.to_datetime(label_extra["signal_date"])
    samples = samples.merge(
        label_extra,
        on=["instrument_id", "signal_date"],
        how="left",
        validate="one_to_one",
    )
    samples = add_v5_scores(samples)
    samples, calibration_audit = add_walkforward_pl_estimates(samples)

    if args.control_events:
        control_events_path = resolve_from(root, args.control_events)
    elif args.demo02_dir:
        control_events_path = (
            resolve_from(root, args.demo02_dir)
            / "output"
            / "control_state"
            / "scored_events.parquet"
        )
    else:
        control_events_path = resolve_from(
            root,
            str(
                config.get(
                    "legacy_control_events_path",
                    "output/control_state/scored_events.parquet",
                )
            ),
        )
    control_events = pd.read_parquet(
        control_events_path,
        columns=[
            "ann_date",
            "vendor_symbol",
            "discovery_selected_behavior_score",
        ],
    )
    instruments = pd.read_parquet(
        data_root / "instruments.parquet",
        columns=["instrument_id", "vendor_symbol"],
    )
    updates = build_control_updates(control_events, instruments)
    samples = attach_control_pool(samples, updates)
    samples["control_entry_gate"] = (
        samples["demo01_pl_gate"] & samples["control_pool_top20"]
    )
    samples["reward_top10_control_gate"] = (
        samples["reward_top10_gate"] & samples["control_pool_top20"]
    )
    samples["tail_safe_top10_control_gate"] = (
        samples["tail_safe_top10_gate"] & samples["control_pool_top20"]
    )
    samples["dual_top10_control_gate"] = (
        samples["dual_top10_gate"] & samples["control_pool_top20"]
    )

    cohort = cohort_metrics(samples)
    daily_parts = []
    paired_summary = {}
    for name, baseline_column, overlay_column in (
        ("reward_top10", "reward_top10_gate", "reward_top10_control_gate"),
        ("dual_top10", "dual_top10_gate", "dual_top10_control_gate"),
        ("pl_1_2", "demo01_pl_gate", "control_entry_gate"),
    ):
        daily_part, comparison_summary = matched_daily_comparison(
            samples, baseline_column, overlay_column, name
        )
        if not daily_part.empty:
            daily_parts.append(daily_part)
        paired_summary[name] = comparison_summary
    daily_comparison = (
        pd.concat(daily_parts, ignore_index=True) if daily_parts else pd.DataFrame()
    )
    trade_parts = []
    yearly_parts = []
    portfolio_summary = {}
    for strategy, column in {
        "reward_top10": "reward_top10_gate",
        "reward_top10_plus_control_top20": "reward_top10_control_gate",
        "dual_top10": "dual_top10_gate",
        "dual_top10_plus_control_top20": "dual_top10_control_gate",
        "demo01_pl_gate": "demo01_pl_gate",
        "demo01_pl_gate_plus_control_top20": "control_entry_gate",
    }.items():
        trades, stats, yearly = simulate_single_position(samples, column)
        trades["strategy"] = strategy
        yearly["strategy"] = strategy
        trade_parts.append(trades)
        yearly_parts.append(yearly)
        portfolio_summary[strategy] = stats
    trades = pd.concat(trade_parts, ignore_index=True)
    yearly = pd.concat(yearly_parts, ignore_index=True)

    development_base = cohort[
        (cohort["period"] == "development_2023_2024")
        & (cohort["strategy"] == "demo01_pl_gate")
    ].iloc[0]
    development_overlay = cohort[
        (cohort["period"] == "development_2023_2024")
        & (cohort["strategy"] == "demo01_pl_gate_plus_control_top20")
    ].iloc[0]
    diagnostic_base = cohort[
        (cohort["period"] == "diagnostic_2025_2026")
        & (cohort["strategy"] == "demo01_pl_gate")
    ].iloc[0]
    diagnostic_overlay = cohort[
        (cohort["period"] == "diagnostic_2025_2026")
        & (cohort["strategy"] == "demo01_pl_gate_plus_control_top20")
    ].iloc[0]
    passes = bool(
        development_overlay["trades"] >= 30
        and diagnostic_overlay["trades"] >= 30
        and development_overlay["mean_net_r"] > 0
        and diagnostic_overlay["mean_net_r"] > 0
        and development_overlay["profit_factor"] >= MIN_ESTIMATED_PL_RATIO
        and diagnostic_overlay["profit_factor"] >= MIN_ESTIMATED_PL_RATIO
        and development_overlay["mean_net_r"] > development_base["mean_net_r"]
        and diagnostic_overlay["mean_net_r"] > diagnostic_base["mean_net_r"]
        and paired_summary["pl_1_2"].get("development_2023_2024", {}).get(
            "mean_net_r_difference", math.nan
        ) > 0
        and paired_summary["pl_1_2"].get("diagnostic_2025_2026", {}).get(
            "mean_net_r_difference", math.nan
        ) > 0
    )
    no_pl_candidates = bool(
        development_base["trades"] == 0 and diagnostic_base["trades"] == 0
    )
    decision = {
        "status": (
            "NO_ENTRY_AT_PL_1_2"
            if no_pl_candidates
            else ("HISTORICAL_BENEFIT_ONLY" if passes else "NO_INCREMENTAL_BENEFIT")
        ),
        "passes_predeclared_historical_gate": passes,
        "detail": (
            "控盘Top20过滤在开发与历史诊断期均改善盈亏质量；只授权前向观察。"
            if passes
            else (
                "demo01冻结特征没有候选达到估计盈亏比1.2和正期望，控盘条件无法挽救零入场规则；分解对照仅用于判断过滤方向。"
                if no_pl_candidates
                else "控盘Top20过滤没有同时改善开发期与历史诊断期的盈亏质量，不作为入场条件。"
            )
        ),
        "not_an_untouched_holdout": True,
    }
    summary = {
        "decision": decision,
        "rules": {
            "control_top_fraction": CONTROL_TOP_FRACTION,
            "control_max_age_days": CONTROL_MAX_AGE_DAYS,
            "minimum_control_announcement_cohort": MIN_CONTROL_COHORT,
            "reward_top_fraction": REWARD_TOP_FRACTION,
            "minimum_estimated_pl_ratio": MIN_ESTIMATED_PL_RATIO,
            "calibration_shrinkage_rows": CALIBRATION_SHRINKAGE,
            "calibration_embargo_days": CALIBRATION_EMBARGO_DAYS,
        },
        "sample_audit": sample_audit,
        "control_updates": {
            "rows": int(len(updates)),
            "high_rows": int(updates["control_top20"].sum()),
            "instruments": int(updates["instrument_id"].nunique()),
            "monitor_sample_rows": int(samples["control_pool_top20"].sum()),
        },
        "calibration_audit": calibration_audit,
        "paired_daily_comparison": paired_summary,
        "single_position": portfolio_summary,
    }
    cohort.to_csv(output_dir / "cohort_metrics.csv", index=False, encoding="utf-8-sig")
    daily_comparison.to_parquet(
        output_dir / "matched_daily_comparison.parquet", index=False
    )
    trades.to_csv(output_dir / "single_position_trades.csv", index=False, encoding="utf-8-sig")
    yearly.to_csv(output_dir / "single_position_yearly.csv", index=False, encoding="utf-8-sig")
    updates.to_parquet(output_dir / "control_monitor_updates.parquet", index=False)
    monitoring_columns = [
        "signal_date",
        "instrument_id",
        "control_announcement_date",
        "control_age_days",
        "control_rank_percentile",
        "control_pool_top20",
        "net_r_percentile",
        "tail_loss_r_percentile",
        "estimated_pl_ratio",
        "estimated_expectancy_r",
        "reward_top10_gate",
        "tail_safe_top10_gate",
        "dual_top10_gate",
        "demo01_pl_gate",
        "control_entry_gate",
        "v5_net_return",
        "v5_net_r",
        "v5_tail_loss_r",
    ]
    samples.loc[
        samples["demo01_pl_gate"] | samples["control_pool_top20"], monitoring_columns
    ].to_parquet(output_dir / "eligible_monitor_rows.parquet", index=False)
    write_json(output_dir / "summary.json", summary)
    (output_dir / "report.md").write_text(
        render_report(summary, cohort, yearly), encoding="utf-8"
    )
    print(json.dumps(json_value(decision), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
