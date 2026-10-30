"""Point-in-time validation of phase states and phase transitions.

The audit uses demo02's public-disclosure/market-behaviour state definitions
and demo01's strict T+1 executable V5 outcomes.  It is a historical diagnostic:
2025-2026 has already been inspected by both projects and is not a new holdout.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ownership_pipeline import ROOT, load_config, resolve
from state_cache_contract import fingerprint, record_cache, verify_cache, market_input_files
from pit_name_states import historical_name_states
from state_definition_pool import (
    MAX_DISCLOSURE_AGE_DAYS,
    MAX_ONE_WORD_DAYS_20,
    MIN_HISTORY_OBSERVATIONS_250,
    MIN_LISTING_SESSIONS,
    MIN_MONITOR_CONFIDENCE,
    MIN_MONITOR_TURNOVER_CNY,
    MIN_RECENT_OBSERVATIONS_20,
    MIN_SCORE_COVERAGE,
    MIN_STABLE_DAYS_5,
    MIN_STATE_MARGIN,
    MIN_STATE_SCORE,
    MIN_STRATEGY_CONFIDENCE,
    MIN_STRATEGY_TURNOVER_CNY,
    STATE_CONFIDENCE_CAPS,
    _age_score,
    _attach_disclosures,
    add_phase_scores,
    compute_symbol_features,
    load_market_history,
)


DEVELOPMENT_YEARS = (2023, 2024)
DIAGNOSTIC_YEARS = (2025, 2026)
HISTORICAL_STATE_START = pd.Timestamp("2022-01-01")
MAX_TRANSITION_GAP_DAYS = 45
MIN_STRATEGY_TRADES = 30
MIN_PROFIT_FACTOR = 1.20


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


def build_historical_features(
    panel: pd.DataFrame, start: pd.Timestamp
) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    total = panel["instrument_id"].nunique()
    for number, (_, bars) in enumerate(panel.groupby("instrument_id", sort=False), start=1):
        calculated = compute_symbol_features(bars)
        selected = calculated[calculated["trade_date"].ge(start)]
        if not selected.empty:
            parts.append(selected)
        if number % 400 == 0 or number == total:
            print(f"Historical phase features: {number}/{total} instruments", flush=True)
    if not parts:
        raise RuntimeError("No historical state rows were produced")
    result = pd.concat(parts, ignore_index=True)
    float_columns = result.select_dtypes(include=["float64"]).columns
    result[float_columns] = result[float_columns].astype("float32")
    return result


def add_stability(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.sort_values(["instrument_id", "trade_date"], kind="stable").copy()
    stability = np.zeros(len(result), dtype="int8")
    for positions in result.groupby("instrument_id", sort=False).indices.values():
        pos = np.asarray(positions, dtype=np.int64)
        states = result.loc[pos, "raw_state"].to_numpy(dtype=object)
        local = np.zeros(len(pos), dtype="int8")
        for state in ("accumulation", "lock", "markup", "distribution"):
            counts = (
                pd.Series(states == state)
                .rolling(5, min_periods=1)
                .sum()
                .to_numpy(dtype="int8")
            )
            mask = states == state
            local[mask] = counts[mask]
        stability[pos] = local
    result["stable_days_5"] = stability
    return result


def add_historical_pool_fields(
    frame: pd.DataFrame, instruments: pd.DataFrame, calendar: pd.DataFrame
) -> pd.DataFrame:
    result = add_stability(frame)
    meta = instruments[
        ["instrument_id", "list_date", "industry_vendor"]
    ].drop_duplicates("instrument_id")
    result = result.merge(meta, on="instrument_id", how="left", validate="many_to_one")
    calendar_values = calendar["calendar_date"].to_numpy(dtype="datetime64[ns]")
    list_dates = pd.to_datetime(result["list_date"], errors="coerce").to_numpy(
        dtype="datetime64[ns]"
    )
    list_idx = np.searchsorted(calendar_values, list_dates, side="left")
    result["listing_sessions"] = result["trade_idx"].to_numpy(dtype=int) - list_idx + 1
    result.loc[pd.isna(result["list_date"]), "listing_sessions"] = 0

    disclosure_freshness = pd.concat(
        [
            _age_score(result["holder_disclosure_age_days"], 120, 180),
            _age_score(result["composition_disclosure_age_days"], 120, 180),
            _age_score(result["holder_report_age_days"], 150, 240),
            _age_score(result["composition_report_age_days"], 150, 240),
        ],
        axis=1,
    ).mean(axis=1)
    liquidity_log = np.log(
        result["average_turnover_cny_20"].clip(lower=MIN_MONITOR_TURNOVER_CNY)
        / MIN_MONITOR_TURNOVER_CNY
    ) / np.log(MIN_STRATEGY_TURNOVER_CNY / MIN_MONITOR_TURNOVER_CNY)
    liquidity_score = (100.0 * liquidity_log).clip(0.0, 100.0)
    recent_score = (100.0 * result["recent_observations_20"] / 20.0).clip(0.0, 100.0)
    history_score = (100.0 * result["history_observations_250"] / 250.0).clip(0.0, 100.0)
    one_word_score = (100.0 - 15.0 * result["one_word_days_20"]).clip(0.0, 100.0)
    tradeability = pd.concat(
        [liquidity_score, recent_score, history_score, one_word_score], axis=1
    ).mean(axis=1)
    separability = (result["state_margin"] / 20.0 * 100.0).clip(0.0, 100.0)
    stability_score = (result["stable_days_5"] / 5.0 * 100.0).clip(0.0, 100.0)
    extreme_count = (
        ~result["turnover_ratio_5_60"].between(0.05, 20.0)
    ).astype(int) + (~result["volume_ratio_5_60"].between(0.05, 20.0)).astype(int) + (
        ~result["volatility_ratio_20_60"].between(0.05, 5.0)
    ).astype(int)
    sample_fit = (100.0 - 25.0 * extreme_count).clip(0.0, 100.0)
    raw_confidence = (
        0.20 * (100.0 * result["score_coverage"])
        + 0.15 * disclosure_freshness
        + 0.15 * tradeability
        + 0.20 * (100.0 * result["signal_agreement"])
        + 0.15 * separability
        + 0.10 * stability_score
        + 0.05 * sample_fit
    )
    confidence_cap = result["raw_state"].map(STATE_CONFIDENCE_CAPS).fillna(49.0)
    result["confidence_before_eligibility"] = np.minimum(raw_confidence, confidence_cap)

    base_market = (
        result["historical_state_known"]
        & ~result["historical_is_st"]
        & result["listing_sessions"].ge(MIN_LISTING_SESSIONS)
        & result["history_observations_250"].ge(MIN_HISTORY_OBSERVATIONS_250)
        & result["recent_observations_20"].ge(MIN_RECENT_OBSERVATIONS_20)
        & result["average_turnover_cny_20"].ge(MIN_MONITOR_TURNOVER_CNY)
        & result["one_word_days_20"].le(MAX_ONE_WORD_DAYS_20)
    )
    ownership = (
        result["holder_disclosure_age_days"].between(0, MAX_DISCLOSURE_AGE_DAYS)
        & result["composition_disclosure_age_days"].between(0, MAX_DISCLOSURE_AGE_DAYS)
        & result["holder_update_count"].ge(2)
        & result["composition_update_count"].ge(2)
        & result["composition_quality_valid"].fillna(False)
    )
    state_valid = (
        result["state_score"].ge(MIN_STATE_SCORE)
        & result["state_margin"].ge(MIN_STATE_MARGIN)
        & result["score_coverage"].ge(MIN_SCORE_COVERAGE)
        & result["stable_days_5"].ge(MIN_STABLE_DAYS_5)
    )
    defined = base_market & ownership & state_valid
    result["defined_state"] = result["raw_state"].where(defined, "uncertain")
    result["confidence"] = result["confidence_before_eligibility"].where(
        defined, np.minimum(result["confidence_before_eligibility"], 49.0)
    )
    strategy = (
        defined
        & result["defined_state"].isin(["lock", "markup"])
        & result["confidence"].ge(MIN_STRATEGY_CONFIDENCE)
        & result["average_turnover_cny_20"].ge(MIN_STRATEGY_TURNOVER_CNY)
    )
    monitor = defined & result["confidence"].ge(MIN_MONITOR_CONFIDENCE) & ~strategy
    result["pool_status"] = np.select(
        [strategy, monitor], ["A_STRATEGY_CANDIDATE", "B_MONITOR_ONLY"], default="REMOVED"
    )
    return result


def add_transitions(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.sort_values(["instrument_id", "trade_date"], kind="stable").copy()
    previous_state = np.full(len(result), None, dtype=object)
    previous_date = np.full(len(result), np.datetime64("NaT"), dtype="datetime64[ns]")
    for positions in result.groupby("instrument_id", sort=False).indices.values():
        pos = np.asarray(positions, dtype=np.int64)
        states = result.loc[pos, "defined_state"].replace("uncertain", np.nan)
        dates = result.loc[pos, "trade_date"]
        previous_state[pos] = states.ffill().shift(1).to_numpy(dtype=object)
        previous_date[pos] = dates.where(states.notna()).ffill().shift(1).to_numpy(
            dtype="datetime64[ns]"
        )
    result["previous_defined_state"] = previous_state
    result["previous_defined_date"] = previous_date
    result["transition_gap_days"] = (
        result["trade_date"] - pd.to_datetime(result["previous_defined_date"])
    ).dt.days
    phase_change = (
        result["defined_state"].ne("uncertain")
        & result["previous_defined_state"].notna()
        & result["defined_state"].ne(result["previous_defined_state"])
        & result["transition_gap_days"].between(1, MAX_TRANSITION_GAP_DAYS)
    )
    result["transition"] = np.where(
        phase_change,
        result["previous_defined_state"].astype(str)
        + "_to_"
        + result["defined_state"].astype(str),
        "none",
    )
    return result


def build_or_load_state_history(
    cache_path: Path,
    market_root: Path,
    events: pd.DataFrame,
    end: pd.Timestamp,
    rebuild: bool,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    event_bytes = pd.util.hash_pandas_object(events, index=False).to_numpy().tobytes()
    signature = fingerprint(
        {"end": str(end.date()), "event_columns": list(events.columns),
         "events_sha256": hashlib.sha256(event_bytes).hexdigest()},
        {**market_input_files(market_root),
         **{name: ROOT / name for name in ("state_definition_pool.py", "state_transition_backtest.py",
            "ownership_pipeline.py", "pit_name_states.py", "state_cache_contract.py")}},
    )
    if cache_path.exists() and not rebuild:
        verify_cache(cache_path, signature)
        states = pd.read_parquet(cache_path)
        for column in ("instrument_id", "vendor_symbol", "industry_vendor", "raw_state",
                       "defined_state", "pool_status", "previous_defined_state", "transition"):
            states[column] = states[column].astype(object).where(states[column].notna(), None)
        return states, {"loaded_cache": True, "rows": int(len(states))}
    panel, instruments, calendar = load_market_history(
        market_root, pd.Timestamp("2020-01-01"), end
    )
    features = build_historical_features(panel, HISTORICAL_STATE_START)
    del panel
    gc.collect()
    features = _attach_disclosures(features, events)
    name_states = historical_name_states(features, pd.read_parquet(market_root / "name_history.parquet"))
    features["historical_state_known"] = name_states["historical_state_known"]
    features["historical_is_st"] = name_states["historical_is_st"]
    features = add_phase_scores(features)
    features = add_historical_pool_fields(features, instruments, calendar)
    features = add_transitions(features)
    keep_columns = [
        "instrument_id",
        "vendor_symbol",
        "trade_date",
        "trade_idx",
        "industry_vendor",
        "raw_state",
        "defined_state",
        "pool_status",
        "confidence",
        "state_score",
        "state_margin",
        "score_coverage",
        "signal_agreement",
        "stable_days_5",
        "average_turnover_cny_20",
        "holder_disclosure_age_days",
        "composition_disclosure_age_days",
        "previous_defined_state",
        "previous_defined_date",
        "transition_gap_days",
        "transition",
    ]
    states = features[keep_columns].copy()
    # pandas 3 can infer a new string dtype on Parquet read. Keep the same
    # explicit text schema on both fresh calculation and cached reuse.
    for column in ("instrument_id", "vendor_symbol", "industry_vendor", "raw_state",
                   "defined_state", "pool_status", "previous_defined_state", "transition"):
        states[column] = states[column].astype(object).where(states[column].notna(), None)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    states.to_parquet(cache_path, index=False)
    record_cache(cache_path, signature)
    audit = {
        "loaded_cache": False,
        "rows": int(len(states)),
        "instruments": int(states["instrument_id"].nunique()),
        "dates": int(states["trade_date"].nunique()),
        "defined_rows": int(states["defined_state"].ne("uncertain").sum()),
        "a_pool_rows": int(states["pool_status"].eq("A_STRATEGY_CANDIDATE").sum()),
        "transition_rows": int(states["transition"].ne("none").sum()),
    }
    del features
    gc.collect()
    return states, audit


def _load_demo01_samples(project_root: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    entry_root = ROOT
    if str(entry_root) not in sys.path:
        sys.path.insert(0, str(entry_root))
    from backtest_mfe_mae import load_config as load_demo01_config, resolve_from
    from v5_feature_information_audit import load_samples
    from v6_control_monitor_entry_audit import add_v5_scores, add_walkforward_pl_estimates

    config = load_demo01_config(entry_root / "config.json")
    data_root = resolve_from(entry_root, str(config["data_root"]))
    label_path = resolve_from(
        entry_root,
        str(
            config.get(
                "legacy_v5_label_path",
                "output/v5_tail_label_audit/v5_tail_labels.parquet",
            )
        ),
    )
    samples, audit = load_samples(
        data_root, pd.Timestamp(str(config["feature_start"])), label_path
    )
    extra = pd.read_parquet(
        label_path,
        columns=[
            "instrument_id",
            "trade_date",
            "v5_net_return",
            "v5_stop_pct",
            "v5_exit_idx",
        ],
    ).rename(columns={"trade_date": "signal_date"})
    extra["signal_date"] = pd.to_datetime(extra["signal_date"])
    samples = samples.merge(
        extra, on=["instrument_id", "signal_date"], how="left", validate="one_to_one"
    )
    samples = add_v5_scores(samples)
    samples, calibration = add_walkforward_pl_estimates(samples)
    audit["calibration"] = calibration
    return samples, audit


def _trade_statistics(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "trades": 0,
            "mean_net_return": math.nan,
            "mean_net_r": math.nan,
            "win_rate": math.nan,
            "payoff_ratio": math.nan,
            "profit_factor": math.nan,
            "mean_tail_loss_r": math.nan,
        }
    r = frame["v5_net_r"].to_numpy(dtype=float)
    wins = r[r > 0]
    losses = r[r < 0]
    gross_loss = float(-losses.sum()) if len(losses) else 0.0
    return {
        "trades": int(len(frame)),
        "mean_net_return": float(frame["v5_net_return"].mean()),
        "mean_net_r": float(frame["v5_net_r"].mean()),
        "win_rate": float(frame["v5_net_return"].gt(0).mean()),
        "payoff_ratio": float(wins.mean() / -losses.mean())
        if len(wins) and len(losses)
        else math.nan,
        "profit_factor": float(wins.sum() / gross_loss) if gross_loss > 0 else math.inf,
        "mean_tail_loss_r": float(frame["v5_tail_loss_r"].mean()),
    }


def cohort_metrics(samples: pd.DataFrame, strategies: dict[str, str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    periods = {
        "development_2023_2024": DEVELOPMENT_YEARS,
        "diagnostic_2025_2026": DIAGNOSTIC_YEARS,
    }
    for period, years in periods.items():
        period_mask = samples["signal_date"].dt.year.isin(years)
        for strategy, column in strategies.items():
            stats = _trade_statistics(samples[period_mask & samples[column]])
            rows.append({"period": period, "strategy": strategy, **stats})
        for year in years:
            year_mask = samples["signal_date"].dt.year.eq(year)
            for strategy, column in strategies.items():
                stats = _trade_statistics(samples[year_mask & samples[column]])
                rows.append({"period": str(year), "strategy": strategy, **stats})
    return pd.DataFrame(rows)


def matched_daily_metrics(
    samples: pd.DataFrame, strategies: dict[str, str]
) -> tuple[pd.DataFrame, dict[str, Any]]:
    entry_root = ROOT
    if str(entry_root) not in sys.path:
        sys.path.insert(0, str(entry_root))
    from v5_feature_information_audit import newey_west_mean_test

    daily_rows: list[dict[str, Any]] = []
    for strategy, column in strategies.items():
        selected = samples[samples[column]]
        for date, candidates in selected.groupby("signal_date", sort=True):
            universe = samples[samples["signal_date"].eq(date)]
            if universe.empty:
                continue
            daily_rows.append(
                {
                    "signal_date": date,
                    "year": int(date.year),
                    "strategy": strategy,
                    "signals": int(len(candidates)),
                    "signal_mean_net_r": float(candidates["v5_net_r"].mean()),
                    "universe_mean_net_r": float(universe["v5_net_r"].mean()),
                    "signal_mean_tail_loss_r": float(candidates["v5_tail_loss_r"].mean()),
                    "universe_mean_tail_loss_r": float(universe["v5_tail_loss_r"].mean()),
                }
            )
    daily = pd.DataFrame(daily_rows)
    if daily.empty:
        return daily, {}
    daily["net_r_difference"] = daily["signal_mean_net_r"] - daily["universe_mean_net_r"]
    daily["tail_loss_difference"] = (
        daily["signal_mean_tail_loss_r"] - daily["universe_mean_tail_loss_r"]
    )
    summary: dict[str, Any] = {}
    for strategy, group in daily.groupby("strategy", sort=False):
        summary[strategy] = {}
        for period, years in {
            "development_2023_2024": DEVELOPMENT_YEARS,
            "diagnostic_2025_2026": DIAGNOSTIC_YEARS,
        }.items():
            work = group[group["year"].isin(years)]
            mean_r, t_r, p_r = newey_west_mean_test(work["net_r_difference"])
            mean_tail, t_tail, p_tail = newey_west_mean_test(work["tail_loss_difference"])
            summary[strategy][period] = {
                "days": int(len(work)),
                "signals": int(work["signals"].sum()),
                "mean_net_r_difference": mean_r,
                "net_r_hac_t": t_r,
                "net_r_hac_p": p_r,
                "mean_tail_loss_difference": mean_tail,
                "tail_hac_t": t_tail,
                "tail_hac_p": p_tail,
                "better_day_rate": float(work["net_r_difference"].gt(0).mean())
                if len(work)
                else math.nan,
            }
    return daily, summary


def strategy_decision(
    cohort: pd.DataFrame, daily_summary: dict[str, Any], strategy: str
) -> dict[str, Any]:
    rows = cohort[
        cohort["strategy"].eq(strategy)
        & cohort["period"].isin(["development_2023_2024", "diagnostic_2025_2026"])
    ].set_index("period")
    required_periods = ["development_2023_2024", "diagnostic_2025_2026"]
    if not all(period in rows.index for period in required_periods):
        return {"passed": False, "reason": "missing_period"}
    checks = {}
    for period in required_periods:
        row = rows.loc[period]
        daily = daily_summary.get(strategy, {}).get(period, {})
        checks[period] = {
            "trades_at_least_30": bool(row["trades"] >= MIN_STRATEGY_TRADES),
            "mean_net_r_positive": bool(row["mean_net_r"] > 0),
            "profit_factor_at_least_1_2": bool(row["profit_factor"] >= MIN_PROFIT_FACTOR),
            "increment_positive": bool(daily.get("mean_net_r_difference", math.nan) > 0),
            "increment_hac_p_below_0_05": bool(daily.get("net_r_hac_p", math.nan) < 0.05),
        }
    passed = all(all(values.values()) for values in checks.values())
    return {"passed": passed, "checks": checks}


def render_report(
    summary: dict[str, Any], cohort: pd.DataFrame, yearly: pd.DataFrame
) -> str:
    decision = summary["decision"]
    return "\n".join(
        [
            "# 阶段状态与转换的可执行回测",
            "",
            f"**最终结论：{decision['status']}**",
            "",
            decision["detail"],
            "",
            "## 候选级统计",
            "",
            cohort.to_markdown(index=False, floatfmt=".4f"),
            "",
            "## 单持仓逐年结果",
            "",
            yearly.to_markdown(index=False, floatfmt=".4f") if len(yearly) else "没有可执行交易。",
            "",
            "## 冻结口径",
            "",
            "- 公告信息仅在公告日及其后使用；交易结果采用demo01 V5的T+1开盘、精确涨跌停、停牌延期、ATR止损和费用标签。",
            "- 状态须最高分≥65、分差≥10、覆盖≥80%、最近5日至少3日一致。",
            "- 锁仓/拉升只有可信度≥80且20日日均成交额≥1亿元才进入A级；吸筹/派发因缺逐日资金流只能为B级。",
            "- 状态转换与上个有效状态的间隔不超过45天。",
            "- 策略通过要求开发期和历史诊断期均不少于30笔、平均净R>0、利润因子≥1.2，且同日增量HAC p<0.05。",
            "",
            "## 研究边界",
            "",
            "2025—2026已被前序研究查看，本报告不是新的独立留出检验。即使历史门槛通过，也只能进入2026-08-11之后的前向观察，不能直接实盘授权。",
            "",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/ownership_state.json")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--rebuild-state-cache", action="store_true")
    parser.add_argument(
        "--state-cache-only",
        action="store_true",
        help="Build the point-in-time state/transition cache and skip outcome diagnostics.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config((ROOT / args.config).resolve())
    project_root = ROOT.parent
    market_root = resolve(config["market_data_root"])
    end = pd.Timestamp(str(config["research_end"]))
    output_dir = (
        Path(args.output_dir).resolve()
        if args.output_dir
        else resolve(config["output_dir"]) / "state_transition_backtest"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    events = pd.read_parquet(resolve(config["processed_data_dir"]) / "ownership_events.parquet")
    events["ann_date"] = pd.to_datetime(events["ann_date"])
    events = events[events["ann_date"].le(end)].copy()
    states, state_audit = build_or_load_state_history(
        output_dir / "daily_state_cache.parquet",
        market_root,
        events,
        end,
        args.rebuild_state_cache,
    )

    if args.state_cache_only:
        current = states[states["trade_date"].eq(end)].copy()
        current.to_parquet(output_dir / "current_state_transitions.parquet", index=False)
        current.to_csv(
            output_dir / "current_state_transitions.csv",
            index=False,
            encoding="utf-8-sig",
        )
        current_summary = {
            "as_of_date": str(end.date()),
            "state_audit": state_audit,
            "current_rows": int(len(current)),
            "current_pool_counts": {
                str(key): int(value)
                for key, value in current["pool_status"].value_counts().items()
            },
            "current_defined_state_counts": {
                str(key): int(value)
                for key, value in current["defined_state"].value_counts().items()
            },
            "current_transition_counts": {
                str(key): int(value)
                for key, value in current.loc[
                    current["transition"].ne("none"), "transition"
                ].value_counts().items()
            },
        }
        (output_dir / "current_transition_summary.json").write_text(
            json.dumps(_json_value(current_summary), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps(_json_value(current_summary), ensure_ascii=False, indent=2))
        return 0

    print("Loading strict demo01 samples", flush=True)
    samples, sample_audit = _load_demo01_samples(project_root)
    state_signals = states[states["defined_state"].ne("uncertain")].copy()
    state_signals = state_signals.rename(columns={"trade_date": "signal_date"})
    signal_columns = [
        "instrument_id",
        "signal_date",
        "defined_state",
        "pool_status",
        "confidence",
        "state_score",
        "state_margin",
        "stable_days_5",
        "previous_defined_state",
        "transition_gap_days",
        "transition",
    ]
    samples = samples.merge(
        state_signals[signal_columns],
        on=["instrument_id", "signal_date"],
        how="left",
        validate="one_to_one",
    )
    samples["defined_state"] = samples["defined_state"].fillna("uncertain")
    samples["pool_status"] = samples["pool_status"].fillna("REMOVED")
    samples["transition"] = samples["transition"].fillna("none")

    samples["static_a_markup"] = (
        samples["pool_status"].eq("A_STRATEGY_CANDIDATE")
        & samples["defined_state"].eq("markup")
    )
    samples["static_a_lock"] = (
        samples["pool_status"].eq("A_STRATEGY_CANDIDATE")
        & samples["defined_state"].eq("lock")
    )
    samples["lock_to_markup"] = (
        samples["static_a_markup"] & samples["transition"].eq("lock_to_markup")
    )
    samples["markup_to_distribution"] = samples["transition"].eq(
        "markup_to_distribution"
    )
    samples["any_to_distribution"] = (
        samples["defined_state"].eq("distribution") & samples["transition"].ne("none")
    )
    samples["lock_to_markup_reward_top10"] = (
        samples["lock_to_markup"] & samples["reward_top10_gate"]
    )
    samples["lock_to_markup_dual_top10"] = (
        samples["lock_to_markup"] & samples["dual_top10_gate"]
    )
    samples["lock_to_markup_pl_1_2"] = (
        samples["lock_to_markup"] & samples["demo01_pl_gate"]
    )
    # Compatibility field used only in the reused single-position trade log.
    # Position selection and returns do not depend on this value.
    samples["control_age_days"] = samples["transition_gap_days"]

    strategies = {
        "static_a_markup": "static_a_markup",
        "static_a_lock": "static_a_lock",
        "lock_to_markup": "lock_to_markup",
        "lock_to_markup_plus_reward_top10": "lock_to_markup_reward_top10",
        "lock_to_markup_plus_dual_top10": "lock_to_markup_dual_top10",
        "lock_to_markup_plus_pl_1_2": "lock_to_markup_pl_1_2",
        "markup_to_distribution_risk": "markup_to_distribution",
        "any_to_distribution_risk": "any_to_distribution",
    }
    cohort = cohort_metrics(samples, strategies)
    daily, daily_summary = matched_daily_metrics(samples, strategies)
    transition_decision = strategy_decision(cohort, daily_summary, "lock_to_markup")
    reward_decision = strategy_decision(
        cohort, daily_summary, "lock_to_markup_plus_reward_top10"
    )
    pl_decision = strategy_decision(
        cohort, daily_summary, "lock_to_markup_plus_pl_1_2"
    )

    entry_root = ROOT
    if str(entry_root) not in sys.path:
        sys.path.insert(0, str(entry_root))
    from v6_control_monitor_entry_audit import simulate_single_position

    trade_parts: list[pd.DataFrame] = []
    yearly_parts: list[pd.DataFrame] = []
    portfolio_summary: dict[str, Any] = {}
    portfolio_strategies = {
        "lock_to_markup": "lock_to_markup",
        "lock_to_markup_plus_reward_top10": "lock_to_markup_reward_top10",
        "lock_to_markup_plus_dual_top10": "lock_to_markup_dual_top10",
        "lock_to_markup_plus_pl_1_2": "lock_to_markup_pl_1_2",
    }
    for strategy, column in portfolio_strategies.items():
        trades, stats, yearly = simulate_single_position(samples, column)
        trades["strategy"] = strategy
        yearly["strategy"] = strategy
        trade_parts.append(trades)
        yearly_parts.append(yearly)
        portfolio_summary[strategy] = stats
    trades = pd.concat(trade_parts, ignore_index=True) if trade_parts else pd.DataFrame()
    yearly = pd.concat(yearly_parts, ignore_index=True) if yearly_parts else pd.DataFrame()

    authorized = bool(pl_decision.get("passed", False))
    diagnostic_pass = bool(
        transition_decision.get("passed", False) or reward_decision.get("passed", False)
    )
    decision = {
        "status": (
            "FORWARD_MONITOR_ONLY"
            if diagnostic_pass and not authorized
            else ("HISTORICAL_ENTRY_GATE_PASSED" if authorized else "NO_ENTRY_AUTHORIZATION")
        ),
        "detail": (
            "锁仓转拉升在历史门槛上有诊断价值，但demo01盈亏比1.2联合条件未通过；只建立前向监控，不授权入场。"
            if diagnostic_pass and not authorized
            else (
                "锁仓转拉升与demo01严格盈亏比条件同时通过历史门槛；仍须从2026-08-11之后前向确认。"
                if authorized
                else "锁仓转拉升没有同时通过开发期、历史诊断期和同日增量门槛，当前定义池不能直接落地为入场策略。"
            )
        ),
        "lock_to_markup": transition_decision,
        "lock_to_markup_plus_reward_top10": reward_decision,
        "lock_to_markup_plus_pl_1_2": pl_decision,
        "not_an_untouched_holdout": True,
    }
    summary = {
        "decision": decision,
        "state_audit": state_audit,
        "sample_audit": sample_audit,
        "matched_daily": daily_summary,
        "single_position": portfolio_summary,
        "rules": {
            "development_years": DEVELOPMENT_YEARS,
            "diagnostic_years": DIAGNOSTIC_YEARS,
            "maximum_transition_gap_days": MAX_TRANSITION_GAP_DAYS,
            "minimum_strategy_trades": MIN_STRATEGY_TRADES,
            "minimum_profit_factor": MIN_PROFIT_FACTOR,
        },
    }
    cohort.to_csv(output_dir / "cohort_metrics.csv", index=False, encoding="utf-8-sig")
    daily.to_parquet(output_dir / "matched_daily_metrics.parquet", index=False)
    candidate_columns = [
        "signal_date",
        "instrument_id",
        "defined_state",
        "pool_status",
        "confidence",
        "state_score",
        "state_margin",
        "previous_defined_state",
        "transition_gap_days",
        "transition",
        "net_r_percentile",
        "tail_loss_r_percentile",
        "estimated_pl_ratio",
        "estimated_expectancy_r",
        "v5_net_return",
        "v5_net_r",
        "v5_tail_loss_r",
    ]
    samples.loc[
        samples["static_a_markup"]
        | samples["static_a_lock"]
        | samples["transition"].ne("none"),
        candidate_columns,
    ].to_parquet(output_dir / "candidate_rows.parquet", index=False)
    trades.to_csv(output_dir / "single_position_trades.csv", index=False, encoding="utf-8-sig")
    yearly.to_csv(output_dir / "single_position_yearly.csv", index=False, encoding="utf-8-sig")
    (output_dir / "summary.json").write_text(
        json.dumps(_json_value(summary), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "report.md").write_text(
        render_report(summary, cohort, yearly), encoding="utf-8"
    )
    print(json.dumps(_json_value(decision), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
