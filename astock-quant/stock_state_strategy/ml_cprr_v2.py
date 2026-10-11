from __future__ import annotations

import argparse
import gc
import json
import math
import time
from pathlib import Path
from typing import Any

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view
from sklearn.isotonic import IsotonicRegression

from backtest_mfe_mae import (
    build_outcomes,
    build_stock_views,
    load_config,
    read_year_partitions,
    resolve_from,
    simulate_portfolio,
)


FORWARD_WINDOW = 20
COST_BUFFER = 0.0035
CPRR_CAP = 10.0
ENTRY_CPRR_THRESHOLD = 1.50
ENTRY_TOP_N = 1
RANDOM_RUNS_DEFAULT = 50
SEEDS = [20260821, 20260822, 20260823, 20260824, 20260825]

FEATURES = [
    "ret_5",
    "ret_20",
    "ret_60",
    "close_ma20_ratio",
    "close_ma60_ratio",
    "ma20_ma60_ratio",
    "dist_high20",
    "dist_high60",
    "atr14_pct",
    "vol20",
    "vol_ratio_5_20",
    "volume_ratio_20",
    "amount_ratio_20",
    "turnover_ratio_20",
    "near_high20_flag",
    "rs20_rank",
    "rs60_rank",
    "market_ret20_median",
    "market_breadth20",
]


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


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


def load_daily_panel(
    data_root: Path,
    start_year: int,
    end_year: int,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    log("Loading instruments and trading calendar")
    instruments = pd.read_parquet(data_root / "instruments.parquet")
    calendar = pd.read_parquet(data_root / "trading_calendar.parquet")
    calendar["calendar_date"] = pd.to_datetime(calendar["calendar_date"])
    calendar = (
        calendar.loc[calendar["is_open"]]
        .sort_values("calendar_date")
        .drop_duplicates("calendar_date")
        .reset_index(drop=True)
    )
    calendar["trade_idx"] = np.arange(len(calendar), dtype="int32")

    log("Loading full daily price, factor and metric partitions")
    prices = read_year_partitions(
        data_root,
        "daily_prices",
        start_year,
        end_year,
        [
            "instrument_id",
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "volume_shares",
            "turnover_cny",
            "is_quality_valid",
        ],
    )
    factors = read_year_partitions(
        data_root,
        "adjustment_factors",
        start_year,
        end_year,
        ["instrument_id", "trade_date", "adjustment_factor"],
    )
    metrics = read_year_partitions(
        data_root,
        "daily_metrics",
        start_year,
        end_year,
        [
            "instrument_id",
            "trade_date",
            "turnover_rate_pct",
            "limit_status",
        ],
    )
    for frame in (prices, factors, metrics):
        frame["trade_date"] = pd.to_datetime(frame["trade_date"])

    quality_rows = int(prices["is_quality_valid"].fillna(False).sum())
    prices = prices.loc[prices["is_quality_valid"].fillna(False)].drop(
        columns="is_quality_valid"
    )
    panel = prices.merge(
        factors,
        on=["instrument_id", "trade_date"],
        how="inner",
        validate="one_to_one",
    ).merge(
        metrics,
        on=["instrument_id", "trade_date"],
        how="inner",
        validate="one_to_one",
    )
    panel = panel.loc[
        panel["adjustment_factor"].notna()
        & (panel["adjustment_factor"] > 0)
    ].copy()
    panel["limit_status"] = panel["limit_status"].fillna(-1).astype("int8")

    date_to_idx = calendar.set_index("calendar_date")["trade_idx"]
    panel["trade_idx"] = panel["trade_date"].map(date_to_idx)
    panel = panel.loc[panel["trade_idx"].notna()].copy()
    panel["trade_idx"] = panel["trade_idx"].astype("int32")

    for field in ("open", "high", "low", "close"):
        panel[f"adj_{field}"] = panel[field] * panel["adjustment_factor"]

    instruments = instruments.copy()
    instruments["list_date"] = pd.to_datetime(instruments["list_date"])
    instruments["delist_date"] = pd.to_datetime(instruments["delist_date"])
    instruments["current_is_st"] = instruments["name_zh"].fillna("").str.contains(
        "ST", case=False, na=False
    )
    if "board" in instruments:
        instruments = instruments.loc[instruments["board"] == "MAIN_BOARD"]
    panel = panel.merge(
        instruments[
            [
                "instrument_id",
                "list_date",
                "delist_date",
                "current_is_st",
            ]
        ],
        on="instrument_id",
        how="inner",
        validate="many_to_one",
    )
    panel = panel.loc[
        (~panel["current_is_st"])
        & (panel["trade_date"] >= panel["list_date"])
        & (
            panel["delist_date"].isna()
            | (panel["trade_date"] <= panel["delist_date"])
        )
    ].copy()
    panel = panel.sort_values(
        ["instrument_id", "trade_idx"]
    ).reset_index(drop=True)
    panel["listing_sessions"] = (
        panel.groupby("instrument_id", sort=False).cumcount() + 1
    ).astype("int32")
    panel["avg_turnover_20"] = panel.groupby(
        "instrument_id", sort=False
    )["turnover_cny"].transform(
        lambda values: values.rolling(20, min_periods=20).mean()
    )

    audit = {
        "quality_price_rows": quality_rows,
        "merged_panel_rows": int(len(panel)),
        "instruments": int(panel["instrument_id"].nunique()),
        "duplicate_keys": int(
            panel.duplicated(["instrument_id", "trade_date"]).sum()
        ),
        "date_min": str(panel["trade_date"].min().date()),
        "date_max": str(panel["trade_date"].max().date()),
        "current_st_filter_is_historical_approximation": True,
    }
    if audit["duplicate_keys"]:
        raise ValueError("Duplicate instrument/date keys in V2 panel")
    return panel, calendar, audit


def add_daily_features(panel: pd.DataFrame) -> pd.DataFrame:
    log("Calculating 19 point-in-time daily features")
    panel = panel.sort_values(["instrument_id", "trade_idx"]).copy()
    group = panel.groupby("instrument_id", sort=False, group_keys=False)
    close = panel["adj_close"]

    panel["ret_5"] = close / group["adj_close"].shift(5) - 1.0
    panel["ret_20"] = close / group["adj_close"].shift(20) - 1.0
    panel["ret_60"] = close / group["adj_close"].shift(60) - 1.0
    ma20 = group["adj_close"].transform(
        lambda values: values.rolling(20, min_periods=20).mean()
    )
    ma60 = group["adj_close"].transform(
        lambda values: values.rolling(60, min_periods=60).mean()
    )
    panel["close_ma20_ratio"] = close / ma20 - 1.0
    panel["close_ma60_ratio"] = close / ma60 - 1.0
    panel["ma20_ma60_ratio"] = ma20 / ma60 - 1.0
    high20 = group["adj_high"].transform(
        lambda values: values.rolling(20, min_periods=20).max()
    )
    high60 = group["adj_high"].transform(
        lambda values: values.rolling(60, min_periods=60).max()
    )
    panel["dist_high20"] = close / high20 - 1.0
    panel["dist_high60"] = close / high60 - 1.0
    panel["near_high20_flag"] = (close >= 0.97 * high20).astype("int8")

    previous_close = group["adj_close"].shift(1)
    true_range = np.maximum(
        panel["adj_high"] - panel["adj_low"],
        np.maximum(
            (panel["adj_high"] - previous_close).abs(),
            (panel["adj_low"] - previous_close).abs(),
        ),
    )
    panel["_true_range"] = true_range
    atr14 = panel.groupby("instrument_id", sort=False)["_true_range"].transform(
        lambda values: values.rolling(14, min_periods=14).mean()
    )
    panel["atr14_pct"] = atr14 / close
    panel["atr20_pct"] = panel.groupby(
        "instrument_id", sort=False
    )["_true_range"].transform(
        lambda values: values.rolling(20, min_periods=20).mean()
    ) / close
    panel = panel.drop(columns="_true_range")

    panel["_daily_return"] = group["adj_close"].pct_change()
    daily_group = panel.groupby("instrument_id", sort=False)["_daily_return"]
    vol5 = daily_group.transform(
        lambda values: values.rolling(5, min_periods=5).std()
    )
    vol20_daily = daily_group.transform(
        lambda values: values.rolling(20, min_periods=20).std()
    )
    panel["vol20"] = vol20_daily * np.sqrt(252.0)
    panel["vol_ratio_5_20"] = vol5 / vol20_daily
    panel = panel.drop(columns="_daily_return")

    volume_ma20 = group["volume_shares"].transform(
        lambda values: values.rolling(20, min_periods=20).mean()
    )
    amount_ma20 = group["turnover_cny"].transform(
        lambda values: values.rolling(20, min_periods=20).mean()
    )
    turnover_ma20 = group["turnover_rate_pct"].transform(
        lambda values: values.rolling(20, min_periods=20).mean()
    )
    panel["volume_ratio_20"] = panel["volume_shares"] / volume_ma20
    panel["amount_ratio_20"] = panel["turnover_cny"] / amount_ma20
    panel["turnover_ratio_20"] = panel["turnover_rate_pct"] / turnover_ma20

    by_date = panel.groupby("trade_date", sort=False)
    panel["rs20_rank"] = by_date["ret_20"].rank(pct=True)
    panel["rs60_rank"] = by_date["ret_60"].rank(pct=True)
    panel["market_ret20_median"] = by_date["ret_20"].transform("median")
    panel["_positive20"] = (panel["ret_20"] > 0).astype(float)
    panel["market_breadth20"] = panel.groupby("trade_date", sort=False)[
        "_positive20"
    ].transform("mean")
    panel = panel.drop(columns="_positive20")
    return panel


def add_executable_labels(panel: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    log("Calculating executable T+1 / 20-session CPRR labels on the full panel")
    row_count = len(panel)
    mfe = np.full(row_count, np.nan, dtype="float32")
    mae_pre = np.full(row_count, np.nan, dtype="float32")
    terminal_return = np.full(row_count, np.nan, dtype="float32")
    label_end_idx = np.full(row_count, -1, dtype="int32")
    peak_offset = np.full(row_count, -1, dtype="int8")
    entry_unbuyable = 0
    incomplete_path = 0

    groups = panel.groupby("instrument_id", sort=False).indices
    for number, positions in enumerate(groups.values(), start=1):
        pos = np.asarray(positions, dtype=np.int64)
        length = len(pos)
        if length <= FORWARD_WINDOW + 1:
            continue
        high = panel.loc[pos, "adj_high"].to_numpy(dtype=float)
        low = panel.loc[pos, "adj_low"].to_numpy(dtype=float)
        open_price = panel.loc[pos, "adj_open"].to_numpy(dtype=float)
        close = panel.loc[pos, "adj_close"].to_numpy(dtype=float)
        trade_idx = panel.loc[pos, "trade_idx"].to_numpy(dtype=np.int32)
        limit_status = panel.loc[pos, "limit_status"].to_numpy(dtype=np.int8)

        candidates = length - (FORWARD_WINDOW + 1)
        signal_positions = pos[:candidates]
        entry = open_price[1 : candidates + 1]
        high_windows = sliding_window_view(high[2:], FORWARD_WINDOW)
        low_windows = sliding_window_view(low[1:], FORWARD_WINDOW + 1)
        peak_positions = np.argmax(high_windows, axis=1)
        row_numbers = np.arange(candidates)
        peak_high = high_windows[row_numbers, peak_positions]
        cumulative_low = np.minimum.accumulate(low_windows, axis=1)
        pre_peak_low = cumulative_low[row_numbers, peak_positions + 1]
        end_close = close[FORWARD_WINDOW + 1 :]
        end_indices = trade_idx[FORWARD_WINDOW + 1 :]

        continuous = (
            end_indices - trade_idx[:candidates] == FORWARD_WINDOW + 1
        )
        finite = (
            np.isfinite(entry)
            & (entry > 0)
            & np.isfinite(peak_high)
            & np.isfinite(pre_peak_low)
            & np.isfinite(end_close)
        )
        buyable = limit_status[1 : candidates + 1] != 3
        valid = continuous & finite & buyable
        incomplete_path += int((~continuous | ~finite).sum())
        entry_unbuyable += int((continuous & finite & ~buyable).sum())

        valid_rows = signal_positions[valid]
        mfe[valid_rows] = np.maximum(
            peak_high[valid] / entry[valid] - 1.0, 0.0
        ).astype("float32")
        mae_pre[valid_rows] = np.maximum(
            1.0 - pre_peak_low[valid] / entry[valid], 0.0
        ).astype("float32")
        terminal_return[valid_rows] = (
            end_close[valid] / entry[valid] - 1.0 - COST_BUFFER
        ).astype("float32")
        label_end_idx[valid_rows] = end_indices[valid]
        peak_offset[valid_rows] = (peak_positions[valid] + 1).astype("int8")

        if number % 500 == 0 or number == len(groups):
            log(f"Label progress: {number:,}/{len(groups):,} instruments")

    panel["mfe20_exec"] = mfe
    panel["mae_pre_exec"] = mae_pre
    panel["terminal_net_return20"] = terminal_return
    panel["label_end_idx"] = label_end_idx
    panel["peak_offset_sessions"] = peak_offset
    reward = np.maximum(panel["mfe20_exec"] - COST_BUFFER, 0.0)
    risk = np.maximum.reduce(
        [
            (panel["mae_pre_exec"] + COST_BUFFER).to_numpy(dtype=float),
            (0.5 * panel["atr20_pct"]).to_numpy(dtype=float),
            np.full(len(panel), 0.01),
        ]
    )
    panel["actual_cprr20"] = np.minimum(reward / risk, CPRR_CAP).astype(
        "float32"
    )
    panel["target_log_cprr"] = np.log1p(panel["actual_cprr20"]).astype(
        "float32"
    )
    panel["good_path"] = (
        (panel["mfe20_exec"] >= 0.08)
        & (panel["mae_pre_exec"] <= 0.04)
    ).astype("int8")
    audit = {
        "valid_label_rows": int(panel["mfe20_exec"].notna().sum()),
        "incomplete_or_noncontinuous_paths": incomplete_path,
        "unbuyable_entry_rows": entry_unbuyable,
        "definition": (
            "signal T close; enter T+1 adjusted open; MFE uses T+2..T+21; "
            "MAE_pre uses T+1 through peak; terminal uses T+21 close; "
            "round-trip cost buffer 0.35%"
        ),
    }
    return panel, audit


def build_model_samples(
    panel: pd.DataFrame,
    calendar: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    finite_features = np.isfinite(
        panel[FEATURES].to_numpy(dtype="float32")
    ).all(axis=1)
    eligible = (
        (panel["trade_date"] >= pd.Timestamp("2021-01-01"))
        & (panel["listing_sessions"] >= 250)
        & (panel["avg_turnover_20"] >= 20_000_000.0)
        & panel["actual_cprr20"].notna()
        & (panel["label_end_idx"] >= 0)
        & finite_features
    )
    model_columns = [
        "instrument_id",
        "trade_date",
        "trade_idx",
        "atr20_pct",
        *FEATURES,
        "mfe20_exec",
        "mae_pre_exec",
        "terminal_net_return20",
        "actual_cprr20",
        "target_log_cprr",
        "good_path",
        "label_end_idx",
        "peak_offset_sessions",
    ]
    samples = panel.loc[eligible, model_columns].copy()
    samples = samples.rename(columns={"trade_date": "signal_date"})
    end_date_map = calendar.set_index("trade_idx")["calendar_date"]
    samples["label_end_date"] = samples["label_end_idx"].map(end_date_map)
    samples = samples.sort_values(
        ["signal_date", "instrument_id"]
    ).reset_index(drop=True)

    price_columns = [
        "instrument_id",
        "trade_idx",
        "trade_date",
        "open",
        "adj_open",
        "adj_high",
        "adj_low",
        "adj_close",
        "limit_status",
    ]
    price_panel = panel.loc[
        panel["trade_date"] >= pd.Timestamp("2024-12-01"), price_columns
    ].copy()
    audit = {
        "eligible_rows": int(len(samples)),
        "eligible_instruments": int(samples["instrument_id"].nunique()),
        "eligible_signal_dates": int(samples["signal_date"].nunique()),
        "price_panel_rows_2024_12_plus": int(len(price_panel)),
    }
    return samples, price_panel, audit


def split_samples(samples: pd.DataFrame) -> dict[str, pd.DataFrame]:
    y2023 = pd.Timestamp("2023-01-01")
    y2024 = pd.Timestamp("2024-01-01")
    y2025 = pd.Timestamp("2025-01-01")
    splits = {
        "initial_train": samples.loc[
            (samples["signal_date"] < y2023)
            & (samples["label_end_date"] < y2023)
        ].copy(),
        "early_stop": samples.loc[
            (samples["signal_date"] >= y2023)
            & (samples["signal_date"] < y2024)
            & (samples["label_end_date"] < y2024)
        ].copy(),
        "final_train": samples.loc[
            (samples["signal_date"] < y2024)
            & (samples["label_end_date"] < y2024)
        ].copy(),
        "calibration": samples.loc[
            (samples["signal_date"] >= y2024)
            & (samples["signal_date"] < y2025)
            & (samples["label_end_date"] < y2025)
        ].copy(),
        "test": samples.loc[samples["signal_date"] >= y2025].copy(),
    }
    for name, frame in splits.items():
        if frame.empty:
            raise ValueError(f"Empty V2 split: {name}")
        frame.reset_index(drop=True, inplace=True)
    return splits


def date_weights(frame: pd.DataFrame) -> np.ndarray:
    counts = frame.groupby("signal_date")["instrument_id"].transform("size")
    dates = max(int(frame["signal_date"].nunique()), 1)
    return (len(frame) / dates / counts).to_numpy(dtype=float)


def model_params(seed: int, estimators: int) -> dict[str, Any]:
    return {
        "objective": "huber",
        "alpha": 0.90,
        "n_estimators": int(estimators),
        "learning_rate": 0.03,
        "num_leaves": 31,
        "max_depth": 7,
        "min_child_samples": 200,
        "subsample": 0.80,
        "subsample_freq": 1,
        "colsample_bytree": 0.80,
        "reg_alpha": 0.10,
        "reg_lambda": 1.0,
        "random_state": seed,
        "n_jobs": -1,
        "verbosity": -1,
    }


def fit_ensemble(
    initial_train: pd.DataFrame,
    early_stop: pd.DataFrame,
    final_train: pd.DataFrame,
) -> tuple[list[lgb.LGBMRegressor], dict[int, int]]:
    models: list[lgb.LGBMRegressor] = []
    iterations: dict[int, int] = {}
    for offset, seed in enumerate(SEEDS):
        fit = initial_train.loc[initial_train["trade_idx"] % 5 == offset]
        valid = early_stop.loc[early_stop["trade_idx"] % 5 == offset]
        final = final_train.loc[final_train["trade_idx"] % 5 == offset]
        log(
            f"Training offset {offset}/4 seed {seed}: "
            f"fit={len(fit):,}, early_stop={len(valid):,}, final={len(final):,}"
        )
        probe = lgb.LGBMRegressor(**model_params(seed, 1200))
        probe.fit(
            fit[FEATURES],
            fit["target_log_cprr"],
            sample_weight=date_weights(fit),
            eval_set=[(valid[FEATURES], valid["target_log_cprr"])],
            eval_sample_weight=[date_weights(valid)],
            eval_metric="l1",
            callbacks=[lgb.early_stopping(100, verbose=False)],
        )
        best = max(int(probe.best_iteration_ or 1200), 10)
        iterations[seed] = best
        model = lgb.LGBMRegressor(**model_params(seed, best))
        model.fit(
            final[FEATURES],
            final["target_log_cprr"],
            sample_weight=date_weights(final),
        )
        models.append(model)
        log(f"Offset {offset} best iteration: {best}")
        del fit, valid, final, probe
        gc.collect()
    return models, iterations


def ensemble_prediction(
    frame: pd.DataFrame,
    models: list[lgb.LGBMRegressor],
) -> pd.DataFrame:
    predictions = np.column_stack(
        [np.maximum(model.predict(frame[FEATURES]), 0.0) for model in models]
    )
    median = np.median(predictions, axis=1)
    q25 = np.quantile(predictions, 0.25, axis=1)
    q75 = np.quantile(predictions, 0.75, axis=1)
    lcb = np.maximum(median - 0.25 * (q75 - q25), 0.0)
    return pd.DataFrame(
        {
            "pred_log_cprr_median": median,
            "pred_log_cprr_iqr": q75 - q25,
            "pred_cprr_raw": np.expm1(median),
            "pred_cprr_lcb_raw": np.expm1(lcb),
        },
        index=frame.index,
    )


def fit_calibrator(
    calibration: pd.DataFrame,
    prediction: pd.DataFrame,
) -> IsotonicRegression:
    x_values = prediction["pred_cprr_lcb_raw"].to_numpy(dtype=float)
    y_values = calibration["actual_cprr20"].to_numpy(dtype=float)
    weights = date_weights(calibration)
    calibrator = IsotonicRegression(
        y_min=0.0,
        y_max=CPRR_CAP,
        increasing=True,
        out_of_bounds="clip",
    )
    if np.unique(x_values).size < 2:
        constant = float(np.average(y_values, weights=weights))
        calibrator.fit([0.0, 1.0], [constant, constant])
    else:
        calibrator.fit(x_values, y_values, sample_weight=weights)
    return calibrator


def apply_scores(
    frame: pd.DataFrame,
    prediction: pd.DataFrame,
    calibrator: IsotonicRegression,
) -> pd.DataFrame:
    result = frame.copy()
    for column in prediction:
        result[column] = prediction[column].to_numpy()
    result["final_cprr20_v2"] = calibrator.predict(
        result["pred_cprr_lcb_raw"]
    )
    result = result.sort_values(
        ["signal_date", "final_cprr20_v2", "instrument_id"],
        ascending=[True, False, True],
    ).reset_index(drop=True)
    result["daily_rank"] = (
        result.groupby("signal_date").cumcount() + 1
    ).astype("int16")
    result["daily_rank_pct"] = result.groupby("signal_date")[
        "final_cprr20_v2"
    ].rank(pct=True, ascending=False, method="first")
    result["rank_top1_signal"] = result["daily_rank"] == 1
    result["entry_signal"] = (
        (result["daily_rank"] <= ENTRY_TOP_N)
        & (result["final_cprr20_v2"] >= ENTRY_CPRR_THRESHOLD)
    )
    return result


def safe_spearman(x_values: pd.Series, y_values: pd.Series) -> float:
    if len(x_values) < 3 or x_values.nunique() < 2 or y_values.nunique() < 2:
        return math.nan
    return float(x_values.corr(y_values, method="spearman"))


def subset_metrics(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "rows": 0,
            "signal_dates": 0,
            "actual_cprr_mean": math.nan,
            "actual_cprr_median": math.nan,
            "terminal_net_return_mean": math.nan,
            "terminal_net_return_median": math.nan,
            "good_path_rate": math.nan,
        }
    return {
        "rows": int(len(frame)),
        "signal_dates": int(frame["signal_date"].nunique()),
        "actual_cprr_mean": float(frame["actual_cprr20"].mean()),
        "actual_cprr_median": float(frame["actual_cprr20"].median()),
        "terminal_net_return_mean": float(
            frame["terminal_net_return20"].mean()
        ),
        "terminal_net_return_median": float(
            frame["terminal_net_return20"].median()
        ),
        "good_path_rate": float(frame["good_path"].mean()),
    }


def prediction_diagnostics(frame: pd.DataFrame) -> dict[str, Any]:
    daily_ic: list[float] = []
    for _, group in frame.groupby("signal_date"):
        value = safe_spearman(
            group["pred_cprr_lcb_raw"], group["actual_cprr20"]
        )
        if np.isfinite(value):
            daily_ic.append(value)
    return {
        "rows": int(len(frame)),
        "signal_dates": int(frame["signal_date"].nunique()),
        "overall_spearman": safe_spearman(
            frame["pred_cprr_lcb_raw"], frame["actual_cprr20"]
        ),
        "daily_rank_ic_mean": (
            float(np.mean(daily_ic)) if daily_ic else math.nan
        ),
        "daily_rank_ic_median": (
            float(np.median(daily_ic)) if daily_ic else math.nan
        ),
        "daily_rank_ic_dates": int(len(daily_ic)),
        "universe": subset_metrics(frame),
        "daily_top1": subset_metrics(frame.loc[frame["daily_rank"] == 1]),
        "daily_top3": subset_metrics(frame.loc[frame["daily_rank"] <= 3]),
        "daily_top5pct": subset_metrics(
            frame.loc[frame["daily_rank_pct"] <= 0.05]
        ),
        "official_entry": subset_metrics(frame.loc[frame["entry_signal"]]),
    }


def feature_importance(models: list[lgb.LGBMRegressor]) -> pd.DataFrame:
    values = []
    for seed, model in zip(SEEDS, models):
        gain = model.booster_.feature_importance(importance_type="gain")
        total = float(gain.sum())
        for feature, item in zip(FEATURES, gain):
            values.append(
                {
                    "seed": seed,
                    "feature": feature,
                    "gain_share": float(item / total) if total else 0.0,
                }
            )
    detail = pd.DataFrame(values)
    return (
        detail.groupby("feature", as_index=False)["gain_share"]
        .mean()
        .sort_values("gain_share", ascending=False)
    )


def candidate_frame(frame: pd.DataFrame, score: str) -> pd.DataFrame:
    result = frame[
        ["instrument_id", "signal_date", "trade_idx", "atr20_pct", score]
    ].copy()
    result = result.rename(columns={"trade_idx": "signal_idx", score: "score"})
    return result


def outcomes_for_selection(
    selected: pd.DataFrame,
    all_outcomes: pd.DataFrame,
    score_column: str,
) -> pd.DataFrame:
    keys = selected[["instrument_id", "signal_date", score_column]].copy()
    result = keys.merge(
        all_outcomes.drop(columns="score", errors="ignore"),
        on=["instrument_id", "signal_date"],
        how="inner",
        validate="one_to_one",
    )
    result["score"] = result[score_column].astype(float)
    return result.drop(columns=score_column)


def build_random_selections(
    test: pd.DataFrame,
    runs: int,
    seed: int,
) -> pd.DataFrame:
    pools = {date: group.index.to_numpy() for date, group in test.groupby("signal_date")}
    records: list[dict[str, Any]] = []
    for run in range(runs):
        rng = np.random.default_rng(seed + run)
        for date, indices in pools.items():
            chosen = int(rng.choice(indices))
            records.append(
                {
                    "run": run,
                    "row_id": int(test.loc[chosen, "row_id"]),
                    "random_score": float(rng.random()),
                    "signal_date": date,
                }
            )
    return pd.DataFrame(records)


def run_portfolios(
    test: pd.DataFrame,
    price_panel: pd.DataFrame,
    calendar: pd.DataFrame,
    config: dict[str, Any],
    random_runs_count: int,
    output_dir: Path,
) -> tuple[dict[str, Any], pd.DataFrame, dict[str, Any]]:
    official = test.loc[test["entry_signal"]].copy()
    rank_top1 = test.loc[test["rank_top1_signal"]].copy()
    test = test.copy()
    test["row_id"] = np.arange(len(test), dtype="int32")
    official = official.merge(
        test[["instrument_id", "signal_date", "row_id"]],
        on=["instrument_id", "signal_date"],
        how="left",
        validate="one_to_one",
    )
    rank_top1 = rank_top1.merge(
        test[["instrument_id", "signal_date", "row_id"]],
        on=["instrument_id", "signal_date"],
        how="left",
        validate="one_to_one",
    )
    random_selections = build_random_selections(
        test, random_runs_count, SEEDS[0] + 1000
    )
    needed_ids = pd.unique(
        pd.concat(
            [official["row_id"], rank_top1["row_id"], random_selections["row_id"]],
            ignore_index=True,
        )
    )
    needed = test.set_index("row_id").loc[needed_ids].reset_index()
    needed_candidates = candidate_frame(needed, "final_cprr20_v2")
    instrument_ids = set(needed_candidates["instrument_id"].unique())
    stock_views = build_stock_views(price_panel, instrument_ids)
    barrier = next(
        item for item in config["barriers"] if item["name"] == "8_4"
    )
    log(f"Computing fixed 8/4 outcomes for {len(needed_candidates):,} unique rows")
    all_outcomes, outcome_audit = build_outcomes(
        needed_candidates, stock_views, calendar, barrier
    )

    official_outcomes = outcomes_for_selection(
        official, all_outcomes, "final_cprr20_v2"
    )
    rank_top1_outcomes = outcomes_for_selection(
        rank_top1, all_outcomes, "final_cprr20_v2"
    )
    official_trades, official_curve, official_metrics = simulate_portfolio(
        official_outcomes, stock_views, calendar, config
    )
    top1_trades, top1_curve, top1_metrics = simulate_portfolio(
        rank_top1_outcomes, stock_views, calendar, config
    )

    outcome_with_id = all_outcomes.merge(
        test[["row_id", "instrument_id", "signal_date"]],
        on=["instrument_id", "signal_date"],
        how="left",
        validate="one_to_one",
    )
    random_records = []
    for run, selection in random_selections.groupby("run"):
        random_outcomes = selection[["row_id", "random_score"]].merge(
            outcome_with_id.drop(columns="score", errors="ignore"),
            on="row_id",
            how="inner",
            validate="one_to_one",
        )
        random_outcomes["score"] = random_outcomes["random_score"]
        _, _, metrics = simulate_portfolio(
            random_outcomes, stock_views, calendar, config
        )
        random_records.append({"run": int(run), **metrics})
        if (int(run) + 1) % 10 == 0:
            log(f"Random portfolio progress: {int(run) + 1}/{random_runs_count}")
    random_runs = pd.DataFrame(random_records)

    official_trades.to_csv(
        output_dir / "official_trades.csv", index=False, encoding="utf-8-sig"
    )
    official_curve.to_csv(
        output_dir / "official_equity.csv", index=False, encoding="utf-8-sig"
    )
    top1_trades.to_csv(
        output_dir / "rank_top1_trades.csv", index=False, encoding="utf-8-sig"
    )
    top1_curve.to_csv(
        output_dir / "rank_top1_equity.csv", index=False, encoding="utf-8-sig"
    )
    random_runs.to_csv(
        output_dir / "random_top1_runs.csv", index=False, encoding="utf-8-sig"
    )
    portfolio = {
        "official_threshold_top1": official_metrics,
        "rank_top1_no_threshold_diagnostic": top1_metrics,
        "random_top1": {
            "runs": int(len(random_runs)),
            "total_return_p05": float(random_runs["total_return"].quantile(0.05)),
            "total_return_median": float(random_runs["total_return"].median()),
            "total_return_p95": float(random_runs["total_return"].quantile(0.95)),
            "average_r_median": float(random_runs["average_r"].median()),
            "profit_factor_median": float(random_runs["profit_factor"].median()),
        },
    }
    return portfolio, random_runs, outcome_audit


def assess(
    test_diagnostics: dict[str, Any],
    portfolio: dict[str, Any],
) -> dict[str, Any]:
    official_path = test_diagnostics["official_entry"]
    universe = test_diagnostics["universe"]
    official_portfolio = portfolio["official_threshold_top1"]
    checks = {
        "official_portfolio_trades_at_least_100": int(
            official_portfolio.get("trades", 0)
        ) >= 100,
        "daily_rank_ic_above_0_03": float(
            test_diagnostics.get("daily_rank_ic_mean", math.nan)
        ) > 0.03,
        "official_terminal_mean_beats_universe": float(
            official_path.get("terminal_net_return_mean", math.nan)
        ) > float(universe.get("terminal_net_return_mean", math.nan)),
        "official_terminal_median_beats_universe": float(
            official_path.get("terminal_net_return_median", math.nan)
        ) > float(universe.get("terminal_net_return_median", math.nan)),
        "official_average_r_above_0_15": float(
            official_portfolio.get("average_r", math.nan)
        ) > 0.15,
        "official_profit_factor_above_1_20": float(
            official_portfolio.get("profit_factor", math.nan)
        ) > 1.20,
        "official_return_beats_random_top1_median": float(
            official_portfolio.get("total_return", math.nan)
        ) > float(portfolio["random_top1"]["total_return_median"]),
        "official_max_drawdown_better_than_minus_15pct": float(
            official_portfolio.get("max_drawdown", math.nan)
        ) > -0.15,
    }
    passed = int(sum(bool(value) for value in checks.values()))
    signal_rows = int(official_path.get("rows", 0))
    if signal_rows < 50:
        decision = "绝对入场阈值不可用；仅根据排序诊断判断是否值得研究"
    elif passed >= 6:
        decision = "可进入冻结前向验证，但不可直接模拟盘或实盘"
    else:
        decision = "V2未通过，不建议进入模拟盘；保留为研究基准"
    return {
        "checks": checks,
        "passed": passed,
        "total_checks": len(checks),
        "decision": decision,
    }


def pct(value: Any) -> str:
    return "NA" if value is None or not np.isfinite(float(value)) else f"{float(value):.2%}"


def num(value: Any, digits: int = 3) -> str:
    return "NA" if value is None or not np.isfinite(float(value)) else f"{float(value):.{digits}f}"


def write_report(path: Path, summary: dict[str, Any]) -> None:
    test = summary["test_diagnostics"]
    portfolio = summary["portfolio"]
    official = portfolio["official_threshold_top1"]
    top1 = portfolio["rank_top1_no_threshold_diagnostic"]
    random = portfolio["random_top1"]
    assessment = summary["assessment"]
    universe_path = test["universe"]
    official_path = test["official_entry"]
    top1_path = test["daily_top1"]
    check_lines = [
        f"- [{'x' if value else ' '}] `{key}`"
        for key, value in assessment["checks"].items()
    ]
    lines = [
        "# FinalCPRR20 V2 日频快速回测",
        "",
        "## 结论",
        "",
        f"**{assessment['decision']}**",
        "",
        f"预设8项标准通过 {assessment['passed']}/8。",
        "",
        "## 排序与路径诊断（2025年以后）",
        "",
        f"- 测试候选 {test['rows']:,} 条、{test['signal_dates']}个信号日。",
        f"- 日截面Rank IC均值/中位数：{num(test['daily_rank_ic_mean'])} / {num(test['daily_rank_ic_median'])}。",
        f"- 正式阈值通过 {official_path['rows']:,} 条、{official_path['signal_dates']}个信号日。",
        "",
        "| 集合 | 样本数 | 实际CPRR均值/中位数 | 20日净收益均值/中位数 | 好路径率 |",
        "|---|---:|---:|---:|---:|",
        f"| 全候选 | {universe_path['rows']:,} | {num(universe_path['actual_cprr_mean'])} / {num(universe_path['actual_cprr_median'])} | {pct(universe_path['terminal_net_return_mean'])} / {pct(universe_path['terminal_net_return_median'])} | {pct(universe_path['good_path_rate'])} |",
        f"| 每日Top1（无阈值诊断） | {top1_path['rows']:,} | {num(top1_path['actual_cprr_mean'])} / {num(top1_path['actual_cprr_median'])} | {pct(top1_path['terminal_net_return_mean'])} / {pct(top1_path['terminal_net_return_median'])} | {pct(top1_path['good_path_rate'])} |",
        f"| 正式CPRR≥{ENTRY_CPRR_THRESHOLD:.2f}且Top1 | {official_path['rows']:,} | {num(official_path['actual_cprr_mean'])} / {num(official_path['actual_cprr_median'])} | {pct(official_path['terminal_net_return_mean'])} / {pct(official_path['terminal_net_return_median'])} | {pct(official_path['good_path_rate'])} |",
        "",
        "## 固定8%止盈/4%止损组合回测",
        "",
        "| 方法 | 交易数 | 总收益 | 平均R | 盈利因子 | 最大回撤 |",
        "|---|---:|---:|---:|---:|---:|",
        f"| 正式阈值Top1 | {int(official.get('trades', 0))} | {pct(official.get('total_return'))} | {num(official.get('average_r'))} | {num(official.get('profit_factor'))} | {pct(official.get('max_drawdown'))} |",
        f"| 每日Top1无阈值诊断 | {int(top1.get('trades', 0))} | {pct(top1.get('total_return'))} | {num(top1.get('average_r'))} | {num(top1.get('profit_factor'))} | {pct(top1.get('max_drawdown'))} |",
        f"| 随机Top1中位数 | — | {pct(random.get('total_return_median'))} | {num(random.get('average_r_median'))} | {num(random.get('profit_factor_median'))} | — |",
        "",
        "## 预设验收项",
        "",
        *check_lines,
        "",
        "## 解释边界",
        "",
        "标签先在完整、未按流动性删行的面板上计算，再按T日条件筛选，避免stage1的未来20行期限漂移。标签使用T+1开盘入场、T+2至T+21的可卖高点、入场日至峰值前低点、0.35%成本缓冲和ATR风险下限。测试退出固定为8%/4%/20日，只用于与既有回测口径对照。2025—2026已经被多次研究，因此本结果仍是准样本外；真正独立验证必须使用2026-08-11以后新增数据。",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="FinalCPRR20 V2 daily ML backtest")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--start-year", type=int, default=2019)
    parser.add_argument("--random-runs", type=int, default=RANDOM_RUNS_DEFAULT)
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    config = load_config(resolve_from(script_dir, args.config))
    data_root = resolve_from(script_dir, str(config["data_root"]))
    output_dir = resolve_from(script_dir, str(config["output_dir"])) / "ml_v2"
    output_dir.mkdir(parents=True, exist_ok=True)

    calendar_file = pd.read_parquet(data_root / "trading_calendar.parquet")
    end_year = int(pd.to_datetime(calendar_file["calendar_date"]).dt.year.max())
    del calendar_file
    panel, calendar, panel_audit = load_daily_panel(
        data_root, args.start_year, end_year
    )
    panel = add_daily_features(panel)
    panel, label_audit = add_executable_labels(panel)
    samples, price_panel, sample_audit = build_model_samples(panel, calendar)
    del panel
    gc.collect()

    splits = split_samples(samples)
    split_sizes = {name: int(len(frame)) for name, frame in splits.items()}
    log(f"V2 split sizes: {split_sizes}")
    models, iterations = fit_ensemble(
        splits["initial_train"], splits["early_stop"], splits["final_train"]
    )

    log("Predicting 2024 calibration and 2025+ test")
    calibration_prediction = ensemble_prediction(splits["calibration"], models)
    test_prediction = ensemble_prediction(splits["test"], models)
    calibrator = fit_calibrator(splits["calibration"], calibration_prediction)
    calibration = apply_scores(
        splits["calibration"], calibration_prediction, calibrator
    )
    test = apply_scores(splits["test"], test_prediction, calibrator)
    calibration_diagnostics = prediction_diagnostics(calibration)
    test_diagnostics = prediction_diagnostics(test)
    log(f"Test daily Rank IC mean: {test_diagnostics['daily_rank_ic_mean']}")
    log(
        f"Official entry rows: {test_diagnostics['official_entry']['rows']:,}; "
        f"Top1 rows: {test_diagnostics['daily_top1']['rows']:,}"
    )

    save_columns = [
        "instrument_id",
        "signal_date",
        "trade_idx",
        "actual_cprr20",
        "mfe20_exec",
        "mae_pre_exec",
        "terminal_net_return20",
        "good_path",
        "pred_cprr_raw",
        "pred_cprr_lcb_raw",
        "final_cprr20_v2",
        "daily_rank",
        "entry_signal",
    ]
    calibration.loc[calibration["daily_rank"] <= 10, save_columns].to_csv(
        output_dir / "calibration_daily_top10.csv",
        index=False,
        encoding="utf-8-sig",
    )
    test.loc[test["daily_rank"] <= 10, save_columns].to_csv(
        output_dir / "test_daily_top10.csv", index=False, encoding="utf-8-sig"
    )
    test.loc[test["entry_signal"], save_columns].to_csv(
        output_dir / "official_candidates.csv", index=False, encoding="utf-8-sig"
    )
    feature_importance(models).to_csv(
        output_dir / "feature_importance.csv", index=False, encoding="utf-8-sig"
    )
    joblib.dump(
        {
            "version": "FinalCPRR20_V2",
            "features": FEATURES,
            "models": models,
            "calibrator": calibrator,
            "iterations": iterations,
            "entry_threshold": ENTRY_CPRR_THRESHOLD,
            "entry_top_n": ENTRY_TOP_N,
            "cost_buffer": COST_BUFFER,
        },
        output_dir / "final_cprr20_v2_bundle.joblib",
        compress=3,
    )

    portfolio, random_runs, outcome_audit = run_portfolios(
        test,
        price_panel,
        calendar,
        config,
        int(args.random_runs),
        output_dir,
    )
    assessment = assess(test_diagnostics, portfolio)
    summary = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "version": "FinalCPRR20_V2",
        "frozen_design": {
            "target": "log1p(executable actual CPRR20)",
            "entry": f"FinalCPRR20_V2 >= {ENTRY_CPRR_THRESHOLD} and daily Top{ENTRY_TOP_N}",
            "model": "5 LightGBM Huber regressors on trading-day offsets 0..4",
            "calibration": "2024 date-weighted isotonic regression",
            "test": "2025+ evaluated once; quasi-OOS because period was previously studied",
        },
        "features": FEATURES,
        "model_iterations": iterations,
        "split_sizes": split_sizes,
        "panel_audit": panel_audit,
        "label_audit": label_audit,
        "sample_audit": sample_audit,
        "calibration_diagnostics": calibration_diagnostics,
        "test_diagnostics": test_diagnostics,
        "portfolio": portfolio,
        "outcome_audit": outcome_audit,
        "assessment": assessment,
    }
    write_json(output_dir / "summary.json", summary)
    write_report(output_dir / "report.md", summary)
    log(f"Decision: {assessment['decision']}")
    log(f"V2 outputs: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
