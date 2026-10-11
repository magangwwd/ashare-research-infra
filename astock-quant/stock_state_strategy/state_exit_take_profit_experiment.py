"""Test a point-in-time distribution-state exit for lock-to-markup entries.

The experiment keeps the frozen demo01 V5 entry and ATR stop.  A distribution
exit is only actionable at the next sellable open after a close is classified
as ``markup_to_distribution``.  Results compare 20-, 40- and 60-session maximum
holding windows; longer-window cohorts exclude signals without a complete
forward window at the research cutoff.
"""

from __future__ import annotations

from data_source_paths import resolve_project_path

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from v5_feature_information_audit import newey_west_mean_test
from v6_control_monitor_entry_audit import simulate_single_position


DEVELOPMENT_YEARS = (2023, 2024)
DIAGNOSTIC_YEARS = (2025, 2026)
BUY_COST_RATE = 0.0013
SELL_COST_RATE = 0.0018


@dataclass(frozen=True)
class ExitOutcome:
    exit_idx: int
    exit_scale: float
    net_return: float
    net_r: float
    tail_loss_r: float
    reason: str
    distribution_signal_idx: int | None


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Test markup-to-distribution exits with 20/40/60-session caps"
    )
    parser.add_argument(
        "--transition-output",
        default="output/state_transition_backtest",
        help="Directory containing candidate_rows and daily_state_cache",
    )
    parser.add_argument(
        "--market-root", default="../down_data/data/gold/main_board"
    )
    parser.add_argument(
        "--output-dir", default="output/state_exit_take_profit_experiment"
    )
    return parser.parse_args()


def resolve_from_root(value: str) -> Path:
    return resolve_project_path(ROOT, value)


def load_candidates(transition_output: Path) -> pd.DataFrame:
    candidates = pd.read_parquet(transition_output / "candidate_rows.parquet")
    candidates["signal_date"] = pd.to_datetime(candidates["signal_date"])
    candidates = candidates[
        candidates["pool_status"].eq("A_STRATEGY_CANDIDATE")
        & candidates["transition"].eq("lock_to_markup")
        & candidates["signal_date"].dt.year.ge(2023)
    ].copy()
    # candidate_rows already contains the frozen V5 net return, net R and tail
    # loss.  Since net_r = net_return / stop_pct, the frozen stop can be
    # recovered without re-reading the much larger label file.
    candidates["v5_stop_pct"] = candidates["v5_net_return"] / candidates["v5_net_r"]
    valid_stop = (
        np.isfinite(candidates["v5_stop_pct"])
        & candidates["v5_stop_pct"].between(0.04 - 1e-6, 0.08 + 1e-6)
    )
    if not valid_stop.all():
        raise ValueError(f"Could not recover {int((~valid_stop).sum())} frozen stops")
    return candidates.sort_values(["signal_date", "instrument_id"]).reset_index(drop=True)


def load_calendar(market_root: Path) -> pd.DataFrame:
    calendar = pd.read_parquet(market_root / "trading_calendar.parquet")
    calendar["calendar_date"] = pd.to_datetime(calendar["calendar_date"])
    return (
        calendar.loc[calendar["is_open"], ["calendar_date"]]
        .sort_values("calendar_date")
        .drop_duplicates("calendar_date")
        .reset_index(drop=True)
        .assign(trade_idx=lambda frame: np.arange(len(frame), dtype="int32"))
    )


def _date_filter(start: pd.Timestamp, end: pd.Timestamp) -> ds.Expression:
    field = ds.field("trade_date")
    return (field >= pa.scalar(start.date(), type=pa.date32())) & (
        field <= pa.scalar(end.date(), type=pa.date32())
    )


def _read_dataset(
    path: Path,
    columns: list[str],
    instrument_ids: list[str],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    predicate = _date_filter(start, end) & ds.field("instrument_id").isin(
        instrument_ids
    )
    return (
        ds.dataset(path, format="parquet", partitioning="hive")
        .to_table(columns=columns, filter=predicate)
        .to_pandas()
    )


def load_candidate_market(
    market_root: Path,
    instrument_ids: list[str],
    start: pd.Timestamp,
    end: pd.Timestamp,
    calendar: pd.DataFrame,
) -> pd.DataFrame:
    prices = _read_dataset(
        market_root / "daily_prices",
        [
            "instrument_id",
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "is_quality_valid",
        ],
        instrument_ids,
        start,
        end,
    )
    print(f"Loaded {len(prices):,} candidate price rows", flush=True)
    factors = _read_dataset(
        market_root / "adjustment_factors",
        ["instrument_id", "trade_date", "adjustment_factor"],
        instrument_ids,
        start,
        end,
    )
    print(f"Loaded {len(factors):,} candidate factor rows", flush=True)
    limits = _read_dataset(
        market_root / "daily_limits",
        ["instrument_id", "trade_date", "up_limit", "down_limit"],
        instrument_ids,
        start,
        end,
    )
    print(f"Loaded {len(limits):,} candidate limit rows", flush=True)
    for frame in (prices, factors, limits):
        frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    market = prices.merge(
        factors,
        on=["instrument_id", "trade_date"],
        how="left",
        validate="one_to_one",
    ).merge(
        limits,
        on=["instrument_id", "trade_date"],
        how="left",
        validate="one_to_one",
    )
    valid = (
        market["is_quality_valid"].fillna(False)
        & market["adjustment_factor"].gt(0)
        & market[["up_limit", "down_limit"]].notna().all(axis=1)
        & market["up_limit"].gt(0)
        & market["down_limit"].gt(0)
    )
    market = market.loc[valid].copy()
    flat_bar = (
        np.isclose(market["open"], market["high"], atol=0.0051, rtol=0)
        & np.isclose(market["open"], market["low"], atol=0.0051, rtol=0)
        & np.isclose(market["open"], market["close"], atol=0.0051, rtol=0)
    )
    one_word_down = flat_bar & np.isclose(
        market["open"], market["down_limit"], atol=0.0051, rtol=0
    )
    market["limit_status"] = np.where(one_word_down, 6, 0).astype("int8")
    market["adj_open"] = market["open"] * market["adjustment_factor"]
    market["adj_low"] = market["low"] * market["adjustment_factor"]
    market["adj_close"] = market["close"] * market["adjustment_factor"]
    date_to_idx = calendar.set_index("calendar_date")["trade_idx"]
    market["trade_idx"] = market["trade_date"].map(date_to_idx)
    if market["trade_idx"].isna().any():
        raise ValueError("Market dates are missing from the trading calendar")
    market["trade_idx"] = market["trade_idx"].astype("int32")
    return market.sort_values(["instrument_id", "trade_idx"]).reset_index(drop=True)


def load_distribution_transitions(
    state_cache: Path,
    instrument_ids: set[str],
    date_to_idx: pd.Series,
) -> dict[str, np.ndarray]:
    print("Loading daily state transitions", flush=True)
    states = pd.read_parquet(
        state_cache, columns=["instrument_id", "trade_date", "transition"]
    )
    print(f"Loaded {len(states):,} daily state rows", flush=True)
    states["trade_date"] = pd.to_datetime(states["trade_date"])
    selected = states[
        states["instrument_id"].isin(instrument_ids)
        & states["transition"].eq("markup_to_distribution")
    ].copy()
    selected["trade_idx"] = selected["trade_date"].map(date_to_idx)
    selected = selected[selected["trade_idx"].notna()]
    return {
        str(instrument_id): np.sort(group["trade_idx"].to_numpy(dtype="int32"))
        for instrument_id, group in selected.groupby("instrument_id", sort=False)
    }


def simulate_exit(
    stock: pd.DataFrame,
    signal_idx: int,
    stop_pct: float,
    horizon: int,
    distribution_indices: np.ndarray,
    use_distribution_exit: bool,
) -> ExitOutcome | None:
    entry_idx = signal_idx + 1
    end_idx = signal_idx + horizon + 1
    if entry_idx not in stock.index:
        return None
    entry = stock.loc[entry_idx]
    if isinstance(entry, pd.DataFrame):
        raise ValueError(f"Duplicate market rows at trade_idx={entry_idx}")
    entry_scale = float(entry["adj_open"])
    if not np.isfinite(entry_scale) or entry_scale <= 0:
        return None
    stop_scale = entry_scale * (1.0 - stop_pct)
    pending_entry_stop = float(entry["adj_low"]) <= stop_scale
    path_min = float(entry["adj_low"])
    future_distribution = distribution_indices[distribution_indices > signal_idx]
    distribution_idx = (
        int(future_distribution[0])
        if use_distribution_exit and len(future_distribution)
        else None
    )

    exit_idx: int | None = None
    exit_scale: float | None = None
    reason = ""
    for current_idx in range(entry_idx + 1, end_idx + 1):
        if current_idx not in stock.index:
            continue
        current = stock.loc[current_idx]
        if isinstance(current, pd.DataFrame):
            raise ValueError(f"Duplicate market rows at trade_idx={current_idx}")
        sellable = int(current["limit_status"]) != 6
        current_open = float(current["adj_open"])
        current_low = float(current["adj_low"])
        if pending_entry_stop and sellable:
            exit_idx, exit_scale, reason = current_idx, current_open, "ENTRY_DAY_STOP_NEXT_OPEN"
            path_min = min(path_min, current_open)
            break
        if sellable and current_open <= stop_scale:
            exit_idx, exit_scale, reason = current_idx, current_open, "GAP_STOP"
            path_min = min(path_min, current_open)
            break
        if (
            sellable
            and distribution_idx is not None
            and current_idx > distribution_idx
        ):
            exit_idx, exit_scale, reason = current_idx, current_open, "DISTRIBUTION_NEXT_OPEN"
            path_min = min(path_min, current_open)
            break
        if sellable and current_low <= stop_scale:
            exit_idx, exit_scale, reason = current_idx, stop_scale, "INTRADAY_STOP"
            path_min = min(path_min, stop_scale)
            break
        path_min = min(path_min, current_low)

    if exit_idx is None:
        if end_idx in stock.index:
            end = stock.loc[end_idx]
            if isinstance(end, pd.DataFrame):
                raise ValueError(f"Duplicate market rows at trade_idx={end_idx}")
            if int(end["limit_status"]) != 6:
                exit_idx, exit_scale, reason = end_idx, float(end["adj_close"]), "TIME_CLOSE"
        if exit_idx is None:
            later_indices = stock.index[stock.index > end_idx]
            for later_idx in later_indices:
                later = stock.loc[later_idx]
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
    net_return = exit_scale / entry_scale * (1.0 - SELL_COST_RATE) - 1.0 - BUY_COST_RATE
    tail_return = path_min / entry_scale * (1.0 - SELL_COST_RATE) - 1.0 - BUY_COST_RATE
    tail_loss_r = max(-tail_return, 0.0) / stop_pct
    return ExitOutcome(
        exit_idx=exit_idx,
        exit_scale=exit_scale,
        net_return=net_return,
        net_r=net_return / stop_pct,
        tail_loss_r=tail_loss_r,
        reason=reason,
        distribution_signal_idx=distribution_idx if reason == "DISTRIBUTION_NEXT_OPEN" else None,
    )


def trade_statistics(frame: pd.DataFrame, prefix: str) -> dict[str, Any]:
    work = frame[frame[f"{prefix}_valid"]]
    if work.empty:
        return {
            "trades": 0,
            "mean_net_return": math.nan,
            "mean_net_r": math.nan,
            "win_rate": math.nan,
            "payoff_ratio": math.nan,
            "profit_factor": math.nan,
            "distribution_exits": 0,
        }
    returns = work[f"{prefix}_net_return"].to_numpy(dtype=float)
    r_values = work[f"{prefix}_net_r"].to_numpy(dtype=float)
    wins = r_values[r_values > 0]
    losses = r_values[r_values < 0]
    gross_loss = float(-losses.sum()) if len(losses) else 0.0
    return {
        "trades": int(len(work)),
        "mean_net_return": float(returns.mean()),
        "mean_net_r": float(r_values.mean()),
        "win_rate": float((returns > 0).mean()),
        "payoff_ratio": float(wins.mean() / -losses.mean())
        if len(wins) and len(losses)
        else math.nan,
        "profit_factor": float(wins.sum() / gross_loss) if gross_loss else math.inf,
        "distribution_exits": int(
            work[f"{prefix}_reason"].eq("DISTRIBUTION_NEXT_OPEN").sum()
        ),
    }


def build_cohort_metrics(outcomes: pd.DataFrame, variants: list[str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    periods = {
        "development_2023_2024": DEVELOPMENT_YEARS,
        "diagnostic_2025_2026": DIAGNOSTIC_YEARS,
    }
    for period, years in periods.items():
        mask = outcomes["signal_date"].dt.year.isin(years)
        for variant in variants:
            rows.append(
                {
                    "period": period,
                    "variant": variant,
                    **trade_statistics(outcomes[mask], variant),
                }
            )
    for year in range(2023, 2027):
        mask = outcomes["signal_date"].dt.year.eq(year)
        for variant in variants:
            rows.append(
                {
                    "period": str(year),
                    "variant": variant,
                    **trade_statistics(outcomes[mask], variant),
                }
            )
    return pd.DataFrame(rows)


def paired_daily_test(
    outcomes: pd.DataFrame,
    left: str,
    right: str,
    common_window_column: str | None,
) -> dict[str, Any]:
    valid = outcomes[f"{left}_valid"] & outcomes[f"{right}_valid"]
    if common_window_column is not None:
        valid &= outcomes[common_window_column]
    work = outcomes[valid].copy()
    work["difference"] = work[f"{left}_net_r"] - work[f"{right}_net_r"]
    daily = work.groupby("signal_date", sort=True)["difference"].mean()
    result: dict[str, Any] = {
        "comparison": f"{left}_minus_{right}",
        "signals": int(len(work)),
        "days": int(len(daily)),
        "candidate_mean_difference_r": float(work["difference"].mean())
        if len(work)
        else math.nan,
        "better_signal_rate": float(work["difference"].gt(0).mean())
        if len(work)
        else math.nan,
    }
    for period, years in {
        "development_2023_2024": DEVELOPMENT_YEARS,
        "diagnostic_2025_2026": DIAGNOSTIC_YEARS,
    }.items():
        selected = daily[daily.index.year.isin(years)]
        mean, statistic, p_value = newey_west_mean_test(selected)
        result[period] = {
            "days": int(len(selected)),
            "daily_mean_difference_r": mean,
            "hac_t": statistic,
            "hac_p": p_value,
        }
    return result


def add_variant(
    outcomes: pd.DataFrame,
    prefix: str,
    values: list[ExitOutcome | None],
) -> None:
    outcomes[f"{prefix}_valid"] = [value is not None for value in values]
    for field in (
        "exit_idx",
        "exit_scale",
        "net_return",
        "net_r",
        "tail_loss_r",
        "reason",
        "distribution_signal_idx",
    ):
        outcomes[f"{prefix}_{field}"] = [
            getattr(value, field) if value is not None else np.nan for value in values
        ]


def simulate_portfolios(
    outcomes: pd.DataFrame, variants: list[str]
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    trade_parts: list[pd.DataFrame] = []
    yearly_parts: list[pd.DataFrame] = []
    summaries: dict[str, Any] = {}
    for variant in variants:
        samples = outcomes[outcomes[f"{variant}_valid"]].copy()
        samples["eligible"] = True
        samples["control_age_days"] = samples["transition_gap_days"]
        samples["v5_exit_idx"] = samples[f"{variant}_exit_idx"].astype(int)
        samples["v5_net_return"] = samples[f"{variant}_net_return"]
        samples["v5_net_r"] = samples[f"{variant}_net_r"]
        samples["v5_tail_loss_r"] = samples[f"{variant}_tail_loss_r"]
        trades, summary, yearly = simulate_single_position(samples, "eligible")
        trades["variant"] = variant
        yearly["variant"] = variant
        trade_parts.append(trades)
        yearly_parts.append(yearly)
        summaries[variant] = summary
    return (
        pd.concat(trade_parts, ignore_index=True),
        pd.concat(yearly_parts, ignore_index=True),
        summaries,
    )


def render_report(
    audit: dict[str, Any],
    cohort: pd.DataFrame,
    comparisons: list[dict[str, Any]],
    portfolio_yearly: pd.DataFrame,
    portfolio_summary: dict[str, Any],
) -> str:
    comparison_rows = []
    for item in comparisons:
        for period in ("development_2023_2024", "diagnostic_2025_2026"):
            comparison_rows.append(
                {
                    "comparison": item["comparison"],
                    "period": period,
                    **item[period],
                }
            )
    portfolio_rows = [
        {"variant": variant, **summary}
        for variant, summary in portfolio_summary.items()
    ]
    return "\n".join(
        [
            "# 拉升转派发退出实验",
            "",
            "## 口径",
            "",
            "- 入场仍为锁仓转A级拉升后的T+1开盘，保留冻结ATR止损和双边费用。",
            "- 派发状态在收盘后才能确认，因此最早在下一可卖交易日开盘退出；同日盘中止损优先。",
            "- 比较20日、40日、60日上限，以及每个上限叠加派发退出的结果。",
            "- 40日和60日结果只使用研究截止日前拥有相应完整窗口的信号；这不是新的独立留出检验。",
            "",
            "## 数据审计",
            "",
            "```json",
            json.dumps(_json_value(audit), ensure_ascii=False, indent=2),
            "```",
            "",
            "## 候选级结果",
            "",
            cohort.to_markdown(index=False, floatfmt=".4f"),
            "",
            "## 配对增量检验",
            "",
            pd.DataFrame(comparison_rows).to_markdown(index=False, floatfmt=".4f"),
            "",
            "## 单持仓结果",
            "",
            pd.DataFrame(portfolio_rows).to_markdown(index=False, floatfmt=".4f"),
            "",
            "## 单持仓逐年结果",
            "",
            portfolio_yearly.to_markdown(index=False, floatfmt=".4f"),
            "",
            "## 解释边界",
            "",
            "派发是缺少逐日大/小单资金流的B级行为代理，不是主力真实卖出的直接证据。任何改善都只能作为历史诊断，不能改变当前NO_ENTRY_AUTHORIZATION。",
            "",
        ]
    )


def main() -> int:
    args = parse_args()
    transition_output = resolve_from_root(args.transition_output)
    market_root = resolve_from_root(args.market_root)
    output_dir = resolve_from_root(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("Loading lock-to-markup candidates and V5 labels", flush=True)
    candidates = load_candidates(transition_output)
    calendar = load_calendar(market_root)
    date_to_idx = calendar.set_index("calendar_date")["trade_idx"]
    candidates["trade_idx"] = candidates["signal_date"].map(date_to_idx)
    if candidates["trade_idx"].isna().any():
        raise ValueError("Candidate dates are missing from the calendar")
    candidates["trade_idx"] = candidates["trade_idx"].astype("int32")
    research_end_idx = int(calendar["trade_idx"].max())
    candidates["complete_60_window"] = (
        candidates["trade_idx"] + 61 <= research_end_idx
    )
    candidates["complete_40_window"] = (
        candidates["trade_idx"] + 41 <= research_end_idx
    )

    instrument_ids = sorted(candidates["instrument_id"].astype(str).unique())
    print(f"Loading market paths for {len(instrument_ids):,} instruments", flush=True)
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
    variants = {
        "baseline_20": (20, False),
        "distribution_exit_max20": (20, True),
        "baseline_40": (40, False),
        "distribution_exit_max40": (40, True),
        "baseline_60": (60, False),
        "distribution_exit_max60": (60, True),
    }
    for prefix, (horizon, use_distribution_exit) in variants.items():
        print(f"Simulating {prefix}", flush=True)
        values: list[ExitOutcome | None] = []
        for row in outcomes.itertuples(index=False):
            if horizon == 40 and not bool(row.complete_40_window):
                values.append(None)
                continue
            if horizon == 60 and not bool(row.complete_60_window):
                values.append(None)
                continue
            values.append(
                simulate_exit(
                    market_views[str(row.instrument_id)],
                    int(row.trade_idx),
                    float(row.v5_stop_pct),
                    horizon,
                    transitions.get(str(row.instrument_id), np.empty(0, dtype="int32")),
                    use_distribution_exit,
                )
            )
        add_variant(outcomes, prefix, values)

    reconcilable = outcomes["baseline_20_valid"]
    return_error = np.abs(
        outcomes.loc[reconcilable, "baseline_20_net_return"]
        - outcomes.loc[reconcilable, "v5_net_return"]
    )
    r_error = np.abs(
        outcomes.loc[reconcilable, "baseline_20_net_r"]
        - outcomes.loc[reconcilable, "v5_net_r"]
    )
    tail_error = np.abs(
        outcomes.loc[reconcilable, "baseline_20_tail_loss_r"]
        - outcomes.loc[reconcilable, "v5_tail_loss_r"]
    )
    reconciliation = {
        "rows": int(reconcilable.sum()),
        "all_rows_valid": bool(reconcilable.all()),
        "maximum_absolute_net_return_error": float(return_error.max()),
        "maximum_absolute_net_r_error": float(r_error.max()),
        "maximum_absolute_tail_loss_r_error": float(tail_error.max()),
        "passed": bool(
            reconcilable.all()
            and (return_error <= 2e-6).all()
            and (r_error <= 2e-6).all()
            and (tail_error <= 2e-6).all()
        ),
    }
    if not reconciliation["passed"]:
        raise RuntimeError(f"Frozen 20-session reconciliation failed: {reconciliation}")

    variant_names = list(variants)
    cohort = build_cohort_metrics(outcomes, variant_names)
    comparisons = [
        paired_daily_test(
            outcomes, "distribution_exit_max20", "baseline_20", None
        ),
        paired_daily_test(
            outcomes, "baseline_40", "baseline_20", "complete_40_window"
        ),
        paired_daily_test(
            outcomes,
            "distribution_exit_max40",
            "baseline_40",
            "complete_40_window",
        ),
        paired_daily_test(
            outcomes,
            "distribution_exit_max40",
            "baseline_20",
            "complete_40_window",
        ),
        paired_daily_test(
            outcomes, "baseline_60", "baseline_20", "complete_60_window"
        ),
        paired_daily_test(
            outcomes,
            "distribution_exit_max60",
            "baseline_60",
            "complete_60_window",
        ),
        paired_daily_test(
            outcomes,
            "distribution_exit_max60",
            "baseline_20",
            "complete_60_window",
        ),
    ]
    portfolio_trades, portfolio_yearly, portfolio_summary = simulate_portfolios(
        outcomes, variant_names
    )
    audit = {
        "candidates": int(len(outcomes)),
        "instruments": int(outcomes["instrument_id"].nunique()),
        "complete_40_window_candidates": int(outcomes["complete_40_window"].sum()),
        "complete_60_window_candidates": int(outcomes["complete_60_window"].sum()),
        "research_end": str(calendar["calendar_date"].max().date()),
        "reconciliation": reconciliation,
    }
    summary = {
        "audit": audit,
        "comparisons": comparisons,
        "single_position": portfolio_summary,
        "limitations": [
            "2025-2026 is inspected historical diagnostic data, not a new holdout.",
            "Distribution is a B-level behavioural proxy without daily order-flow confirmation.",
            "The 40/60-session cohorts exclude right-censored signals near the research cutoff.",
        ],
    }
    outcome_columns = [
        "signal_date",
        "instrument_id",
        "trade_idx",
        "transition_gap_days",
        "v5_stop_pct",
        "complete_40_window",
        "complete_60_window",
    ] + [
        f"{variant}_{field}"
        for variant in variant_names
        for field in (
            "valid",
            "exit_idx",
            "net_return",
            "net_r",
            "tail_loss_r",
            "reason",
            "distribution_signal_idx",
        )
    ]
    outcomes[outcome_columns].to_parquet(
        output_dir / "candidate_outcomes.parquet", index=False
    )
    cohort.to_csv(output_dir / "cohort_metrics.csv", index=False, encoding="utf-8-sig")
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
        render_report(audit, cohort, comparisons, portfolio_yearly, portfolio_summary),
        encoding="utf-8",
    )
    print(json.dumps(_json_value(summary), ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
