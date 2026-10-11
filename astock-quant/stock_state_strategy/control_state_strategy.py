"""Point-in-time research for accumulation, lock-up and distribution states.

The scores in this module are observable proxies, not proof that a person or
account group controls a security.  All historical features stop at the market
close before an announcement.  Future columns are used only as evaluation
labels.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq
from scipy.stats import spearmanr

from ml_incremental_test import prediction_metrics
from ownership_pipeline import ROOT, date_filter, load_config, resolve


HISTORICAL_FEATURES = (
    "price_position_250",
    "ret_20_behavior",
    "ret_60_behavior",
    "trend_efficiency_20",
    "volume_ratio_5_60",
    "turnover_ratio_5_60",
    "up_down_volume_ratio_20",
    "pullback_volume_ratio_20",
    "amihud_20",
    "impact_asymmetry_20",
    "volatility_ratio_20_60",
    "distance_ma_60",
    "breakout_60",
    "momentum_slowdown",
    "turnover_mean_60",
)

FUTURE_BEHAVIOR_LABELS = (
    "future_turnover_ratio_20_60",
    "future_amihud_change_20",
    "future_floor_return_20",
    "future_lock_behavior",
)

SCORE_COLUMNS = (
    "ownership_concentration_score",
    "market_control_score",
    "combined_control_score",
    "discovery_selected_behavior_score",
)

DIAGNOSTIC_SIGNALS = (
    "r_holder_concentration",
    "r_average_holding",
    "r_top10_float",
    "r_top10_hhi",
    "r_position_accumulation",
    "r_position_lock",
    "r_trend_20",
    "r_trend_60",
    "r_trend_efficiency",
    "r_volume_accumulation",
    "r_turnover_activation",
    "r_turnover_lock",
    "r_price_impact",
    "r_impact_asymmetry",
    "r_volatility_contraction",
    "distribution_avoidance",
)


def _safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    denominator = denominator.where(np.isfinite(denominator) & (denominator != 0))
    return numerator / denominator


def _future_rolling(series: pd.Series, window: int, operation: str) -> pd.Series:
    """Apply a rolling operation to sessions t+1 ... t+window."""
    reversed_future = series.shift(-1).iloc[::-1]
    rolling = reversed_future.rolling(window, min_periods=max(3, window - 5))
    if operation == "mean":
        values = rolling.mean()
    elif operation == "min":
        values = rolling.min()
    else:
        raise ValueError(f"Unsupported future rolling operation: {operation}")
    return values.iloc[::-1]


def compute_symbol_behavior(bars: pd.DataFrame) -> pd.DataFrame:
    """Calculate historical features and independent future behavior labels."""
    work = bars.sort_values("trade_date", kind="stable").copy()
    close = pd.to_numeric(work["close"], errors="coerce")
    factor = pd.to_numeric(work["adjustment_factor"], errors="coerce")
    amount = pd.to_numeric(work["turnover_cny"], errors="coerce")
    turnover = pd.to_numeric(work["turnover_rate_pct"], errors="coerce")
    adjusted_close = close * factor
    daily_return = adjusted_close.pct_change(fill_method=None)

    ret_20 = adjusted_close.pct_change(20, fill_method=None)
    ret_60 = adjusted_close.pct_change(60, fill_method=None)
    abs_path_20 = daily_return.abs().rolling(20, min_periods=15).sum()
    rolling_low_250 = adjusted_close.rolling(250, min_periods=200).min()
    rolling_high_250 = adjusted_close.rolling(250, min_periods=200).max()
    amount_mean_5 = amount.rolling(5, min_periods=4).mean()
    amount_mean_60 = amount.rolling(60, min_periods=45).mean()
    turnover_mean_5 = turnover.rolling(5, min_periods=4).mean()
    turnover_mean_60 = turnover.rolling(60, min_periods=45).mean()

    up = daily_return > 0
    down = daily_return < 0
    up_amount_mean = _safe_divide(
        amount.where(up, 0.0).rolling(20, min_periods=15).sum(),
        up.astype(float).rolling(20, min_periods=15).sum(),
    )
    down_amount_mean = _safe_divide(
        amount.where(down, 0.0).rolling(20, min_periods=15).sum(),
        down.astype(float).rolling(20, min_periods=15).sum(),
    )

    amount_safe = amount.where(amount > 0)
    turnover_safe = turnover.where(turnover > 0)
    daily_amihud = daily_return.abs() / amount_safe * 100_000_000.0
    up_impact = _safe_divide(daily_return.clip(lower=0), turnover_safe)
    down_impact = _safe_divide((-daily_return).clip(lower=0), turnover_safe)
    up_impact_mean = up_impact.where(up).rolling(20, min_periods=8).mean()
    down_impact_mean = down_impact.where(down).rolling(20, min_periods=8).mean()
    volatility_20 = daily_return.rolling(20, min_periods=15).std()
    volatility_60 = daily_return.rolling(60, min_periods=45).std()
    previous_high_60 = adjusted_close.shift(1).rolling(60, min_periods=45).max()
    comparable_ret_20 = np.power((1.0 + ret_60).clip(lower=1e-6), 1.0 / 3.0) - 1.0

    output = work[["vendor_symbol", "trade_date"]].copy()
    output["price_position_250"] = _safe_divide(
        adjusted_close - rolling_low_250,
        rolling_high_250 - rolling_low_250,
    ).clip(0.0, 1.0)
    output["ret_20_behavior"] = ret_20
    output["ret_60_behavior"] = ret_60
    output["trend_efficiency_20"] = _safe_divide(ret_20, abs_path_20).clip(-1.0, 1.0)
    output["volume_ratio_5_60"] = _safe_divide(amount_mean_5, amount_mean_60)
    output["turnover_ratio_5_60"] = _safe_divide(turnover_mean_5, turnover_mean_60)
    output["up_down_volume_ratio_20"] = _safe_divide(up_amount_mean, down_amount_mean)
    output["pullback_volume_ratio_20"] = _safe_divide(down_amount_mean, up_amount_mean)
    output["amihud_20"] = daily_amihud.rolling(20, min_periods=15).mean()
    output["impact_asymmetry_20"] = _safe_divide(up_impact_mean, down_impact_mean)
    output["volatility_ratio_20_60"] = _safe_divide(volatility_20, volatility_60)
    output["distance_ma_60"] = _safe_divide(
        adjusted_close, adjusted_close.rolling(60, min_periods=45).mean()
    ) - 1.0
    output["breakout_60"] = _safe_divide(adjusted_close, previous_high_60) - 1.0
    output["momentum_slowdown"] = ret_20 - comparable_ret_20
    output["turnover_mean_60"] = turnover_mean_60

    future_turnover_mean = _future_rolling(turnover, 20, "mean")
    future_amihud_mean = _future_rolling(daily_amihud, 20, "mean")
    future_min_close = _future_rolling(adjusted_close, 20, "min")
    output["future_turnover_ratio_20_60"] = _safe_divide(
        future_turnover_mean, turnover_mean_60
    )
    output["future_amihud_change_20"] = _safe_divide(
        future_amihud_mean, output["amihud_20"]
    )
    output["future_floor_return_20"] = _safe_divide(
        future_min_close, adjusted_close
    ) - 1.0
    return output.replace([np.inf, -np.inf], np.nan)


def load_event_behavior(
    events: pd.DataFrame, config: dict, cache_path: Path, rebuild: bool = False
) -> pd.DataFrame:
    """Read market bars once and retain calculated rows used by disclosures."""
    if cache_path.exists() and not rebuild:
        return pd.read_parquet(cache_path)

    market_root = resolve(config["market_data_root"])
    start = pd.Timestamp(config["research_start"]) - pd.Timedelta(days=500)
    end = pd.Timestamp(config["research_end"])
    predicate = date_filter("trade_date", start, end)
    prices = ds.dataset(
        market_root / "daily_prices", format="parquet", partitioning="hive"
    ).to_table(
        columns=[
            "instrument_id",
            "vendor_symbol",
            "trade_date",
            "close",
            "turnover_cny",
            "is_quality_valid",
        ],
        filter=predicate,
    ).to_pandas()
    metrics = ds.dataset(
        market_root / "daily_metrics", format="parquet", partitioning="hive"
    ).to_table(
        columns=["instrument_id", "trade_date", "turnover_rate_pct"],
        filter=predicate,
    ).to_pandas()
    factors = ds.dataset(
        market_root / "adjustment_factors", format="parquet", partitioning="hive"
    ).to_table(
        columns=["instrument_id", "trade_date", "adjustment_factor"],
        filter=predicate,
    ).to_pandas()
    panel = prices.merge(
        metrics,
        on=["instrument_id", "trade_date"],
        how="left",
        validate="one_to_one",
    ).merge(
        factors,
        on=["instrument_id", "trade_date"],
        how="left",
        validate="one_to_one",
    )
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    panel = panel[panel["is_quality_valid"].fillna(False)].copy()
    panel = panel.sort_values(["vendor_symbol", "trade_date"], kind="stable")

    required_dates = (
        events[["vendor_symbol", "trade_date"]]
        .dropna()
        .drop_duplicates()
        .assign(trade_date=lambda x: pd.to_datetime(x["trade_date"]))
    )
    date_map = {
        symbol: set(group["trade_date"].to_numpy(dtype="datetime64[ns]"))
        for symbol, group in required_dates.groupby("vendor_symbol", sort=False)
    }
    selected_parts: list[pd.DataFrame] = []
    for symbol, bars in panel.groupby("vendor_symbol", sort=False):
        dates = date_map.get(symbol)
        if not dates:
            continue
        calculated = compute_symbol_behavior(bars)
        mask = calculated["trade_date"].isin(dates)
        if mask.any():
            selected_parts.append(calculated.loc[mask])
    if not selected_parts:
        raise RuntimeError("No event market rows were produced")
    behavior = pd.concat(selected_parts, ignore_index=True)
    behavior = behavior.drop_duplicates(["vendor_symbol", "trade_date"], keep="last")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    behavior.to_parquet(cache_path, index=False)
    return behavior


def _cross_section_rank(frame: pd.DataFrame, values: pd.Series) -> pd.Series:
    temporary = pd.DataFrame(
        {"ann_date": frame["ann_date"].to_numpy(), "value": values.to_numpy()},
        index=frame.index,
    )
    return temporary.groupby("ann_date", sort=False)["value"].rank(pct=True)


def _mean_available(frame: pd.DataFrame, columns: list[str], minimum: int) -> pd.Series:
    valid_count = frame[columns].notna().sum(axis=1)
    score = frame[columns].mean(axis=1, skipna=True)
    return score.where(valid_count >= minimum)


def build_scores(events: pd.DataFrame, behavior: pd.DataFrame, config: dict) -> pd.DataFrame:
    frame = events.copy()
    frame["ann_date"] = pd.to_datetime(frame["ann_date"])
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    frame = frame.merge(
        behavior,
        on=["vendor_symbol", "trade_date"],
        how="left",
        validate="many_to_one",
    )
    frame = frame[
        frame["new_holder_disclosure"].fillna(False).astype(bool)
        & frame["holder_num_change_rate"].notna()
        & frame["future_20d_net_return"].notna()
        & (frame["listing_sessions"] >= int(config["minimum_listing_sessions"]))
        & (
            frame["average_turnover_cny_20"]
            >= float(config["minimum_average_turnover_cny"])
        )
        & frame["holder_num_change_rate"].between(-0.8, 5.0)
    ].copy()

    industry_keys = ["trade_date", "industry_vendor"]
    for horizon in (20, 60):
        source = f"ret_{horizon}_behavior"
        median = frame.groupby(industry_keys, observed=True)[source].transform("median")
        market_median = frame.groupby("trade_date", observed=True)[source].transform("median")
        frame[f"excess_ret_{horizon}"] = frame[source] - median.fillna(market_median)

    frame["position_fit_accumulation"] = (
        1.0 - (frame["price_position_250"] - 0.45).abs() / 0.55
    ).clip(0.0, 1.0)
    frame["position_fit_lock"] = (
        1.0 - (frame["price_position_250"] - 0.70).abs() / 0.70
    ).clip(0.0, 1.0)
    frame["turnover_activation_fit"] = -np.abs(
        np.log(frame["turnover_ratio_5_60"].clip(0.05, 20.0) / 1.25)
    )

    ranking_inputs = {
        "r_holder_concentration": -frame["holder_num_change_rate"].clip(-0.8, 5.0),
        "r_average_holding": np.log1p(frame["average_holding_shares"].clip(lower=0)),
        "r_top10_float": frame["top10_float_pct"],
        "r_top10_hhi": frame["top10_hhi"],
        "r_position_accumulation": frame["position_fit_accumulation"],
        "r_position_lock": frame["position_fit_lock"],
        "r_trend_20": frame["excess_ret_20"],
        "r_trend_60": frame["excess_ret_60"],
        "r_trend_efficiency": frame["trend_efficiency_20"],
        "r_volume_accumulation": frame["up_down_volume_ratio_20"],
        "r_turnover_activation": frame["turnover_activation_fit"],
        "r_turnover_lock": -np.log(frame["turnover_ratio_5_60"].clip(0.05, 20.0)),
        "r_price_impact": np.log1p(frame["amihud_20"].clip(lower=0)),
        "r_impact_asymmetry": np.log(
            frame["impact_asymmetry_20"].clip(0.05, 20.0)
        ),
        "r_volatility_contraction": -np.log(
            frame["volatility_ratio_20_60"].clip(0.05, 20.0)
        ),
        "r_high_position": frame["price_position_250"],
        "r_turnover_spike": np.log(frame["turnover_ratio_5_60"].clip(0.05, 20.0)),
        "r_volume_spike": np.log(frame["volume_ratio_5_60"].clip(0.05, 20.0)),
        "r_pullback_volume": np.log(
            frame["pullback_volume_ratio_20"].clip(0.05, 20.0)
        ),
        "r_momentum_slowdown": -frame["momentum_slowdown"],
    }
    for name, values in ranking_inputs.items():
        frame[name] = _cross_section_rank(frame, values)

    frame["ownership_concentration_score"] = _mean_available(
        frame,
        ["r_holder_concentration", "r_average_holding", "r_top10_float", "r_top10_hhi"],
        2,
    )
    frame["accumulation_score"] = _mean_available(
        frame,
        [
            "r_holder_concentration",
            "r_position_accumulation",
            "r_trend_20",
            "r_trend_efficiency",
            "r_volume_accumulation",
            "r_turnover_activation",
        ],
        4,
    )
    frame["lock_score"] = _mean_available(
        frame,
        [
            "r_holder_concentration",
            "r_position_lock",
            "r_trend_60",
            "r_trend_efficiency",
            "r_turnover_lock",
            "r_price_impact",
            "r_impact_asymmetry",
            "r_volatility_contraction",
        ],
        5,
    )
    frame["distribution_risk"] = _mean_available(
        frame,
        [
            "r_high_position",
            "r_turnover_spike",
            "r_volume_spike",
            "r_pullback_volume",
            "r_momentum_slowdown",
        ],
        4,
    )
    frame["distribution_avoidance"] = 1.0 - frame["distribution_risk"]
    frame["market_control_score"] = (
        0.45 * frame["accumulation_score"]
        + 0.55 * frame["lock_score"]
        - 0.30 * frame["distribution_risk"]
    )
    frame["combined_control_score"] = (
        0.35 * frame["ownership_concentration_score"]
        + 0.65 * frame["market_control_score"]
    )

    future_rank_inputs = {
        "future_turnover_lock_rank": -np.log(
            frame["future_turnover_ratio_20_60"].clip(0.05, 20.0)
        ),
        "future_amihud_rank": np.log(
            frame["future_amihud_change_20"].clip(0.05, 20.0)
        ),
        "future_floor_rank": frame["future_floor_return_20"],
    }
    for name, values in future_rank_inputs.items():
        frame[name] = _cross_section_rank(frame, values)
    frame["future_lock_behavior"] = _mean_available(
        frame,
        ["future_turnover_lock_rank", "future_amihud_rank", "future_floor_rank"],
        3,
    )

    states = np.full(len(frame), "normal", dtype=object)
    distribution = frame["distribution_risk"].fillna(-np.inf)
    accumulation = frame["accumulation_score"].fillna(-np.inf)
    lock = frame["lock_score"].fillna(-np.inf)
    states[(accumulation >= 0.65) & (accumulation >= lock) & (accumulation > distribution)] = "accumulation"
    states[(lock >= 0.65) & (lock > accumulation) & (lock > distribution)] = "lock"
    states[(distribution >= 0.65) & (distribution >= accumulation) & (distribution >= lock)] = "distribution"
    frame["control_state"] = states
    return frame.sort_values(["ann_date", "vendor_symbol"], kind="stable").reset_index(drop=True)


def top_bottom_metrics(
    frame: pd.DataFrame, score: str, target: str, minimum_cross_section: int
) -> dict:
    daily_rows = []
    for date, group in frame.groupby("ann_date", sort=True):
        work = group[[score, target]].dropna()
        if len(work) < minimum_cross_section or work[score].nunique() < 3:
            continue
        count = max(1, int(np.ceil(len(work) * 0.2)))
        top = work.nlargest(count, score)[target].mean()
        bottom = work.nsmallest(count, score)[target].mean()
        daily_rows.append((date, top, bottom, top - bottom))
    daily = pd.DataFrame(daily_rows, columns=["ann_date", "top", "bottom", "spread"])
    if daily.empty:
        return {"days": 0, "top": np.nan, "bottom": np.nan, "spread": np.nan, "positive_spread_rate": np.nan}
    return {
        "days": int(len(daily)),
        "top": float(daily["top"].mean()),
        "bottom": float(daily["bottom"].mean()),
        "spread": float(daily["spread"].mean()),
        "positive_spread_rate": float((daily["spread"] > 0).mean()),
    }


def mean_daily_ic(
    frame: pd.DataFrame, score: str, target: str, minimum_cross_section: int
) -> float:
    values = []
    for _, group in frame.groupby("ann_date", sort=False):
        work = group[[score, target]].dropna()
        if len(work) >= minimum_cross_section and work[score].nunique() >= 3:
            values.append(float(spearmanr(work[score], work[target]).statistic))
    return float(np.mean(values)) if values else np.nan


def add_discovery_selected_score(
    frame: pd.DataFrame, config: dict
) -> tuple[pd.DataFrame, list[str]]:
    """Select behavior features using discovery data only, then freeze equal weights."""
    result = frame.copy()
    discovery = result[result["ann_date"] <= pd.Timestamp(config["discovery_end"])]
    minimum = int(config["minimum_cross_section"])
    selected = []
    for signal in DIAGNOSTIC_SIGNALS:
        ic = mean_daily_ic(discovery, signal, "future_lock_behavior", minimum)
        spread = top_bottom_metrics(
            discovery, signal, "future_lock_behavior", minimum
        )["spread"]
        if np.isfinite(ic) and ic >= 0.03 and np.isfinite(spread) and spread > 0:
            selected.append(signal)
    if not selected:
        raise RuntimeError("Discovery period selected no control-behavior signals")
    result["discovery_selected_behavior_score"] = _mean_available(
        result, selected, max(2, len(selected) - 1)
    )
    return result, selected


def evaluate(
    frame: pd.DataFrame, config: dict, selected_signals: list[str]
) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    masks = {
        "discovery": frame["ann_date"] <= pd.Timestamp(config["discovery_end"]),
        "validation": (frame["ann_date"] >= pd.Timestamp(config["validation_start"]))
        & (frame["ann_date"] <= pd.Timestamp(config["validation_end"])),
        "holdout": frame["ann_date"] >= pd.Timestamp(config["holdout_start"]),
    }
    minimum = int(config["minimum_cross_section"])
    seed = int(config["random_seed"])
    summary: dict[str, dict] = {}
    comparison_rows = []
    yearly_rows = []
    for split, mask in masks.items():
        sample = frame[mask].copy()
        summary[split] = {}
        for score in SCORE_COLUMNS:
            return_metrics = prediction_metrics(
                sample,
                score,
                "future_20d_net_return",
                minimum,
                seed + {"discovery": 0, "validation": 1, "holdout": 2}[split],
            )
            lock_metrics = top_bottom_metrics(
                sample, score, "future_lock_behavior", minimum
            )
            lock_ic = mean_daily_ic(sample, score, "future_lock_behavior", minimum)
            summary[split][score] = {
                "return": return_metrics,
                "future_lock_behavior": {"daily_rank_ic": lock_ic, **lock_metrics},
            }
            comparison_rows.append(
                {
                    "split": split,
                    "score": score,
                    "events": return_metrics["sample_count"],
                    "return_daily_rank_ic": return_metrics["mean_daily_rank_ic"],
                    "top20_net_return": return_metrics["selected_mean_net_return"],
                    "all_event_net_return": return_metrics["all_event_mean_net_return"],
                    "return_random_percentile": return_metrics["random_percentile"],
                    "lock_behavior_daily_rank_ic": lock_ic,
                    "lock_behavior_top_bottom": lock_metrics["spread"],
                }
            )

            for year, year_sample in sample.groupby(sample["ann_date"].dt.year):
                metrics = top_bottom_metrics(
                    year_sample, score, "future_20d_net_return", minimum
                )
                yearly_rows.append(
                    {"split": split, "year": int(year), "score": score, **metrics}
                )

    primary_score = "discovery_selected_behavior_score"
    validation = summary["validation"][primary_score]
    holdout = summary["holdout"][primary_score]
    holdout_market = summary["holdout"]["market_control_score"]
    summary["decision"] = {
        "primary_score": primary_score,
        "discovery_selected_signals": selected_signals,
        "passes_control_identification_gate": bool(
            validation["future_lock_behavior"]["daily_rank_ic"] >= 0.03
            and holdout["future_lock_behavior"]["daily_rank_ic"] >= 0.03
            and validation["future_lock_behavior"]["spread"] > 0
            and holdout["future_lock_behavior"]["spread"] > 0
        ),
        "passes_standalone_return_gate": bool(
            validation["return"]["random_percentile"] >= 0.95
            and holdout["return"]["random_percentile"] >= 0.95
            and validation["return"]["selected_mean_net_return"]
            > validation["return"]["all_event_mean_net_return"]
            and holdout["return"]["selected_mean_net_return"]
            > holdout["return"]["all_event_mean_net_return"]
        ),
        "primary_return_ic_vs_predeclared_market_holdout": (
            holdout["return"]["mean_daily_rank_ic"]
            - holdout_market["return"]["mean_daily_rank_ic"]
        ),
        "interpretation": "diagnostic_only",
    }
    return summary, pd.DataFrame(comparison_rows), pd.DataFrame(yearly_rows)


def feature_diagnostics(frame: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Show which predeclared observable characteristics survive each time split."""
    masks = {
        "discovery": frame["ann_date"] <= pd.Timestamp(config["discovery_end"]),
        "validation": (frame["ann_date"] >= pd.Timestamp(config["validation_start"]))
        & (frame["ann_date"] <= pd.Timestamp(config["validation_end"])),
        "holdout": frame["ann_date"] >= pd.Timestamp(config["holdout_start"]),
    }
    minimum = int(config["minimum_cross_section"])
    rows = []
    for signal in DIAGNOSTIC_SIGNALS:
        for split, mask in masks.items():
            sample = frame[mask]
            return_spread = top_bottom_metrics(
                sample, signal, "future_20d_net_return", minimum
            )
            lock_spread = top_bottom_metrics(
                sample, signal, "future_lock_behavior", minimum
            )
            rows.append(
                {
                    "signal": signal,
                    "split": split,
                    "return_daily_rank_ic": mean_daily_ic(
                        sample, signal, "future_20d_net_return", minimum
                    ),
                    "return_top_bottom": return_spread["spread"],
                    "return_positive_spread_rate": return_spread[
                        "positive_spread_rate"
                    ],
                    "lock_behavior_daily_rank_ic": mean_daily_ic(
                        sample, signal, "future_lock_behavior", minimum
                    ),
                    "lock_behavior_top_bottom": lock_spread["spread"],
                    "lock_positive_spread_rate": lock_spread[
                        "positive_spread_rate"
                    ],
                }
            )
    return pd.DataFrame(rows)


def render_report(
    summary: dict,
    comparison: pd.DataFrame,
    yearly: pd.DataFrame,
    diagnostics: pd.DataFrame,
) -> str:
    decision = summary["decision"]
    primary_score = decision["primary_score"]
    primary_yearly = yearly[yearly["score"] == primary_score]
    stable = diagnostics.pivot(
        index="signal", columns="split", values="lock_behavior_daily_rank_ic"
    ).reset_index()
    return "\n".join(
        [
            "# 大资金控盘代理状态研究",
            "",
            "本研究判断的是公开数据下的吸筹、锁筹和派发概率，不是账户级控盘事实。",
            "所有市场特征截止到公告日前最后一个交易日；收益按公告后下一可交易日开盘买入、",
            "20个交易日后可执行卖出并扣除双边成本。",
            "",
            "## 三类评分对照",
            "",
            comparison.to_markdown(index=False, floatfmt=".4f"),
            "",
            "## 发现期筛选并冻结的行为特征",
            "",
            ", ".join(f"`{name}`" for name in decision["discovery_selected_signals"]),
            "",
            "## 主评分逐年Top20%-Bottom20%",
            "",
            primary_yearly.to_markdown(index=False, floatfmt=".4f"),
            "",
            "## 单项特征对未来锁筹行为的日截面IC",
            "",
            stable.to_markdown(index=False, floatfmt=".4f"),
            "",
            "## 判定",
            "",
            f"- 控盘行为识别门槛：{'通过' if decision['passes_control_identification_gate'] else '未通过'}",
            f"- 独立收益策略门槛：{'通过' if decision['passes_standalone_return_gate'] else '未通过'}",
            f"- 主评分相对预设量价评分的留出期收益IC差：{decision['primary_return_ic_vs_predeclared_market_holdout']:.4f}",
            "",
            "只有行为门槛通过时，评分才可称为可验证的筹码锁定代理；只有收益门槛也通过时，",
            "才考虑组合级资金曲线与前向仿真。未通过时只可用于诊断或风险过滤。",
            "",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", type=Path, default=ROOT / "configs" / "ownership_state.json"
    )
    parser.add_argument("--rebuild-features", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(args.config.resolve())
    processed = resolve(config["processed_data_dir"])
    events = pd.read_parquet(processed / "ownership_events.parquet")
    cache_path = processed / "control_behavior_at_events.parquet"
    behavior = load_event_behavior(events, config, cache_path, args.rebuild_features)
    scored = build_scores(events, behavior, config)
    scored, selected_signals = add_discovery_selected_score(scored, config)
    summary, comparison, yearly = evaluate(scored, config, selected_signals)
    diagnostics = feature_diagnostics(scored, config)

    output = resolve(config["output_dir"]) / "control_state"
    output.mkdir(parents=True, exist_ok=True)
    scored_columns = [
        "ann_date",
        "trade_date",
        "vendor_symbol",
        "industry_vendor",
        "control_state",
        *SCORE_COLUMNS,
        "accumulation_score",
        "lock_score",
        "distribution_risk",
        *HISTORICAL_FEATURES,
        *FUTURE_BEHAVIOR_LABELS,
        *DIAGNOSTIC_SIGNALS,
        "future_20d_net_return",
    ]
    scored[[column for column in scored_columns if column in scored]].to_parquet(
        output / "scored_events.parquet", index=False
    )
    comparison.to_csv(output / "comparison.csv", index=False)
    yearly.to_csv(output / "yearly_metrics.csv", index=False)
    diagnostics.to_csv(output / "feature_diagnostics.csv", index=False)
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output / "report.md").write_text(
        render_report(summary, comparison, yearly, diagnostics), encoding="utf-8"
    )
    print(json.dumps(summary["decision"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
