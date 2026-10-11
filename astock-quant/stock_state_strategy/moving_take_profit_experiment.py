"""Evaluate a conservative close-watermark trailing exit for lock-to-markup.

The frozen V1 entry, ATR hard stop, costs, ranking and 20-session cap are kept
unchanged.  The V2 primary exit activates after a close reaches +1.5R and, from
the following session onward, trails the highest *completed close* by 1.0R
while locking at least +0.25R gross.  This avoids inventing the order of a
same-day high and low from daily OHLC bars.
"""

from __future__ import annotations

from data_source_paths import resolve_project_path

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from state_exit_take_profit_experiment import (
    BUY_COST_RATE,
    DEVELOPMENT_YEARS,
    DIAGNOSTIC_YEARS,
    SELL_COST_RATE,
    _json_value,
    add_variant,
    build_cohort_metrics,
    load_calendar,
    load_candidate_market,
    load_candidates,
    load_distribution_transitions,
    paired_daily_test,
    simulate_exit,
    simulate_portfolios,
)


ROOT = Path(__file__).resolve().parent
ACTIVATION_R = 1.5
TRAIL_DISTANCE_R = 1.0
MINIMUM_LOCK_R = 0.25
HORIZON = 20


@dataclass(frozen=True)
class TrailingExitOutcome:
    exit_idx: int
    exit_scale: float
    net_return: float
    net_r: float
    tail_loss_r: float
    reason: str
    distribution_signal_idx: int | None
    entry_idx: int
    hold_sessions: int
    activated: bool
    activation_idx: int | None
    peak_close_scale: float
    trail_floor_at_exit: float | None
    giveback_r: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Test a prior-close watermark trailing exit against V1"
    )
    parser.add_argument(
        "--transition-output", default="output/state_transition_backtest"
    )
    parser.add_argument(
        "--market-root", default="../down_data/data/gold/main_board"
    )
    parser.add_argument(
        "--output-dir", default="output/moving_take_profit_experiment"
    )
    return parser.parse_args()


def resolve_from_root(value: str) -> Path:
    return resolve_project_path(ROOT, value)


def _row(stock: pd.DataFrame, trade_idx: int) -> pd.Series:
    value = stock.loc[trade_idx]
    if isinstance(value, pd.DataFrame):
        raise ValueError(f"Duplicate market rows at trade_idx={trade_idx}")
    return value


def simulate_trailing_exit(
    stock: pd.DataFrame,
    signal_idx: int,
    stop_pct: float,
    horizon: int = HORIZON,
    distribution_indices: np.ndarray | None = None,
    use_distribution_exit: bool = False,
    activation_r: float = ACTIVATION_R,
    trail_distance_r: float = TRAIL_DISTANCE_R,
    minimum_lock_r: float = MINIMUM_LOCK_R,
) -> TrailingExitOutcome | None:
    """Simulate the V2 exit using information known before each session opens.

    The trail for session D is frozen from closes through D-1.  A close on D
    may activate or raise the floor only for D+1.  The frozen ATR hard stop is
    never moved or disabled.  On the combined shadow path, executable priority
    is hard-stop gap, distribution-at-open, trail gap, trail intraday, hard
    stop intraday, then the scheduled time close.
    """
    if not 0 < stop_pct < 1:
        raise ValueError("stop_pct must be between zero and one")
    if activation_r <= 0 or trail_distance_r <= 0 or minimum_lock_r < 0:
        raise ValueError("Trailing parameters must be positive")

    entry_idx = signal_idx + 1
    end_idx = signal_idx + horizon + 1
    if entry_idx not in stock.index:
        return None
    entry = _row(stock, entry_idx)
    entry_scale = float(entry["adj_open"])
    if not np.isfinite(entry_scale) or entry_scale <= 0:
        return None

    hard_stop = entry_scale * (1.0 - stop_pct)
    activation_scale = entry_scale * (1.0 + activation_r * stop_pct)
    minimum_floor = entry_scale * (1.0 + minimum_lock_r * stop_pct)
    pending_entry_stop = float(entry["adj_low"]) <= hard_stop
    path_min = float(entry["adj_low"])
    peak_close = float(entry["adj_close"])
    activated = bool(peak_close >= activation_scale)
    activation_idx: int | None = entry_idx if activated else None
    trail_floor: float | None = None
    if activated:
        trail_floor = max(
            minimum_floor,
            peak_close - trail_distance_r * stop_pct * entry_scale,
        )

    distribution_indices = (
        np.empty(0, dtype="int32")
        if distribution_indices is None
        else distribution_indices
    )
    future_distribution = distribution_indices[distribution_indices > signal_idx]
    distribution_idx = (
        int(future_distribution[0])
        if use_distribution_exit and len(future_distribution)
        else None
    )

    exit_idx: int | None = None
    exit_scale: float | None = None
    floor_at_exit: float | None = None
    reason = ""
    for current_idx in range(entry_idx + 1, end_idx + 1):
        if current_idx not in stock.index:
            continue
        current = _row(stock, current_idx)
        sellable = int(current["limit_status"]) != 6
        current_open = float(current["adj_open"])
        current_low = float(current["adj_low"])
        current_close = float(current["adj_close"])

        if pending_entry_stop and sellable:
            exit_idx, exit_scale, reason = (
                current_idx,
                current_open,
                "ENTRY_DAY_STOP_NEXT_OPEN",
            )
            path_min = min(path_min, current_open)
            break
        if sellable and current_open <= hard_stop:
            exit_idx, exit_scale, reason = current_idx, current_open, "GAP_STOP"
            path_min = min(path_min, current_open)
            break
        if sellable and distribution_idx is not None and current_idx > distribution_idx:
            exit_idx, exit_scale, reason = (
                current_idx,
                current_open,
                "DISTRIBUTION_NEXT_OPEN",
            )
            path_min = min(path_min, current_open)
            break
        if sellable and trail_floor is not None and current_open <= trail_floor:
            exit_idx, exit_scale, reason = current_idx, current_open, "TRAIL_GAP_OPEN"
            floor_at_exit = trail_floor
            path_min = min(path_min, current_open)
            break
        if sellable and trail_floor is not None and current_low <= trail_floor:
            exit_idx, exit_scale, reason = (
                current_idx,
                trail_floor,
                "TRAIL_INTRADAY",
            )
            floor_at_exit = trail_floor
            path_min = min(path_min, trail_floor)
            break
        if sellable and current_low <= hard_stop:
            exit_idx, exit_scale, reason = current_idx, hard_stop, "INTRADAY_STOP"
            path_min = min(path_min, hard_stop)
            break

        path_min = min(path_min, current_low)
        peak_close = max(peak_close, current_close)
        if not activated and peak_close >= activation_scale:
            activated = True
            activation_idx = current_idx
        if activated:
            next_floor = max(
                minimum_floor,
                peak_close - trail_distance_r * stop_pct * entry_scale,
            )
            trail_floor = next_floor if trail_floor is None else max(trail_floor, next_floor)

    if exit_idx is None:
        if end_idx in stock.index:
            end = _row(stock, end_idx)
            if int(end["limit_status"]) != 6:
                exit_idx = end_idx
                exit_scale = float(end["adj_close"])
                reason = "TIME_CLOSE"
        if exit_idx is None:
            later_indices = stock.index[stock.index > end_idx]
            for later_idx in later_indices:
                later = _row(stock, int(later_idx))
                if int(later["limit_status"]) == 6:
                    path_min = min(path_min, float(later["adj_low"]))
                    continue
                exit_idx = int(later_idx)
                exit_scale = float(later["adj_open"])
                reason = "TIME_DELAYED_OPEN"
                path_min = min(path_min, exit_scale)
                break
    if exit_idx is None or exit_scale is None:
        return None

    net_return = (
        exit_scale / entry_scale * (1.0 - SELL_COST_RATE)
        - 1.0
        - BUY_COST_RATE
    )
    tail_return = (
        path_min / entry_scale * (1.0 - SELL_COST_RATE)
        - 1.0
        - BUY_COST_RATE
    )
    return TrailingExitOutcome(
        exit_idx=exit_idx,
        exit_scale=exit_scale,
        net_return=net_return,
        net_r=net_return / stop_pct,
        tail_loss_r=max(-tail_return, 0.0) / stop_pct,
        reason=reason,
        distribution_signal_idx=(
            distribution_idx if reason == "DISTRIBUTION_NEXT_OPEN" else None
        ),
        entry_idx=entry_idx,
        hold_sessions=exit_idx - entry_idx,
        activated=activated,
        activation_idx=activation_idx,
        peak_close_scale=peak_close,
        trail_floor_at_exit=floor_at_exit,
        giveback_r=max(peak_close - exit_scale, 0.0) / (entry_scale * stop_pct),
    )


def add_trailing_variant(
    outcomes: pd.DataFrame,
    prefix: str,
    values: list[TrailingExitOutcome | None],
) -> None:
    outcomes[f"{prefix}_valid"] = [value is not None for value in values]
    for field in TrailingExitOutcome.__dataclass_fields__:
        outcomes[f"{prefix}_{field}"] = [
            getattr(value, field) if value is not None else np.nan for value in values
        ]


def _profit_factor(values: pd.Series) -> float:
    wins = values[values > 0]
    losses = values[values < 0]
    gross_loss = float(-losses.sum())
    return float(wins.sum() / gross_loss) if gross_loss else math.inf


def trailing_diagnostics(
    outcomes: pd.DataFrame, variants: list[str]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    metric_rows: list[dict[str, Any]] = []
    reason_rows: list[dict[str, Any]] = []
    periods: dict[str, tuple[int, ...]] = {
        "development_2023_2024": DEVELOPMENT_YEARS,
        "diagnostic_2025_2026": DIAGNOSTIC_YEARS,
    }
    for year in range(2023, 2027):
        periods[str(year)] = (year,)
    for period, years in periods.items():
        period_mask = outcomes["signal_date"].dt.year.isin(years)
        for variant in variants:
            work = outcomes[period_mask & outcomes[f"{variant}_valid"]]
            activated = work[f"{variant}_activated"].fillna(False).astype(bool)
            activated_work = work[activated]
            r_values = work[f"{variant}_net_r"]
            without_best = r_values.drop(r_values.idxmax()) if len(r_values) > 1 else r_values.iloc[0:0]
            metric_rows.append(
                {
                    "period": period,
                    "variant": variant,
                    "trades": int(len(work)),
                    "activation_rate": float(activated.mean()) if len(work) else math.nan,
                    "mean_hold_sessions": float(work[f"{variant}_hold_sessions"].mean())
                    if len(work)
                    else math.nan,
                    "mean_activated_giveback_r": float(
                        activated_work[f"{variant}_giveback_r"].mean()
                    )
                    if len(activated_work)
                    else math.nan,
                    "mean_r_without_best_trade": float(without_best.mean())
                    if len(without_best)
                    else math.nan,
                    "profit_factor_without_best_trade": _profit_factor(without_best)
                    if len(without_best)
                    else math.nan,
                }
            )
            counts = work[f"{variant}_reason"].value_counts(dropna=False)
            for reason, count in counts.items():
                reason_rows.append(
                    {
                        "period": period,
                        "variant": variant,
                        "reason": str(reason),
                        "count": int(count),
                        "rate": float(count / len(work)) if len(work) else math.nan,
                    }
                )
    return pd.DataFrame(metric_rows), pd.DataFrame(reason_rows)


def portfolio_robustness(
    trades: pd.DataFrame, summaries: dict[str, Any]
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for variant, summary in summaries.items():
        selected = trades[trades["variant"].eq(variant)].copy()
        without_best = selected.drop(
            selected["portfolio_return"].idxmax(), errors="ignore"
        )
        result[variant] = {
            **summary,
            "total_return_without_best_trade": float(
                (1.0 + without_best["portfolio_return"]).prod() - 1.0
            )
            if len(without_best)
            else math.nan,
            "stock_profit_factor_without_best_trade": _profit_factor(
                without_best["net_r"]
            )
            if len(without_best)
            else math.nan,
        }
    return result


def historical_acceptance(
    cohort: pd.DataFrame,
    comparisons: list[dict[str, Any]],
    portfolio: dict[str, Any],
) -> dict[str, Any]:
    primary = "v2_trailing_max20"
    vs_v1 = next(
        item
        for item in comparisons
        if item["comparison"] == f"{primary}_minus_v1_distribution_max20"
    )
    checks: dict[str, bool] = {}
    for period in ("development_2023_2024", "diagnostic_2025_2026"):
        row = cohort[(cohort["period"].eq(period)) & (cohort["variant"].eq(primary))].iloc[0]
        checks[f"{period}_mean_net_r_positive"] = bool(row["mean_net_r"] > 0)
        checks[f"{period}_profit_factor_at_least_1_2"] = bool(
            row["profit_factor"] >= 1.2
        )
        checks[f"{period}_increment_vs_v1_positive"] = bool(
            vs_v1[period]["daily_mean_difference_r"] > 0
        )
        checks[f"{period}_increment_vs_v1_hac_p_at_most_0_05"] = bool(
            vs_v1[period]["hac_p"] <= 0.05
        )
    single = portfolio[primary]
    checks.update(
        {
            "single_position_minimum_30_trades": bool(single["trades"] >= 30),
            "single_position_mean_net_r_positive": bool(single["mean_net_r"] > 0),
            "single_position_profit_factor_at_least_1_2": bool(
                single["profit_factor"] >= 1.2
            ),
            "single_position_total_return_positive": bool(single["total_return"] > 0),
            "single_position_drawdown_within_10pct": bool(
                single["maximum_closed_trade_drawdown"] >= -0.10
            ),
            "single_position_positive_without_best_trade": bool(
                single["total_return_without_best_trade"] > 0
            ),
        }
    )
    passed = all(checks.values())
    return {
        "passed": passed,
        "checks": checks,
        "decision": (
            "ELIGIBLE_FOR_NEW_FORWARD_PAPER_OBSERVATION"
            if passed
            else "HISTORICAL_ACCEPTANCE_FAILED_DO_NOT_ACTIVATE_V2"
        ),
        "automatic_live_authorization": False,
    }


def render_report(
    audit: dict[str, Any],
    cohort: pd.DataFrame,
    comparisons: list[dict[str, Any]],
    diagnostics: pd.DataFrame,
    reasons: pd.DataFrame,
    portfolio_yearly: pd.DataFrame,
    portfolio: dict[str, Any],
    acceptance: dict[str, Any],
) -> str:
    comparison_rows: list[dict[str, Any]] = []
    for item in comparisons:
        for period in ("development_2023_2024", "diagnostic_2025_2026"):
            comparison_rows.append(
                {"comparison": item["comparison"], "period": period, **item[period]}
            )
    portfolio_rows = [
        {"variant": variant, **summary} for variant, summary in portfolio.items()
    ]
    return "\n".join(
        [
            "# 锁仓转拉升：移动止盈历史实验",
            "",
            "## 结论",
            "",
            f"- 历史验收：`{acceptance['decision']}`。这不是新的样本外验证，也不授权实盘。",
            "- V2 主组仅把 V1 的派发止盈替换为移动止盈；入场、ATR 硬止损、费用、仓位和 20 日上限不变。",
            "- 移动止盈参数事前固定为：收盘达到 +1.5R 后激活、距最高已完成收盘 1.0R、最低锁定 +0.25R。",
            "- 当日收盘只能影响下一交易日的移动价，规避日线内最高/最低先后未知导致的乐观成交。",
            "",
            "## 数据与复现审计",
            "",
            "```json",
            json.dumps(_json_value(audit), ensure_ascii=False, indent=2),
            "```",
            "",
            "## 候选级结果",
            "",
            cohort.to_markdown(index=False, floatfmt=".4f"),
            "",
            "## 配对日增量（单位 R）",
            "",
            pd.DataFrame(comparison_rows).to_markdown(index=False, floatfmt=".4f"),
            "",
            "## 移动止盈诊断",
            "",
            diagnostics.to_markdown(index=False, floatfmt=".4f"),
            "",
            "## 退出原因",
            "",
            reasons.to_markdown(index=False, floatfmt=".4f"),
            "",
            "## 单持仓组合",
            "",
            pd.DataFrame(portfolio_rows).to_markdown(index=False, floatfmt=".4f"),
            "",
            "## 单持仓逐年",
            "",
            portfolio_yearly.to_markdown(index=False, floatfmt=".4f"),
            "",
            "## 预注册历史验收",
            "",
            "```json",
            json.dumps(_json_value(acceptance), ensure_ascii=False, indent=2),
            "```",
            "",
            "## 边界",
            "",
            "2025—2026 已被用于既有研究，只能称历史诊断段。若历史验收通过，仍须从启用后的第一个完整信号开始做不回填的前向纸面观察；若未通过，则保留结果但不启用 V2。",
            "",
        ]
    )


def main() -> int:
    args = parse_args()
    transition_output = resolve_from_root(args.transition_output)
    market_root = resolve_from_root(args.market_root)
    output_dir = resolve_from_root(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("Loading frozen lock-to-markup candidates", flush=True)
    candidates = load_candidates(transition_output)
    calendar = load_calendar(market_root)
    date_to_idx = calendar.set_index("calendar_date")["trade_idx"]
    candidates["trade_idx"] = candidates["signal_date"].map(date_to_idx)
    if candidates["trade_idx"].isna().any():
        raise ValueError("Candidate dates are missing from the calendar")
    candidates["trade_idx"] = candidates["trade_idx"].astype("int32")

    instrument_ids = sorted(candidates["instrument_id"].astype(str).unique())
    market = load_candidate_market(
        market_root,
        instrument_ids,
        candidates["signal_date"].min(),
        calendar["calendar_date"].max(),
        calendar,
    )
    market_views = {
        str(instrument_id): group.set_index("trade_idx", drop=False).sort_index()
        for instrument_id, group in market.groupby("instrument_id", sort=False)
    }
    transitions = load_distribution_transitions(
        transition_output / "daily_state_cache.parquet",
        set(instrument_ids),
        date_to_idx,
    )

    outcomes = candidates.copy()
    print("Replaying V1 no-distribution and distribution exits", flush=True)
    baseline_values = []
    v1_values = []
    trailing_values: list[TrailingExitOutcome | None] = []
    combined_values: list[TrailingExitOutcome | None] = []
    for row in outcomes.itertuples(index=False):
        stock = market_views[str(row.instrument_id)]
        state_changes = transitions.get(
            str(row.instrument_id), np.empty(0, dtype="int32")
        )
        signal_idx = int(row.trade_idx)
        stop_pct = float(row.v5_stop_pct)
        baseline_values.append(
            simulate_exit(stock, signal_idx, stop_pct, HORIZON, state_changes, False)
        )
        v1_values.append(
            simulate_exit(stock, signal_idx, stop_pct, HORIZON, state_changes, True)
        )
        trailing_values.append(
            simulate_trailing_exit(stock, signal_idx, stop_pct, HORIZON, state_changes)
        )
        combined_values.append(
            simulate_trailing_exit(
                stock,
                signal_idx,
                stop_pct,
                HORIZON,
                state_changes,
                use_distribution_exit=True,
            )
        )

    add_variant(outcomes, "baseline_20", baseline_values)
    add_variant(outcomes, "v1_distribution_max20", v1_values)
    add_trailing_variant(outcomes, "v2_trailing_max20", trailing_values)
    add_trailing_variant(outcomes, "shadow_trailing_plus_distribution", combined_values)

    valid = outcomes["baseline_20_valid"]
    return_error = np.abs(
        outcomes.loc[valid, "baseline_20_net_return"]
        - outcomes.loc[valid, "v5_net_return"]
    )
    r_error = np.abs(
        outcomes.loc[valid, "baseline_20_net_r"] - outcomes.loc[valid, "v5_net_r"]
    )
    tail_error = np.abs(
        outcomes.loc[valid, "baseline_20_tail_loss_r"]
        - outcomes.loc[valid, "v5_tail_loss_r"]
    )
    reconciliation = {
        "rows": int(valid.sum()),
        "all_rows_valid": bool(valid.all()),
        "maximum_absolute_net_return_error": float(return_error.max()),
        "maximum_absolute_net_r_error": float(r_error.max()),
        "maximum_absolute_tail_loss_r_error": float(tail_error.max()),
        "passed": bool(
            valid.all()
            and (return_error <= 2e-6).all()
            and (r_error <= 2e-6).all()
            and (tail_error <= 2e-6).all()
        ),
    }
    if not reconciliation["passed"]:
        raise RuntimeError(f"Frozen V5 reconciliation failed: {reconciliation}")

    variants = [
        "baseline_20",
        "v1_distribution_max20",
        "v2_trailing_max20",
        "shadow_trailing_plus_distribution",
    ]
    cohort = build_cohort_metrics(outcomes, variants)
    comparisons = [
        paired_daily_test(outcomes, "v2_trailing_max20", "v1_distribution_max20", None),
        paired_daily_test(outcomes, "v2_trailing_max20", "baseline_20", None),
        paired_daily_test(
            outcomes,
            "shadow_trailing_plus_distribution",
            "v1_distribution_max20",
            None,
        ),
        paired_daily_test(
            outcomes,
            "shadow_trailing_plus_distribution",
            "v2_trailing_max20",
            None,
        ),
    ]
    trailing_variants = [
        "v2_trailing_max20",
        "shadow_trailing_plus_distribution",
    ]
    diagnostics, reasons = trailing_diagnostics(outcomes, trailing_variants)
    portfolio_trades, portfolio_yearly, portfolio_summary = simulate_portfolios(
        outcomes, variants
    )
    portfolio = portfolio_robustness(portfolio_trades, portfolio_summary)
    acceptance = historical_acceptance(cohort, comparisons, portfolio)
    audit = {
        "strategy_parameters": {
            "activation_r": ACTIVATION_R,
            "trail_distance_r": TRAIL_DISTANCE_R,
            "minimum_lock_r": MINIMUM_LOCK_R,
            "maximum_forward_sessions_after_entry": HORIZON,
            "watermark": "HIGHEST_COMPLETED_CLOSE_THROUGH_D_MINUS_1",
        },
        "candidates": int(len(outcomes)),
        "instruments": int(outcomes["instrument_id"].nunique()),
        "research_end": str(calendar["calendar_date"].max().date()),
        "reconciliation": reconciliation,
    }
    summary = {
        "audit": audit,
        "comparisons": comparisons,
        "single_position": portfolio,
        "historical_acceptance": acceptance,
        "limitations": [
            "2025-2026 is inspected historical diagnostic data, not a new holdout.",
            "Daily OHLC cannot identify the intraday order of high and low; only prior completed closes update the trail.",
            "Distribution remains a B-level behavioural proxy and is used only in V1 and the shadow group.",
        ],
    }

    outcome_columns = [
        "signal_date",
        "instrument_id",
        "trade_idx",
        "transition_gap_days",
        "estimated_pl_ratio",
        "v5_stop_pct",
    ]
    for variant in variants:
        fields = [
            "valid",
            "exit_idx",
            "exit_scale",
            "net_return",
            "net_r",
            "tail_loss_r",
            "reason",
            "distribution_signal_idx",
        ]
        if variant in trailing_variants:
            fields += [
                "entry_idx",
                "hold_sessions",
                "activated",
                "activation_idx",
                "peak_close_scale",
                "trail_floor_at_exit",
                "giveback_r",
            ]
        outcome_columns.extend(f"{variant}_{field}" for field in fields)
    outcomes[outcome_columns].to_parquet(
        output_dir / "candidate_outcomes.parquet", index=False
    )
    cohort.to_csv(output_dir / "cohort_metrics.csv", index=False, encoding="utf-8-sig")
    diagnostics.to_csv(
        output_dir / "trailing_diagnostics.csv", index=False, encoding="utf-8-sig"
    )
    reasons.to_csv(output_dir / "exit_reasons.csv", index=False, encoding="utf-8-sig")
    portfolio_trades.to_csv(
        output_dir / "single_position_trades.csv", index=False, encoding="utf-8-sig"
    )
    portfolio_yearly.to_csv(
        output_dir / "single_position_yearly.csv", index=False, encoding="utf-8-sig"
    )
    (output_dir / "summary.json").write_text(
        json.dumps(_json_value(summary), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "report.md").write_text(
        render_report(
            audit,
            cohort,
            comparisons,
            diagnostics,
            reasons,
            portfolio_yearly,
            portfolio,
            acceptance,
        ),
        encoding="utf-8",
    )
    print(json.dumps(_json_value(summary), ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
