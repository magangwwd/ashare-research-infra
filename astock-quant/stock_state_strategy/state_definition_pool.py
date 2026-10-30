"""Current A-share phase-definition pool with explicit confidence controls.

The four phase scores are observable behaviour proxies.  They do not identify
an account controller and they are not estimates of a controlled-float ratio.
Only information available by the signal close is used.  Public disclosures
are joined by announcement date, never by report period end.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds

from ownership_pipeline import ROOT, load_config, resolve
from pit_name_states import historical_name_states


HISTORY_START = pd.Timestamp("2020-01-01")
MIN_LISTING_SESSIONS = 250
MIN_HISTORY_OBSERVATIONS_250 = 238
MIN_RECENT_OBSERVATIONS_20 = 18
MIN_MONITOR_TURNOVER_CNY = 30_000_000.0
MIN_STRATEGY_TURNOVER_CNY = 100_000_000.0
MAX_ONE_WORD_DAYS_20 = 3
MAX_DISCLOSURE_AGE_DAYS = 180
MIN_STATE_SCORE = 65.0
MIN_STATE_MARGIN = 10.0
MIN_SCORE_COVERAGE = 0.80
MIN_STABLE_DAYS_5 = 3
MIN_MONITOR_CONFIDENCE = 65.0
MIN_STRATEGY_CONFIDENCE = 80.0

STATE_NAMES_CN = {
    "accumulation": "吸筹",
    "lock": "锁仓",
    "markup": "拉升",
    "distribution": "派发",
    "uncertain": "无法定义",
}

# Daily large/small-order flow is not present in down_data.  Absorption and
# distribution therefore cannot enter the high-confidence strategy pool yet.
STATE_CONFIDENCE_CAPS = {
    "accumulation": 79.0,
    "lock": 90.0,
    "markup": 90.0,
    "distribution": 79.0,
    "uncertain": 49.0,
}

STATE_COMPONENTS: dict[str, tuple[tuple[str, float], ...]] = {
    "accumulation": (
        ("r_holder_concentration", 0.20),
        ("r_top10_concentration", 0.10),
        ("position_fit_accumulation", 0.20),
        ("mild_trend_fit", 0.10),
        ("r_up_down_volume", 0.15),
        ("turnover_activation_fit", 0.10),
        ("r_low_upper_shadow", 0.05),
        ("r_trend_20", 0.10),
    ),
    "lock": (
        ("r_holder_concentration", 0.15),
        ("r_top10_concentration", 0.10),
        ("position_fit_lock", 0.15),
        ("mild_trend_fit", 0.10),
        ("r_turnover_lock", 0.15),
        ("r_volatility_contraction", 0.15),
        ("r_price_impact", 0.10),
        ("r_pullback_shrink", 0.10),
    ),
    "markup": (
        ("r_breakout", 0.25),
        ("r_trend_20", 0.20),
        ("r_trend_efficiency", 0.15),
        ("early_volume_fit", 0.15),
        ("r_pullback_shrink", 0.10),
        ("r_price_impact", 0.10),
        ("r_high_position", 0.05),
    ),
    "distribution": (
        ("r_high_position", 0.15),
        ("r_turnover_spike", 0.15),
        ("r_volume_spike", 0.15),
        ("r_upper_shadow", 0.15),
        ("r_momentum_slowdown", 0.10),
        ("r_pullback_volume", 0.10),
        ("r_holder_increase", 0.15),
        ("r_top10_deconcentration", 0.05),
    ),
}


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


def _safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    denominator = denominator.where(np.isfinite(denominator) & (denominator != 0))
    return numerator / denominator


def _dataset_date_filter(field: str, start: pd.Timestamp, end: pd.Timestamp):
    return (ds.field(field) >= pa.scalar(start.date(), type=pa.date32())) & (
        ds.field(field) <= pa.scalar(end.date(), type=pa.date32())
    )


def load_market_history(
    market_root: Path, start: pd.Timestamp, end: pd.Timestamp
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    predicate = _dataset_date_filter("trade_date", start, end)
    prices = ds.dataset(
        market_root / "daily_prices", format="parquet", partitioning="hive"
    ).to_table(
        columns=[
            "instrument_id",
            "vendor_symbol",
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "turnover_cny",
            "is_quality_valid",
        ],
        filter=predicate,
    ).to_pandas()
    metrics = ds.dataset(
        market_root / "daily_metrics", format="parquet", partitioning="hive"
    ).to_table(
        columns=[
            "instrument_id",
            "trade_date",
            "turnover_rate_pct",
            "float_market_cap_cny",
        ],
        filter=predicate,
    ).to_pandas()
    factors = ds.dataset(
        market_root / "adjustment_factors", format="parquet", partitioning="hive"
    ).to_table(
        columns=["instrument_id", "trade_date", "adjustment_factor"],
        filter=predicate,
    ).to_pandas()
    limits = ds.dataset(
        market_root / "daily_limits", format="parquet", partitioning="hive"
    ).to_table(
        columns=["instrument_id", "trade_date", "up_limit", "down_limit"],
        filter=predicate,
    ).to_pandas()
    panel = prices.merge(
        metrics, on=["instrument_id", "trade_date"], how="left", validate="one_to_one"
    ).merge(
        factors, on=["instrument_id", "trade_date"], how="left", validate="one_to_one"
    ).merge(
        limits, on=["instrument_id", "trade_date"], how="left", validate="one_to_one"
    )
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    panel = panel[
        panel["is_quality_valid"].fillna(False)
        & panel["adjustment_factor"].notna()
        & panel["adjustment_factor"].gt(0)
    ].copy()

    calendar = pd.read_parquet(market_root / "trading_calendar.parquet")
    calendar["calendar_date"] = pd.to_datetime(calendar["calendar_date"])
    calendar = calendar[
        calendar["is_open"].fillna(False)
        & calendar["calendar_date"].between(start, end)
    ].sort_values("calendar_date").drop_duplicates("calendar_date").reset_index(drop=True)
    date_to_idx = pd.Series(
        np.arange(len(calendar), dtype="int32"), index=calendar["calendar_date"]
    )
    panel["trade_idx"] = panel["trade_date"].map(date_to_idx)
    panel = panel[panel["trade_idx"].notna()].copy()
    panel["trade_idx"] = panel["trade_idx"].astype("int32")
    panel = panel.sort_values(["instrument_id", "trade_idx"], kind="stable")
    instruments = pd.read_parquet(market_root / "instruments.parquet")
    return panel.reset_index(drop=True), instruments, calendar


def _calendar_observation_count(indices: np.ndarray, window: int) -> np.ndarray:
    left = np.searchsorted(indices, indices - (window - 1), side="left")
    return (np.arange(len(indices)) - left + 1).astype("int16")


def _calendar_flag_count(indices: np.ndarray, flags: np.ndarray, window: int) -> np.ndarray:
    left = np.searchsorted(indices, indices - (window - 1), side="left")
    cumulative = np.concatenate(([0], np.cumsum(flags.astype("int16"))))
    return (cumulative[np.arange(len(indices)) + 1] - cumulative[left]).astype("int16")


def compute_symbol_features(bars: pd.DataFrame) -> pd.DataFrame:
    work = bars.sort_values("trade_idx", kind="stable").copy()
    factor = pd.to_numeric(work["adjustment_factor"], errors="coerce")
    adjusted_open = pd.to_numeric(work["open"], errors="coerce") * factor
    adjusted_high = pd.to_numeric(work["high"], errors="coerce") * factor
    adjusted_low = pd.to_numeric(work["low"], errors="coerce") * factor
    adjusted_close = pd.to_numeric(work["close"], errors="coerce") * factor
    amount = pd.to_numeric(work["turnover_cny"], errors="coerce")
    turnover = pd.to_numeric(work["turnover_rate_pct"], errors="coerce")
    daily_return = adjusted_close.pct_change(fill_method=None)
    ret_20 = adjusted_close.pct_change(20, fill_method=None)
    ret_60 = adjusted_close.pct_change(60, fill_method=None)
    amount_mean_5 = amount.rolling(5, min_periods=4).mean()
    amount_mean_20 = amount.rolling(20, min_periods=18).mean()
    amount_mean_60 = amount.rolling(60, min_periods=45).mean()
    turnover_mean_5 = turnover.rolling(5, min_periods=4).mean()
    turnover_mean_60 = turnover.rolling(60, min_periods=45).mean()
    volatility_20 = daily_return.rolling(20, min_periods=15).std()
    volatility_60 = daily_return.rolling(60, min_periods=45).std()
    rolling_low_250 = adjusted_close.rolling(250, min_periods=238).min()
    rolling_high_250 = adjusted_close.rolling(250, min_periods=238).max()
    previous_high_60 = adjusted_close.shift(1).rolling(60, min_periods=45).max()
    abs_path_20 = daily_return.abs().rolling(20, min_periods=15).sum()

    up = daily_return > 0
    down = daily_return < 0
    up_amount = _safe_divide(
        amount.where(up, 0.0).rolling(20, min_periods=15).sum(),
        up.astype(float).rolling(20, min_periods=15).sum(),
    )
    down_amount = _safe_divide(
        amount.where(down, 0.0).rolling(20, min_periods=15).sum(),
        down.astype(float).rolling(20, min_periods=15).sum(),
    )
    amount_safe = amount.where(amount > 0)
    turnover_safe = turnover.where(turnover > 0)
    daily_amihud = daily_return.abs() / amount_safe * 100_000_000.0
    price_range = adjusted_high - adjusted_low
    upper_shadow = _safe_divide(
        adjusted_high - pd.concat([adjusted_open, adjusted_close], axis=1).max(axis=1),
        price_range,
    ).clip(0.0, 1.0)

    comparable_ret_20 = np.power((1.0 + ret_60).clip(lower=1e-6), 1.0 / 3.0) - 1.0
    flat = (
        np.isclose(work["open"], work["high"], atol=0.0051, rtol=0)
        & np.isclose(work["open"], work["low"], atol=0.0051, rtol=0)
        & np.isclose(work["open"], work["close"], atol=0.0051, rtol=0)
    )
    one_word = flat & (
        np.isclose(work["open"], work["up_limit"], atol=0.0051, rtol=0)
        | np.isclose(work["open"], work["down_limit"], atol=0.0051, rtol=0)
    )
    trade_indices = work["trade_idx"].to_numpy(dtype="int32")

    output = work[["instrument_id", "vendor_symbol", "trade_date", "trade_idx"]].copy()
    output["price_position_250"] = _safe_divide(
        adjusted_close - rolling_low_250, rolling_high_250 - rolling_low_250
    ).clip(0.0, 1.0)
    output["ret_20_behavior"] = ret_20
    output["ret_60_behavior"] = ret_60
    output["trend_efficiency_20"] = _safe_divide(ret_20, abs_path_20).clip(-1.0, 1.0)
    output["volume_ratio_5_60"] = _safe_divide(amount_mean_5, amount_mean_60)
    output["turnover_ratio_5_60"] = _safe_divide(turnover_mean_5, turnover_mean_60)
    output["up_down_volume_ratio_20"] = _safe_divide(up_amount, down_amount)
    output["pullback_volume_ratio_20"] = _safe_divide(down_amount, up_amount)
    output["amihud_20"] = daily_amihud.rolling(20, min_periods=15).mean()
    output["volatility_ratio_20_60"] = _safe_divide(volatility_20, volatility_60)
    output["breakout_60"] = _safe_divide(adjusted_close, previous_high_60) - 1.0
    output["momentum_slowdown"] = ret_20 - comparable_ret_20
    output["upper_shadow_mean_20"] = upper_shadow.rolling(20, min_periods=15).mean()
    output["average_turnover_cny_20"] = amount_mean_20
    output["turnover_rate_pct"] = turnover
    output["float_market_cap_cny"] = pd.to_numeric(
        work["float_market_cap_cny"], errors="coerce"
    )
    output["recent_observations_20"] = _calendar_observation_count(trade_indices, 20)
    output["history_observations_250"] = _calendar_observation_count(trade_indices, 250)
    output["one_word_days_20"] = _calendar_flag_count(
        trade_indices, np.asarray(one_word, dtype=bool), 20
    )
    return output.replace([np.inf, -np.inf], np.nan)


def build_recent_features(
    panel: pd.DataFrame, recent_dates: set[pd.Timestamp]
) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    total = panel["instrument_id"].nunique()
    for number, (_, bars) in enumerate(panel.groupby("instrument_id", sort=False), start=1):
        calculated = compute_symbol_features(bars)
        selected = calculated[calculated["trade_date"].isin(recent_dates)]
        if not selected.empty:
            parts.append(selected)
        if number % 500 == 0 or number == total:
            print(f"Computed phase features: {number}/{total} instruments", flush=True)
    if not parts:
        raise RuntimeError("No recent feature rows were produced")
    return pd.concat(parts, ignore_index=True)


def _attach_disclosures(recent: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    left = recent.sort_values(["trade_date", "vendor_symbol"], kind="stable").copy()
    left["vendor_symbol"] = left["vendor_symbol"].astype("object")
    left["trade_date"] = pd.to_datetime(left["trade_date"]).astype("datetime64[ns]")
    holder = events[events["new_holder_disclosure"].fillna(False)].copy()
    holder_columns = [
        "vendor_symbol",
        "ann_date",
        "holder_report_end",
        "holder_num",
        "holder_num_change_rate",
    ]
    holder = holder[holder_columns].dropna(subset=["ann_date"]).rename(
        columns={"ann_date": "holder_announcement_date"}
    )
    holder["vendor_symbol"] = holder["vendor_symbol"].astype("object")
    holder["holder_announcement_date"] = pd.to_datetime(
        holder["holder_announcement_date"]
    ).astype("datetime64[ns]")
    holder = holder.sort_values(
        ["holder_announcement_date", "vendor_symbol"], kind="stable"
    ).drop_duplicates(["vendor_symbol", "holder_announcement_date"], keep="last")
    # Count only disclosures already visible at each matched announcement.
    holder["holder_update_count"] = holder.groupby("vendor_symbol", sort=False).cumcount() + 1
    left = pd.merge_asof(
        left,
        holder,
        left_on="trade_date",
        right_on="holder_announcement_date",
        by="vendor_symbol",
        direction="backward",
        allow_exact_matches=True,
    )

    composition = events[events["new_composition_disclosure"].fillna(False)].copy()
    composition_columns = [
        "vendor_symbol",
        "ann_date",
        "composition_report_end",
        "top10_float_pct",
        "top10_hhi",
        "top10_float_change_pp",
        "composition_quality_valid",
        "classification_coverage_pct",
        "identified_institution_pct",
    ]
    composition = composition[composition_columns].dropna(subset=["ann_date"]).rename(
        columns={"ann_date": "composition_announcement_date"}
    )
    composition["vendor_symbol"] = composition["vendor_symbol"].astype("object")
    composition["composition_announcement_date"] = pd.to_datetime(
        composition["composition_announcement_date"]
    ).astype("datetime64[ns]")
    composition = composition.sort_values(
        ["composition_announcement_date", "vendor_symbol"], kind="stable"
    ).drop_duplicates(["vendor_symbol", "composition_announcement_date"], keep="last")
    composition["composition_update_count"] = composition.groupby("vendor_symbol", sort=False).cumcount() + 1
    left = pd.merge_asof(
        left.sort_values(["trade_date", "vendor_symbol"], kind="stable"),
        composition,
        left_on="trade_date",
        right_on="composition_announcement_date",
        by="vendor_symbol",
        direction="backward",
        allow_exact_matches=True,
    )
    for column in ("holder_update_count", "composition_update_count"):
        left[column] = left[column].fillna(0).astype("int16")
    left["holder_disclosure_age_days"] = (
        left["trade_date"] - pd.to_datetime(left["holder_announcement_date"])
    ).dt.days
    left["composition_disclosure_age_days"] = (
        left["trade_date"] - pd.to_datetime(left["composition_announcement_date"])
    ).dt.days
    left["holder_report_age_days"] = (
        left["trade_date"] - pd.to_datetime(left["holder_report_end"])
    ).dt.days
    left["composition_report_age_days"] = (
        left["trade_date"] - pd.to_datetime(left["composition_report_end"])
    ).dt.days
    return left.sort_values(["vendor_symbol", "trade_date"], kind="stable").reset_index(
        drop=True
    )


def _rank(frame: pd.DataFrame, values: pd.Series) -> pd.Series:
    return values.groupby(frame["trade_date"], sort=False).rank(pct=True, method="average")


def _decay_rank(rank: pd.Series, age_days: pd.Series) -> pd.Series:
    age = pd.to_numeric(age_days, errors="coerce")
    decay = np.exp(-age.clip(lower=0) / 120.0)
    result = 0.5 + (rank - 0.5) * decay
    return result.where(age.between(0, MAX_DISCLOSURE_AGE_DAYS))


def weighted_state_score(
    frame: pd.DataFrame, components: tuple[tuple[str, float], ...]
) -> tuple[pd.Series, pd.Series, pd.Series]:
    columns = [name for name, _ in components]
    weights = pd.Series({name: weight for name, weight in components}, dtype=float)
    values = frame[columns].astype(float)
    available = values.notna().mul(weights, axis=1)
    available_weight = available.sum(axis=1)
    weighted_sum = values.fillna(0.0).mul(weights, axis=1).sum(axis=1)
    score = 100.0 * weighted_sum / available_weight.where(available_weight > 0)
    coverage = available_weight / float(weights.sum())
    supporting_weight = values.ge(0.55).mul(weights, axis=1).sum(axis=1)
    agreement = supporting_weight / available_weight.where(available_weight > 0)
    return score, coverage, agreement


def add_phase_scores(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    holder_concentration = _rank(
        result, -pd.to_numeric(result["holder_num_change_rate"], errors="coerce").clip(-0.8, 5.0)
    )
    holder_increase = _rank(
        result, pd.to_numeric(result["holder_num_change_rate"], errors="coerce").clip(-0.8, 5.0)
    )
    top10_rank = _rank(result, pd.to_numeric(result["top10_float_pct"], errors="coerce"))
    hhi_rank = _rank(result, pd.to_numeric(result["top10_hhi"], errors="coerce"))
    top10_change_rank = _rank(
        result, -pd.to_numeric(result["top10_float_change_pp"], errors="coerce")
    )
    result["r_holder_concentration"] = _decay_rank(
        holder_concentration, result["holder_disclosure_age_days"]
    )
    result["r_holder_increase"] = _decay_rank(
        holder_increase, result["holder_disclosure_age_days"]
    )
    result["r_top10_concentration"] = _decay_rank(
        pd.concat([top10_rank, hhi_rank], axis=1).mean(axis=1),
        result["composition_disclosure_age_days"],
    )
    result["r_top10_deconcentration"] = _decay_rank(
        top10_change_rank, result["composition_disclosure_age_days"]
    )

    position = pd.to_numeric(result["price_position_250"], errors="coerce")
    result["position_fit_accumulation"] = (
        1.0 - (position - 0.40).abs() / 0.45
    ).clip(0.0, 1.0)
    result["position_fit_lock"] = (1.0 - (position - 0.70).abs() / 0.45).clip(0.0, 1.0)
    ret_60 = pd.to_numeric(result["ret_60_behavior"], errors="coerce")
    result["mild_trend_fit"] = (1.0 - (ret_60 - 0.08).abs() / 0.30).clip(0.0, 1.0)
    turnover_ratio = pd.to_numeric(result["turnover_ratio_5_60"], errors="coerce")
    volume_ratio = pd.to_numeric(result["volume_ratio_5_60"], errors="coerce")
    result["turnover_activation_fit"] = (
        1.0 - np.abs(np.log(turnover_ratio.clip(0.05, 20.0) / 1.20)) / np.log(3.0)
    ).clip(0.0, 1.0)
    result["early_volume_fit"] = (
        1.0 - np.abs(np.log(volume_ratio.clip(0.05, 20.0) / 1.40)) / np.log(3.0)
    ).clip(0.0, 1.0)

    ranking_inputs = {
        "r_up_down_volume": np.log(
            pd.to_numeric(result["up_down_volume_ratio_20"], errors="coerce").clip(0.05, 20.0)
        ),
        "r_low_upper_shadow": -pd.to_numeric(result["upper_shadow_mean_20"], errors="coerce"),
        "r_trend_20": pd.to_numeric(result["ret_20_behavior"], errors="coerce"),
        "r_turnover_lock": -np.log(turnover_ratio.clip(0.05, 20.0)),
        "r_volatility_contraction": -np.log(
            pd.to_numeric(result["volatility_ratio_20_60"], errors="coerce").clip(0.05, 20.0)
        ),
        "r_price_impact": np.log1p(
            pd.to_numeric(result["amihud_20"], errors="coerce").clip(lower=0.0)
        ),
        "r_pullback_shrink": -np.log(
            pd.to_numeric(result["pullback_volume_ratio_20"], errors="coerce").clip(0.05, 20.0)
        ),
        "r_breakout": pd.to_numeric(result["breakout_60"], errors="coerce"),
        "r_trend_efficiency": pd.to_numeric(result["trend_efficiency_20"], errors="coerce"),
        "r_high_position": position,
        "r_turnover_spike": np.log(turnover_ratio.clip(0.05, 20.0)),
        "r_volume_spike": np.log(volume_ratio.clip(0.05, 20.0)),
        "r_upper_shadow": pd.to_numeric(result["upper_shadow_mean_20"], errors="coerce"),
        "r_momentum_slowdown": -pd.to_numeric(result["momentum_slowdown"], errors="coerce"),
        "r_pullback_volume": np.log(
            pd.to_numeric(result["pullback_volume_ratio_20"], errors="coerce").clip(0.05, 20.0)
        ),
    }
    for name, values in ranking_inputs.items():
        result[name] = _rank(result, values)

    for state, components in STATE_COMPONENTS.items():
        score, coverage, agreement = weighted_state_score(result, components)
        result[f"{state}_score"] = score
        result[f"{state}_coverage"] = coverage
        result[f"{state}_agreement"] = agreement

    score_columns = [f"{state}_score" for state in STATE_COMPONENTS]
    score_values = result[score_columns].to_numpy(dtype=float)
    safe = np.where(np.isfinite(score_values), score_values, -np.inf)
    best_index = np.argmax(safe, axis=1)
    states = np.asarray(list(STATE_COMPONENTS), dtype=object)[best_index]
    ordered = np.sort(safe, axis=1)
    finite_score_count = np.isfinite(score_values).sum(axis=1)
    result["raw_state"] = states
    result["state_score"] = np.where(finite_score_count >= 1, ordered[:, -1], np.nan)
    margin = np.full(len(result), np.nan, dtype=float)
    enough = finite_score_count >= 2
    margin[enough] = ordered[enough, -1] - ordered[enough, -2]
    result["state_margin"] = margin
    for state in STATE_COMPONENTS:
        mask = result["raw_state"].eq(state)
        result.loc[mask, "score_coverage"] = result.loc[mask, f"{state}_coverage"]
        result.loc[mask, "signal_agreement"] = result.loc[mask, f"{state}_agreement"]
    result["score_coverage"] = pd.to_numeric(result["score_coverage"], errors="coerce")
    result["signal_agreement"] = pd.to_numeric(result["signal_agreement"], errors="coerce")
    return result


def _age_score(age: pd.Series, full_age: int, zero_age: int) -> pd.Series:
    values = pd.to_numeric(age, errors="coerce")
    score = 100.0 * (zero_age - values) / (zero_age - full_age)
    return score.clip(0.0, 100.0).where(values.notna(), 0.0)


def add_current_pool_fields(
    recent: pd.DataFrame,
    instruments: pd.DataFrame,
    calendar: pd.DataFrame,
    market_root: Path,
    current_date: pd.Timestamp,
) -> pd.DataFrame:
    result = recent.copy()
    current_raw = result[result["trade_date"].eq(current_date)][
        ["instrument_id", "raw_state"]
    ].rename(columns={"raw_state": "current_raw_state"})
    stability = result.merge(current_raw, on="instrument_id", how="left")
    stability["matches_current"] = stability["raw_state"].eq(stability["current_raw_state"])
    stability_count = stability.groupby("instrument_id")["matches_current"].sum().rename(
        "stable_days_5"
    )
    current = result[result["trade_date"].eq(current_date)].copy()
    current["current_bar_available"] = True
    current = current.merge(stability_count, on="instrument_id", how="left")

    meta_columns = [
        "instrument_id",
        "vendor_symbol",
        "name_zh",
        "industry_vendor",
        "board",
        "exchange",
        "list_date",
        "delist_date",
    ]
    available_meta = [column for column in meta_columns if column in instruments.columns]
    meta = instruments[available_meta].drop_duplicates("instrument_id")
    if "vendor_symbol" in meta.columns:
        meta = meta.drop(columns="vendor_symbol")
    current = current.merge(meta, on="instrument_id", how="left", validate="one_to_one")

    calendar_values = calendar["calendar_date"].to_numpy(dtype="datetime64[ns]")
    list_dates = pd.to_datetime(current["list_date"], errors="coerce").to_numpy(
        dtype="datetime64[ns]"
    )
    current_idx = int(np.searchsorted(calendar_values, np.datetime64(current_date), side="right") - 1)
    list_idx = np.searchsorted(calendar_values, list_dates, side="left")
    current["listing_sessions"] = current_idx - list_idx + 1
    current.loc[pd.isna(current["list_date"]), "listing_sessions"] = 0

    history = pd.read_parquet(market_root / "name_history.parquet")
    name_states = historical_name_states(current, history)
    current["historical_state_known"] = name_states["historical_state_known"]
    current["historical_is_st"] = name_states["historical_is_st"]

    disclosure_age = pd.concat(
        [
            _age_score(current["holder_disclosure_age_days"], 120, 180),
            _age_score(current["composition_disclosure_age_days"], 120, 180),
            _age_score(current["holder_report_age_days"], 150, 240),
            _age_score(current["composition_report_age_days"], 150, 240),
        ],
        axis=1,
    ).mean(axis=1)
    liquidity_log = np.log(
        current["average_turnover_cny_20"].clip(lower=MIN_MONITOR_TURNOVER_CNY)
        / MIN_MONITOR_TURNOVER_CNY
    ) / np.log(MIN_STRATEGY_TURNOVER_CNY / MIN_MONITOR_TURNOVER_CNY)
    liquidity_score = (100.0 * liquidity_log).clip(0.0, 100.0)
    recent_score = (100.0 * current["recent_observations_20"] / 20.0).clip(0.0, 100.0)
    history_score = (100.0 * current["history_observations_250"] / 250.0).clip(0.0, 100.0)
    one_word_score = (100.0 - 15.0 * current["one_word_days_20"]).clip(0.0, 100.0)
    tradeability = pd.concat(
        [liquidity_score, recent_score, history_score, one_word_score], axis=1
    ).mean(axis=1)
    separability = (current["state_margin"] / 20.0 * 100.0).clip(0.0, 100.0)
    stability_score = (current["stable_days_5"] / 5.0 * 100.0).clip(0.0, 100.0)
    sample_fit = pd.Series(100.0, index=current.index)
    extreme_count = (
        ~current["turnover_ratio_5_60"].between(0.05, 20.0)
    ).astype(int) + (~current["volume_ratio_5_60"].between(0.05, 20.0)).astype(int) + (
        ~current["volatility_ratio_20_60"].between(0.05, 5.0)
    ).astype(int)
    sample_fit = (sample_fit - 25.0 * extreme_count).clip(0.0, 100.0)

    current["data_completeness_score"] = 100.0 * current["score_coverage"]
    current["disclosure_freshness_score"] = disclosure_age
    current["tradeability_score"] = tradeability
    current["signal_agreement_score"] = 100.0 * current["signal_agreement"]
    current["state_separability_score"] = separability
    current["temporal_stability_score"] = stability_score
    current["sample_applicability_score"] = sample_fit
    raw_confidence = (
        0.20 * current["data_completeness_score"]
        + 0.15 * current["disclosure_freshness_score"]
        + 0.15 * current["tradeability_score"]
        + 0.20 * current["signal_agreement_score"]
        + 0.15 * current["state_separability_score"]
        + 0.10 * current["temporal_stability_score"]
        + 0.05 * current["sample_applicability_score"]
    )
    cap = current["raw_state"].map(STATE_CONFIDENCE_CAPS).fillna(49.0)
    current["confidence_before_source_cap"] = raw_confidence
    current["confidence"] = np.minimum(raw_confidence, cap)

    base_market_eligible = (
        current["historical_state_known"]
        & ~current["historical_is_st"]
        & current["listing_sessions"].ge(MIN_LISTING_SESSIONS)
        & current["history_observations_250"].ge(MIN_HISTORY_OBSERVATIONS_250)
        & current["recent_observations_20"].ge(MIN_RECENT_OBSERVATIONS_20)
        & current["average_turnover_cny_20"].ge(MIN_MONITOR_TURNOVER_CNY)
        & current["one_word_days_20"].le(MAX_ONE_WORD_DAYS_20)
    )
    ownership_eligible = (
        current["holder_disclosure_age_days"].between(0, MAX_DISCLOSURE_AGE_DAYS)
        & current["composition_disclosure_age_days"].between(0, MAX_DISCLOSURE_AGE_DAYS)
        & current["holder_update_count"].ge(2)
        & current["composition_update_count"].ge(2)
        & current["composition_quality_valid"].fillna(False)
    )
    state_eligible = (
        current["state_score"].ge(MIN_STATE_SCORE)
        & current["state_margin"].ge(MIN_STATE_MARGIN)
        & current["score_coverage"].ge(MIN_SCORE_COVERAGE)
        & current["stable_days_5"].ge(MIN_STABLE_DAYS_5)
    )
    current["base_market_eligible"] = base_market_eligible
    current["ownership_eligible"] = ownership_eligible
    current["state_eligible"] = state_eligible
    defined = base_market_eligible & ownership_eligible & state_eligible
    current["defined_state"] = current["raw_state"].where(defined, "uncertain")
    current["state_cn"] = current["defined_state"].map(STATE_NAMES_CN)
    current["confidence_before_eligibility"] = current["confidence"]
    current["confidence"] = current["confidence"].where(
        defined, np.minimum(current["confidence"], STATE_CONFIDENCE_CAPS["uncertain"])
    )

    strategy = (
        defined
        & current["defined_state"].isin(["lock", "markup"])
        & current["confidence"].ge(MIN_STRATEGY_CONFIDENCE)
        & current["average_turnover_cny_20"].ge(MIN_STRATEGY_TURNOVER_CNY)
    )
    monitor = defined & current["confidence"].ge(MIN_MONITOR_CONFIDENCE) & ~strategy
    current["pool_status"] = np.select(
        [strategy, monitor], ["A_STRATEGY_CANDIDATE", "B_MONITOR_ONLY"], default="REMOVED"
    )

    reasons: list[str] = []
    for row in current.itertuples(index=False):
        items: list[str] = []
        if not row.historical_state_known:
            items.append("ST状态未知")
        elif row.historical_is_st:
            items.append("ST或风险警示")
        if row.listing_sessions < MIN_LISTING_SESSIONS:
            items.append("上市不足250交易日")
        if row.history_observations_250 < MIN_HISTORY_OBSERVATIONS_250:
            items.append("近250日行情覆盖不足95%")
        if row.recent_observations_20 < MIN_RECENT_OBSERVATIONS_20:
            items.append("近20日有效成交不足18日")
        if not np.isfinite(row.average_turnover_cny_20) or row.average_turnover_cny_20 < MIN_MONITOR_TURNOVER_CNY:
            items.append("近20日日均成交额低于3000万元")
        if row.one_word_days_20 > MAX_ONE_WORD_DAYS_20:
            items.append("近20日一字涨跌停超过3日")
        if not row.ownership_eligible:
            items.append("股东或持仓披露缺失/过期/质量不足")
        if row.state_score < MIN_STATE_SCORE:
            items.append("最高状态分低于65")
        if row.state_margin < MIN_STATE_MARGIN:
            items.append("第一与第二状态分差低于10")
        if row.score_coverage < MIN_SCORE_COVERAGE:
            items.append("状态指标覆盖低于80%")
        if row.stable_days_5 < MIN_STABLE_DAYS_5:
            items.append("最近5日同状态不足3日")
        if row.confidence < MIN_MONITOR_CONFIDENCE:
            items.append("可信度低于65")
        if row.pool_status == "B_MONITOR_ONLY" and row.defined_state in {"accumulation", "distribution"}:
            items.append("缺逐日大/小单资金流，资金型阶段暂不升A级")
        if row.pool_status == "B_MONITOR_ONLY" and row.average_turnover_cny_20 < MIN_STRATEGY_TURNOVER_CNY:
            items.append("成交额未达到A级1亿元门槛")
        reasons.append("；".join(dict.fromkeys(items)))
    current["reasons"] = reasons
    return current.sort_values(
        ["pool_status", "confidence", "state_score", "vendor_symbol"],
        ascending=[True, False, False, True],
        kind="stable",
    ).reset_index(drop=True)


def append_missing_listed_instruments(
    snapshot: pd.DataFrame, instruments: pd.DataFrame, current_date: pd.Timestamp
) -> pd.DataFrame:
    if "listing_status" in instruments.columns:
        universe = instruments[instruments["listing_status"].eq("LISTED")].copy()
    else:
        list_date = pd.to_datetime(instruments["list_date"], errors="coerce")
        delist_date = pd.to_datetime(instruments["delist_date"], errors="coerce")
        universe = instruments[
            list_date.le(current_date) & (delist_date.isna() | delist_date.gt(current_date))
        ].copy()
    missing = universe[~universe["instrument_id"].isin(snapshot["instrument_id"])].copy()
    if missing.empty:
        return snapshot
    rows = pd.DataFrame(index=np.arange(len(missing)), columns=snapshot.columns)
    source = missing.reset_index(drop=True)
    for column in (
        "instrument_id",
        "vendor_symbol",
        "name_zh",
        "industry_vendor",
        "board",
        "exchange",
        "list_date",
        "delist_date",
    ):
        if column in rows.columns and column in source.columns:
            rows[column] = source[column].to_numpy()
    rows["trade_date"] = current_date
    rows["current_bar_available"] = False
    rows["raw_state"] = "uncertain"
    rows["defined_state"] = "uncertain"
    rows["state_cn"] = STATE_NAMES_CN["uncertain"]
    rows["confidence"] = 0.0
    rows["confidence_before_eligibility"] = 0.0
    rows["base_market_eligible"] = False
    rows["ownership_eligible"] = False
    rows["state_eligible"] = False
    rows["pool_status"] = "REMOVED"
    rows["reasons"] = "当前交易日无有效行情（停牌或数据缺失）"
    combined = pd.concat([snapshot, rows], ignore_index=True)
    return combined.sort_values(
        ["pool_status", "confidence", "state_score", "vendor_symbol"],
        ascending=[True, False, False, True],
        kind="stable",
    ).reset_index(drop=True)


def build_summary(snapshot: pd.DataFrame, current_date: pd.Timestamp) -> dict[str, Any]:
    status_counts = snapshot["pool_status"].value_counts().to_dict()
    state_counts = (
        snapshot.groupby(["pool_status", "state_cn"], observed=True).size().rename("stocks").reset_index()
    )
    return {
        "as_of_date": str(current_date.date()),
        "listed_universe_rows": int(len(snapshot)),
        "current_valid_bar_rows": int(snapshot["current_bar_available"].fillna(False).sum()),
        "current_state_feature_rows": int(snapshot["state_score"].notna().sum()),
        "pool_counts": {str(key): int(value) for key, value in status_counts.items()},
        "a_pool_share": float(snapshot["pool_status"].eq("A_STRATEGY_CANDIDATE").mean()),
        "b_pool_share": float(snapshot["pool_status"].eq("B_MONITOR_ONLY").mean()),
        "state_counts": state_counts.to_dict("records"),
        "a_pool_median_confidence": float(
            snapshot.loc[snapshot["pool_status"].eq("A_STRATEGY_CANDIDATE"), "confidence"].median()
        ),
        "rules": {
            "minimum_listing_sessions": MIN_LISTING_SESSIONS,
            "minimum_history_observations_250": MIN_HISTORY_OBSERVATIONS_250,
            "minimum_recent_observations_20": MIN_RECENT_OBSERVATIONS_20,
            "minimum_monitor_turnover_cny": MIN_MONITOR_TURNOVER_CNY,
            "minimum_strategy_turnover_cny": MIN_STRATEGY_TURNOVER_CNY,
            "maximum_one_word_days_20": MAX_ONE_WORD_DAYS_20,
            "maximum_disclosure_age_days": MAX_DISCLOSURE_AGE_DAYS,
            "minimum_state_score": MIN_STATE_SCORE,
            "minimum_state_margin": MIN_STATE_MARGIN,
            "minimum_score_coverage": MIN_SCORE_COVERAGE,
            "minimum_stable_days_5": MIN_STABLE_DAYS_5,
            "minimum_monitor_confidence": MIN_MONITOR_CONFIDENCE,
            "minimum_strategy_confidence": MIN_STRATEGY_CONFIDENCE,
            "confidence_caps": STATE_CONFIDENCE_CAPS,
        },
        "limitations": [
            "This is a phase-behaviour proxy, not proof of account control.",
            "Daily large/small-order money-flow data is absent from down_data.",
            "Accumulation and distribution confidence is capped at 79 until independent flow evidence is added.",
            "A-pool membership is not an entry signal; state-transition return validation is still required.",
            "2025-2026 is historical diagnostic data already inspected by the project.",
        ],
    }


def render_report(summary: dict[str, Any], state_summary: pd.DataFrame, top_a: pd.DataFrame) -> str:
    counts = summary["pool_counts"]
    return "\n".join(
        [
            "# 单支股票阶段定义池审计",
            "",
            f"数据截止：{summary['as_of_date']}。",
            "",
            "## 当前结论",
            "",
            f"当前在市主板股票 {summary['listed_universe_rows']:,} 只，其中"
            f" {summary['current_valid_bar_rows']:,} 只有当日有效行情；"
            f" {summary['current_state_feature_rows']:,} 只具备成熟状态特征。"
            f"A级策略候选 {counts.get('A_STRATEGY_CANDIDATE', 0):,} 只，"
            f"B级观察 {counts.get('B_MONITOR_ONLY', 0):,} 只，"
            f"删除 {counts.get('REMOVED', 0):,} 只。",
            "",
            "A级只表示数据、区分度、稳定性和可交易性达到定义标准，并不等于买入。"
            "只有状态转换经过点时可执行回测后，才能授权入场规则。",
            "",
            "## 状态分布",
            "",
            state_summary.to_markdown(index=False, floatfmt=".2f"),
            "",
            "## A级候选（按可信度）",
            "",
            top_a.to_markdown(index=False, floatfmt=".2f") if len(top_a) else "当前没有A级候选。",
            "",
            "## 关键口径",
            "",
            "- 先检查上市时长、行情连续性、成交额、ST、一字板与披露时效，再计算状态。",
            "- 状态成立需最高分不低于65、领先第二名至少10分、指标覆盖不低于80%，且最近5日至少3日一致。",
            "- A级还需可信度不低于80、近20日日均成交额不低于1亿元，且状态只能是锁仓或拉升。",
            "- 吸筹和派发因缺少逐日大/小单资金流，可信度封顶79，只能进入B级观察池。",
            "- 所有股东信息按公告日生效，不按报告期末回填。",
            "",
            "## 限制",
            "",
            "本结果是公开持仓与交易行为状态，不是真实控盘率，也不能识别同一控制人的账户组。"
            "2025—2026数据已被前序研究查看，后续收益结果只能称历史诊断；真正前向验证从2026-08-11之后开始。",
            "",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/ownership_state.json")
    parser.add_argument("--history-start", default=str(HISTORY_START.date()))
    parser.add_argument("--as-of", default=None)
    parser.add_argument("--output-dir", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config((ROOT / args.config).resolve())
    market_root = resolve(config["market_data_root"])
    configured_end = pd.Timestamp(str(config["research_end"]))
    current_date = pd.Timestamp(args.as_of) if args.as_of else configured_end
    history_start = pd.Timestamp(args.history_start)
    output_dir = (
        Path(args.output_dir).resolve()
        if args.output_dir
        else resolve(config["output_dir"]) / "state_definition_pool"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    cache_path = output_dir / "recent_feature_cache.parquet"
    cache_key = f"{history_start.date()}_{current_date.date()}"
    cache_meta_path = output_dir / "recent_feature_cache.json"
    cache_valid = False
    if cache_path.exists() and cache_meta_path.exists():
        try:
            cache_meta = json.loads(cache_meta_path.read_text(encoding="utf-8"))
            cache_valid = cache_meta.get("cache_key") == cache_key
        except (OSError, json.JSONDecodeError):
            cache_valid = False
    if cache_valid:
        recent = pd.read_parquet(cache_path)
        instruments = pd.read_parquet(market_root / "instruments.parquet")
        calendar = pd.read_parquet(market_root / "trading_calendar.parquet")
        calendar["calendar_date"] = pd.to_datetime(calendar["calendar_date"])
        calendar = calendar[
            calendar["is_open"].fillna(False)
            & calendar["calendar_date"].between(history_start, current_date)
        ].sort_values("calendar_date").drop_duplicates("calendar_date").reset_index(drop=True)
        print("Loaded recent feature cache", flush=True)
    else:
        print("Loading market history", flush=True)
        panel, instruments, calendar = load_market_history(
            market_root, history_start, current_date
        )
        available_end = panel["trade_date"].max()
        if available_end < current_date:
            current_date = pd.Timestamp(available_end)
        open_dates = calendar.loc[calendar["calendar_date"] <= current_date, "calendar_date"]
        recent_dates = set(open_dates.tail(5))
        recent = build_recent_features(panel, recent_dates)
        recent.to_parquet(cache_path, index=False)
        cache_meta_path.write_text(
            json.dumps({"cache_key": cache_key}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        del panel

    events = pd.read_parquet(resolve(config["processed_data_dir"]) / "ownership_events.parquet")
    events["ann_date"] = pd.to_datetime(events["ann_date"])
    events = events[events["ann_date"] <= current_date].copy()
    recent = _attach_disclosures(recent, events)
    recent = add_phase_scores(recent)
    snapshot = add_current_pool_fields(
        recent, instruments, calendar, market_root, current_date
    )
    snapshot = append_missing_listed_instruments(snapshot, instruments, current_date)

    state_summary = snapshot.groupby(
        ["pool_status", "state_cn"], observed=True
    ).agg(
        stocks=("instrument_id", "size"),
        median_confidence=("confidence", "median"),
        median_state_score=("state_score", "median"),
        median_margin=("state_margin", "median"),
    ).reset_index()
    summary = build_summary(snapshot, current_date)
    reason_summary = (
        snapshot.loc[snapshot["pool_status"].eq("REMOVED"), ["vendor_symbol", "reasons"]]
        .assign(reason=lambda values: values["reasons"].fillna("").str.split("；"))
        .explode("reason")
    )
    reason_summary = (
        reason_summary[reason_summary["reason"].ne("")]
        .groupby("reason", observed=True)
        .agg(stocks=("vendor_symbol", "nunique"))
        .sort_values("stocks", ascending=False)
        .reset_index()
    )
    top_columns = [
        "vendor_symbol",
        "name_zh",
        "industry_vendor",
        "state_cn",
        "confidence",
        "state_score",
        "state_margin",
        "stable_days_5",
        "average_turnover_cny_20",
        "holder_disclosure_age_days",
        "composition_disclosure_age_days",
    ]
    top_a = snapshot.loc[
        snapshot["pool_status"].eq("A_STRATEGY_CANDIDATE"), top_columns
    ].head(30).copy()
    if len(top_a):
        top_a["average_turnover_cny_20"] /= 100_000_000.0
        top_a = top_a.rename(columns={"average_turnover_cny_20": "日均成交额_亿元"})

    snapshot.to_parquet(output_dir / "current_snapshot.parquet", index=False)
    snapshot.to_csv(output_dir / "current_snapshot.csv", index=False, encoding="utf-8-sig")
    snapshot[snapshot["pool_status"].eq("A_STRATEGY_CANDIDATE")].to_csv(
        output_dir / "a_strategy_pool.csv", index=False, encoding="utf-8-sig"
    )
    snapshot[snapshot["pool_status"].eq("B_MONITOR_ONLY")].to_csv(
        output_dir / "b_monitor_pool.csv", index=False, encoding="utf-8-sig"
    )
    snapshot[snapshot["pool_status"].eq("REMOVED")].to_csv(
        output_dir / "removed_pool.csv", index=False, encoding="utf-8-sig"
    )
    state_summary.to_csv(output_dir / "state_summary.csv", index=False, encoding="utf-8-sig")
    reason_summary.to_csv(
        output_dir / "removal_reason_summary.csv", index=False, encoding="utf-8-sig"
    )
    (output_dir / "summary.json").write_text(
        json.dumps(_json_value(summary), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "report.md").write_text(
        render_report(summary, state_summary, top_a), encoding="utf-8"
    )
    print(json.dumps(_json_value(summary["pool_counts"]), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
