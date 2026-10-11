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

from backtest_mfe_mae import build_stock_views, load_config, resolve_from, row_at
from ml_cprr_v2 import add_daily_features, load_daily_panel
from ml_cprr_v3 import DEFAULT_CAPITAL, FEATURES


HORIZONS = (20, 30, 40)
SCORE_THRESHOLDS = (0.0, 1.0, 1.5, 2.0)
SEED = 20260816
B_FLOOR = 0.01
A_CAP = 1.00
B_CAP = 0.30
ENTRY_GAP_MAX = 0.03
MIN_STOP = 0.04
MAX_STOP = 0.08
ATR_STOP_MULTIPLIER = 1.50
TARGET_CAPTURE = 0.80
MIN_PLANNED_RR = 1.20
STOP_MODES = ("predicted_b", "atr_clipped")
RISK_PER_TRADE = 0.01
MAX_POSITION_WEIGHT = 0.25


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


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(json_value(value), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def add_ab_labels(
    panel: pd.DataFrame,
    horizon: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    log(f"Building strict executable a/b labels for H={horizon}")
    count = len(panel)
    actual_a = np.full(count, np.nan, dtype="float32")
    actual_b = np.full(count, np.nan, dtype="float32")
    label_end_idx = np.full(count, -1, dtype="int32")
    entry_gap = np.full(count, np.nan, dtype="float32")
    entry_executable = np.zeros(count, dtype=bool)
    incomplete = 0
    unbuyable = 0
    high_gap = 0

    groups = panel.groupby("instrument_id", sort=False).indices
    for number, positions in enumerate(groups.values(), start=1):
        pos = np.asarray(positions, dtype=np.int64)
        length = len(pos)
        if length <= horizon + 1:
            continue
        adj_open = panel.loc[pos, "adj_open"].to_numpy(dtype=float)
        adj_high = panel.loc[pos, "adj_high"].to_numpy(dtype=float)
        adj_low = panel.loc[pos, "adj_low"].to_numpy(dtype=float)
        raw_open = panel.loc[pos, "open"].to_numpy(dtype=float)
        raw_close = panel.loc[pos, "close"].to_numpy(dtype=float)
        trade_idx = panel.loc[pos, "trade_idx"].to_numpy(dtype="int32")
        status = panel.loc[pos, "limit_status"].to_numpy(dtype="int8")

        candidates = length - (horizon + 1)
        signal_rows = pos[:candidates]
        entry = adj_open[1 : candidates + 1]
        high_windows = sliding_window_view(adj_high[2:], horizon)
        low_windows = sliding_window_view(adj_low[1:], horizon + 1)
        end_indices = trade_idx[horizon + 1 :]
        gaps = raw_open[1 : candidates + 1] / raw_close[:candidates] - 1.0
        high_value = high_windows.max(axis=1)
        low_value = low_windows.min(axis=1)
        continuous = end_indices - trade_idx[:candidates] == horizon + 1
        finite = (
            np.isfinite(entry)
            & (entry > 0)
            & np.isfinite(high_value)
            & np.isfinite(low_value)
            & np.isfinite(gaps)
        )
        valid = continuous & finite
        buyable = status[1 : candidates + 1] != 3
        acceptable_gap = gaps <= ENTRY_GAP_MAX
        executable = valid & buyable & acceptable_gap
        incomplete += int((~valid).sum())
        unbuyable += int((valid & ~buyable).sum())
        high_gap += int((valid & buyable & ~acceptable_gap).sum())

        valid_rows = signal_rows[valid]
        actual_a[valid_rows] = np.maximum(
            high_value[valid] / entry[valid] - 1.0, 0.0
        ).astype("float32")
        actual_b[valid_rows] = np.maximum(
            1.0 - low_value[valid] / entry[valid], 0.0
        ).astype("float32")
        label_end_idx[valid_rows] = end_indices[valid]
        entry_gap[signal_rows[finite]] = gaps[finite].astype("float32")
        entry_executable[signal_rows[executable]] = True

        if number % 800 == 0 or number == len(groups):
            log(f"H={horizon} label progress {number:,}/{len(groups):,}")

    panel["actual_a"] = actual_a
    panel["actual_b"] = actual_b
    panel["actual_ratio"] = (
        panel["actual_a"] / np.maximum(panel["actual_b"], B_FLOOR)
    ).astype("float32")
    panel["label_end_idx"] = label_end_idx
    panel["entry_gap"] = entry_gap
    panel["entry_executable"] = entry_executable
    return panel, {
        "horizon": horizon,
        "valid_label_rows": int(panel["actual_a"].notna().sum()),
        "entry_executable_rows": int(entry_executable.sum()),
        "incomplete_or_noncontinuous": incomplete,
        "unbuyable_t1": unbuyable,
        "entry_gap_over_3pct": high_gap,
        "b_below_1pct_rate": float(
            (panel.loc[panel["actual_b"].notna(), "actual_b"] < B_FLOOR).mean()
        ),
        "definition": (
            f"T+1 open entry; a=max high in T+2..T+{horizon + 1}; "
            f"b=max loss from lows in T+1..T+{horizon + 1}; ratio uses 1% b floor"
        ),
    }


def build_samples(
    panel: pd.DataFrame,
    calendar: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    finite_features = np.isfinite(
        panel[FEATURES].to_numpy(dtype="float32")
    ).all(axis=1)
    eligible = (
        (panel["trade_date"] >= pd.Timestamp("2021-01-01"))
        & (panel["listing_sessions"] >= 250)
        & (panel["avg_turnover_20"] >= 100_000_000.0)
        & panel["actual_a"].notna()
        & panel["actual_b"].notna()
        & (panel["label_end_idx"] >= 0)
        & finite_features
    )
    columns = [
        "instrument_id",
        "trade_date",
        "trade_idx",
        "open",
        "atr20_pct",
        *FEATURES,
        "actual_a",
        "actual_b",
        "actual_ratio",
        "label_end_idx",
        "entry_gap",
        "entry_executable",
    ]
    samples = panel.loc[eligible, columns].copy()
    samples = samples.rename(columns={"trade_date": "signal_date"})
    end_date_map = calendar.set_index("trade_idx")["calendar_date"]
    samples["label_end_date"] = samples["label_end_idx"].map(end_date_map)
    samples = samples.sort_values(
        ["signal_date", "instrument_id"]
    ).reset_index(drop=True)
    return samples, {
        "rows": int(len(samples)),
        "instruments": int(samples["instrument_id"].nunique()),
        "signal_dates": int(samples["signal_date"].nunique()),
        "executable_rate": float(samples["entry_executable"].mean()),
        "actual_a_median": float(samples["actual_a"].median()),
        "actual_b_median": float(samples["actual_b"].median()),
        "actual_ratio_median": float(samples["actual_ratio"].median()),
    }


def date_weights(frame: pd.DataFrame) -> np.ndarray:
    counts = frame.groupby("signal_date")["instrument_id"].transform("size")
    return (1.0 / counts).to_numpy(dtype=float)


def model_params(alpha: float, quick: bool) -> dict[str, Any]:
    return {
        "objective": "quantile",
        "alpha": alpha,
        "n_estimators": 600 if quick else 1400,
        "learning_rate": 0.04 if quick else 0.025,
        "num_leaves": 31,
        "max_depth": 7,
        "min_child_samples": 150,
        "subsample": 0.80,
        "subsample_freq": 1,
        "colsample_bytree": 0.85,
        "reg_alpha": 0.10,
        "reg_lambda": 1.0,
        "random_state": SEED,
        "n_jobs": -1,
        "verbosity": -1,
    }


def quick_date_sample(frame: pd.DataFrame, quick: bool) -> pd.DataFrame:
    if not quick:
        return frame
    dates = frame["signal_date"].drop_duplicates().to_numpy()[::3]
    return frame.loc[frame["signal_date"].isin(dates)].copy()


def fit_models(
    samples: pd.DataFrame,
    validation_year: int,
    horizon: int,
    quick: bool,
) -> tuple[lgb.LGBMRegressor, lgb.LGBMRegressor, dict[str, float], dict[str, Any]]:
    validation_start = pd.Timestamp(f"{validation_year}-01-01")
    calibration_start = validation_start - pd.DateOffset(months=6)
    train = samples.loc[
        samples["entry_executable"]
        & (samples["signal_date"] < calibration_start)
        & (samples["label_end_date"] < calibration_start)
    ].copy()
    calibration = samples.loc[
        samples["entry_executable"]
        & (samples["signal_date"] >= calibration_start)
        & (samples["signal_date"] < validation_start)
        & (samples["label_end_date"] < validation_start)
    ].copy()
    train = quick_date_sample(train, quick)
    train = train.sort_values(["signal_date", "instrument_id"]).reset_index(
        drop=True
    )
    calibration = calibration.sort_values(
        ["signal_date", "instrument_id"]
    ).reset_index(drop=True)
    if train.empty or calibration.empty:
        raise ValueError(
            f"Empty a/b train/calibration H={horizon}, year={validation_year}"
        )
    weights = date_weights(train)
    eval_weights = date_weights(calibration)
    log(
        f"Fit a/b H={horizon} for {validation_year}: train={len(train):,}, "
        f"calibration={len(calibration):,}"
    )
    model_a = lgb.LGBMRegressor(**model_params(0.50, quick))
    model_a.fit(
        train[FEATURES],
        train["actual_a"].clip(0.0, A_CAP),
        sample_weight=weights,
        eval_set=[(calibration[FEATURES], calibration["actual_a"].clip(0.0, A_CAP))],
        eval_sample_weight=[eval_weights],
        eval_metric="quantile",
        callbacks=[lgb.early_stopping(60, verbose=False)],
    )
    model_b = lgb.LGBMRegressor(**model_params(0.75, quick))
    model_b.fit(
        train[FEATURES],
        train["actual_b"].clip(0.0, B_CAP),
        sample_weight=weights,
        eval_set=[(calibration[FEATURES], calibration["actual_b"].clip(0.0, B_CAP))],
        eval_sample_weight=[eval_weights],
        eval_metric="quantile",
        callbacks=[lgb.early_stopping(60, verbose=False)],
    )
    raw_a = model_a.predict(calibration[FEATURES], num_iteration=model_a.best_iteration_)
    raw_b = model_b.predict(calibration[FEATURES], num_iteration=model_b.best_iteration_)
    a_offset = float(np.median(calibration["actual_a"].to_numpy() - raw_a))
    b_residual = calibration["actual_b"].to_numpy() - raw_b
    b_offset = float(np.quantile(b_residual, 0.75))
    calibrated_a = np.clip(raw_a + a_offset, 0.0, A_CAP)
    calibrated_b = np.clip(raw_b + b_offset, 0.0, B_CAP)
    calibration_a_mae = float(
        np.mean(np.abs(calibrated_a - calibration["actual_a"].to_numpy()))
    )
    calibration_b_coverage = float(
        (calibration["actual_b"].to_numpy() <= calibrated_b).mean()
    )
    offsets = {"a": a_offset, "b": b_offset}
    audit = {
        "validation_year": validation_year,
        "horizon": horizon,
        "train_rows": int(len(train)),
        "train_dates": int(train["signal_date"].nunique()),
        "calibration_rows": int(len(calibration)),
        "calibration_dates": int(calibration["signal_date"].nunique()),
        "a_iterations": int(model_a.best_iteration_ or model_a.n_estimators),
        "b_iterations": int(model_b.best_iteration_ or model_b.n_estimators),
        "a_offset": a_offset,
        "b_offset": b_offset,
        "calibration_a_mae": calibration_a_mae,
        "calibration_b_coverage": calibration_b_coverage,
    }
    return model_a, model_b, offsets, audit


def safe_spearman(x: pd.Series, y: pd.Series) -> float:
    if len(x) < 3 or x.nunique() < 2 or y.nunique() < 2:
        return math.nan
    return float(x.corr(y, method="spearman"))


def e1_stop_values(frame: pd.DataFrame, risk_column: str, stop_mode: str) -> np.ndarray:
    atr_stop = ATR_STOP_MULTIPLIER * frame["atr20_pct"].to_numpy(dtype=float)
    if stop_mode == "atr_clipped":
        return np.clip(atr_stop, MIN_STOP, MAX_STOP)
    if stop_mode == "predicted_b":
        return np.maximum.reduce(
            [
                np.full(len(frame), MIN_STOP),
                frame[risk_column].to_numpy(dtype=float),
                atr_stop,
            ]
        )
    raise ValueError(f"Unsupported stop mode: {stop_mode}")


def score_period(
    samples: pd.DataFrame,
    model_a: lgb.LGBMRegressor,
    model_b: lgb.LGBMRegressor,
    offsets: dict[str, float],
    start: pd.Timestamp,
    end: pd.Timestamp | None,
    stop_mode: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    mask = samples["signal_date"] >= start
    if end is not None:
        mask &= samples["signal_date"] < end
    frame = samples.loc[mask].copy()
    frame["predicted_a"] = np.clip(
        model_a.predict(frame[FEATURES], num_iteration=model_a.best_iteration_)
        + offsets["a"],
        0.0,
        A_CAP,
    )
    frame["predicted_b"] = np.clip(
        model_b.predict(frame[FEATURES], num_iteration=model_b.best_iteration_)
        + offsets["b"],
        0.0,
        B_CAP,
    )
    frame["final_score"] = frame["predicted_a"] / np.maximum(
        frame["predicted_b"], B_FLOOR
    )
    frame["e1_stop_pct"] = e1_stop_values(frame, "predicted_b", stop_mode)
    frame["e1_target_pct"] = TARGET_CAPTURE * frame["predicted_a"]
    frame["planned_rr"] = frame["e1_target_pct"] / frame["e1_stop_pct"]
    frame["e1_feasible"] = (
        (frame["e1_stop_pct"] <= MAX_STOP)
        & (frame["planned_rr"] >= MIN_PLANNED_RR)
    )
    daily_ic = []
    for _, group in frame.groupby("signal_date", sort=False):
        value = safe_spearman(group["final_score"], group["actual_ratio"])
        if np.isfinite(value):
            daily_ic.append(value)
    ordered = frame.sort_values(
        ["signal_date", "final_score", "instrument_id"],
        ascending=[True, False, True],
    )
    top1 = ordered.groupby("signal_date", sort=False).head(1)
    keep = [
        "instrument_id",
        "signal_date",
        "trade_idx",
        "open",
        "atr20_pct",
        "entry_gap",
        "entry_executable",
        "actual_a",
        "actual_b",
        "actual_ratio",
        "predicted_a",
        "predicted_b",
        "final_score",
        "e1_stop_pct",
        "e1_target_pct",
        "planned_rr",
        "e1_feasible",
    ]
    scored = ordered[keep].reset_index(drop=True)
    top1 = top1[keep]
    return scored, {
        "rows": int(len(frame)),
        "signal_dates": int(frame["signal_date"].nunique()),
        "daily_rank_ic_mean": float(np.mean(daily_ic)) if daily_ic else math.nan,
        "daily_rank_ic_median": float(np.median(daily_ic)) if daily_ic else math.nan,
        "top1_actual_ratio_mean": float(top1["actual_ratio"].mean()),
        "top1_actual_ratio_median": float(top1["actual_ratio"].median()),
        "universe_actual_ratio_mean": float(frame["actual_ratio"].mean()),
        "universe_actual_ratio_median": float(frame["actual_ratio"].median()),
        "top1_actual_a_mean": float(top1["actual_a"].mean()),
        "universe_actual_a_mean": float(frame["actual_a"].mean()),
        "top1_actual_b_mean": float(top1["actual_b"].mean()),
        "universe_actual_b_mean": float(frame["actual_b"].mean()),
        "b75_coverage": float((frame["actual_b"] <= frame["predicted_b"]).mean()),
        "e1_feasible_top1_rate": float(top1["e1_feasible"].mean()),
    }


def daily_top1(scored: pd.DataFrame) -> pd.DataFrame:
    return (
        scored.sort_values(
            ["signal_date", "final_score", "instrument_id"],
            ascending=[True, False, True],
        )
        .groupby("signal_date", sort=False)
        .head(1)
        .sort_values("signal_date")
        .copy()
    )


def e1_outcome(
    candidate: Any,
    stock_views: dict[str, pd.DataFrame],
    calendar_dates: list[pd.Timestamp],
    horizon: int,
) -> tuple[dict[str, Any] | None, str]:
    if not bool(candidate.entry_executable):
        reason = (
            "ENTRY_GAP_OVER_3PCT"
            if np.isfinite(float(candidate.entry_gap))
            and float(candidate.entry_gap) > ENTRY_GAP_MAX
            else "ENTRY_UNBUYABLE"
        )
        return None, reason
    if not bool(candidate.e1_feasible):
        return None, "E1_INFEASIBLE"
    stock = stock_views.get(candidate.instrument_id)
    if stock is None:
        return None, "NO_PRICE_VIEW"
    signal_idx = int(candidate.trade_idx)
    entry_idx = signal_idx + 1
    end_idx = signal_idx + horizon + 1
    entry = row_at(stock, entry_idx)
    if entry is None or int(entry["limit_status"]) == 3:
        return None, "ENTRY_UNBUYABLE"
    entry_scale = float(entry["adj_open"])
    entry_raw = float(entry["open"])
    stop_pct = float(candidate.e1_stop_pct)
    target_pct = float(candidate.e1_target_pct)
    stop_scale = entry_scale * (1.0 - stop_pct)
    target_scale = entry_scale * (1.0 + target_pct)
    pending_entry_stop = float(entry["adj_low"]) <= stop_scale
    exit_idx: int | None = None
    exit_scale: float | None = None
    exit_timing = ""
    exit_reason = ""

    for current_idx in range(entry_idx + 1, end_idx + 1):
        current = row_at(stock, current_idx)
        if current is None:
            continue
        sellable = int(current["limit_status"]) != 6
        current_open = float(current["adj_open"])
        current_high = float(current["adj_high"])
        current_low = float(current["adj_low"])
        if pending_entry_stop and sellable:
            exit_idx, exit_scale = current_idx, current_open
            exit_timing, exit_reason = "OPEN", "ENTRY_DAY_STOP_NEXT_OPEN"
            break
        if sellable and current_open <= stop_scale:
            exit_idx, exit_scale = current_idx, current_open
            exit_timing, exit_reason = "OPEN", "GAP_STOP"
            break
        if sellable and current_open >= target_scale:
            exit_idx, exit_scale = current_idx, current_open
            exit_timing, exit_reason = "OPEN", "GAP_TARGET"
            break
        hit_stop = current_low <= stop_scale
        hit_target = current_high >= target_scale
        if sellable and hit_stop:
            exit_idx, exit_scale = current_idx, stop_scale
            exit_timing = "INTRADAY"
            exit_reason = "BOTH_STOP_FIRST" if hit_target else "STOP"
            break
        if sellable and hit_target:
            exit_idx, exit_scale = current_idx, target_scale
            exit_timing, exit_reason = "INTRADAY", "TARGET"
            break

    if exit_idx is None:
        current = row_at(stock, end_idx)
        if current is not None and int(current["limit_status"]) != 6:
            exit_idx, exit_scale = end_idx, float(current["adj_close"])
            exit_timing, exit_reason = "CLOSE", "TIME_EXIT"
        else:
            for later_idx in stock.index[stock.index > end_idx]:
                later = row_at(stock, int(later_idx))
                if later is not None and int(later["limit_status"]) != 6:
                    exit_idx, exit_scale = int(later_idx), float(later["adj_open"])
                    exit_timing, exit_reason = "OPEN", "TIME_EXIT_DELAYED"
                    break
    if exit_idx is None or exit_scale is None:
        return None, "NO_SELLABLE_EXIT"
    return {
        "instrument_id": candidate.instrument_id,
        "signal_date": pd.Timestamp(candidate.signal_date),
        "signal_idx": signal_idx,
        "entry_date": calendar_dates[entry_idx],
        "entry_idx": entry_idx,
        "exit_date": calendar_dates[exit_idx],
        "exit_idx": exit_idx,
        "exit_timing": exit_timing,
        "entry_raw_price": entry_raw,
        "entry_scale": entry_scale,
        "exit_scale": exit_scale,
        "gross_return": exit_scale / entry_scale - 1.0,
        "exit_reason": exit_reason,
        "stop_pct": stop_pct,
        "target_pct": target_pct,
        "planned_rr": float(candidate.planned_rr),
        "predicted_a": float(candidate.predicted_a),
        "predicted_b": float(candidate.predicted_b),
        "final_score": float(candidate.final_score),
        "actual_a": float(candidate.actual_a),
        "actual_b": float(candidate.actual_b),
        "actual_ratio": float(candidate.actual_ratio),
        "atr20_pct": float(candidate.atr20_pct),
        "horizon": horizon,
    }, "FILLED"


def empty_metrics(capital: float) -> dict[str, Any]:
    return {
        "trades": 0,
        "total_return": 0.0,
        "cagr": 0.0,
        "average_r": math.nan,
        "profit_factor": math.nan,
        "win_rate": math.nan,
        "max_drawdown": 0.0,
        "final_equity": capital,
        "skipped_holding_signals": 0,
        "cancelled_entries": 0,
        "max_concurrent_positions": 0,
    }


def simulate_e1(
    signals: pd.DataFrame,
    stock_views: dict[str, pd.DataFrame],
    calendar: pd.DataFrame,
    horizon: int,
    config: dict[str, Any],
    capital: float,
    cache: dict[tuple[Any, ...], tuple[dict[str, Any] | None, str]] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any], list[pd.Timestamp]]:
    if signals.empty:
        return pd.DataFrame(), empty_metrics(capital), []
    cache = {} if cache is None else cache
    calendar_dates = pd.to_datetime(calendar["calendar_date"]).tolist()
    equity = capital
    marks = [capital]
    trades = []
    considered_dates = []
    busy_until = -1
    skipped = 0
    cancelled = 0
    reasons: dict[str, int] = {}
    buy_cost = float(config["buy_cost_rate"])
    sell_cost = float(config["sell_cost_rate"])

    for candidate in signals.sort_values("signal_date").itertuples(index=False):
        signal_idx = int(candidate.trade_idx)
        if signal_idx < busy_until:
            skipped += 1
            continue
        considered_dates.append(pd.Timestamp(candidate.signal_date))
        key = (
            str(candidate.instrument_id),
            signal_idx,
            horizon,
            round(float(candidate.e1_stop_pct), 7),
            round(float(candidate.e1_target_pct), 7),
        )
        if key not in cache:
            cache[key] = e1_outcome(candidate, stock_views, calendar_dates, horizon)
        outcome, reason = cache[key]
        if outcome is None:
            cancelled += 1
            reasons[reason] = reasons.get(reason, 0) + 1
            continue
        effective_risk = float(outcome["stop_pct"]) + max(
            0.5 * float(outcome["atr20_pct"]), 0.01
        )
        target_notional = min(
            equity * MAX_POSITION_WEIGHT,
            equity * RISK_PER_TRADE / effective_risk,
            equity / (1.0 + buy_cost),
        )
        shares = math.floor(
            target_notional / float(outcome["entry_raw_price"]) / 100.0
        ) * 100
        if shares < 100:
            cancelled += 1
            reasons["ROUND_LOT_TOO_SMALL"] = reasons.get("ROUND_LOT_TOO_SMALL", 0) + 1
            continue
        notional = shares * float(outcome["entry_raw_price"])
        purchase_cost = notional * buy_cost
        proceeds = (
            notional
            * float(outcome["exit_scale"])
            / float(outcome["entry_scale"])
            * (1.0 - sell_cost)
        )
        pnl = proceeds - notional - purchase_cost
        net_return = pnl / notional
        equity += pnl
        marks.append(equity)
        trades.append(
            {
                **outcome,
                "shares": shares,
                "notional_cny": notional,
                "buy_cost_cny": purchase_cost,
                "sell_proceeds_cny": proceeds,
                "pnl_cny": pnl,
                "net_return": net_return,
                "net_r": net_return / float(outcome["stop_pct"]),
                "equity_after": equity,
            }
        )
        busy_until = int(outcome["exit_idx"])

    frame = pd.DataFrame(trades)
    if frame.empty:
        metrics = empty_metrics(capital)
    else:
        gains = float(frame.loc[frame["pnl_cny"] > 0, "pnl_cny"].sum())
        losses = float(-frame.loc[frame["pnl_cny"] < 0, "pnl_cny"].sum())
        mark_series = pd.Series(marks, dtype=float)
        drawdown = mark_series / mark_series.cummax() - 1.0
        elapsed = max(
            (pd.Timestamp(frame["exit_date"].iloc[-1]) - pd.Timestamp(frame["entry_date"].iloc[0])).days,
            1,
        )
        total_return = equity / capital - 1.0
        metrics = {
            "trades": int(len(frame)),
            "total_return": total_return,
            "cagr": (1.0 + total_return) ** (365.25 / elapsed) - 1.0,
            "average_r": float(frame["net_r"].mean()),
            "profit_factor": gains / losses if losses > 0 else math.inf,
            "win_rate": float((frame["net_return"] > 0).mean()),
            "max_drawdown": float(drawdown.min()),
            "final_equity": equity,
            "max_concurrent_positions": 1,
        }
    metrics["skipped_holding_signals"] = skipped
    metrics["cancelled_entries"] = cancelled
    metrics["cancellation_reasons"] = reasons
    metrics["decision_dates"] = int(len(considered_dates))
    return frame, metrics, considered_dates


def choose_threshold(
    top1: pd.DataFrame,
    views: dict[str, pd.DataFrame],
    calendar: pd.DataFrame,
    horizon: int,
    config: dict[str, Any],
    capital: float,
    score_thresholds: tuple[float, ...] = SCORE_THRESHOLDS,
) -> tuple[dict[str, Any], pd.DataFrame, list[dict[str, Any]]]:
    rows = []
    frames = {}
    cache: dict[tuple[Any, ...], tuple[dict[str, Any] | None, str]] = {}
    base_trades = 0
    for threshold in score_thresholds:
        signals = top1.loc[
            top1["e1_feasible"] & (top1["final_score"] >= threshold)
        ]
        trades, metrics, _ = simulate_e1(
            signals, views, calendar, horizon, config, capital, cache
        )
        if threshold == 0.0:
            base_trades = int(metrics["trades"])
        yearly_returns = []
        positive_years = 0
        for year in (2022, 2023, 2024):
            year_signals = signals.loc[signals["signal_date"].dt.year == year]
            _, year_metrics, _ = simulate_e1(
                year_signals, views, calendar, horizon, config, capital, cache
            )
            value = float(year_metrics["total_return"])
            yearly_returns.append(value)
            positive_years += int(value > 0)
        row = {
            "horizon": horizon,
            "threshold": threshold,
            **metrics,
            "positive_years": positive_years,
            "median_year_return": float(np.median(yearly_returns)),
            "year_2022_return": yearly_returns[0],
            "year_2023_return": yearly_returns[1],
            "year_2024_return": yearly_returns[2],
        }
        rows.append(row)
        frames[threshold] = trades
    minimum = max(12, int(math.ceil(base_trades * 0.50)))
    eligible = [row for row in rows if int(row["trades"]) >= minimum]
    if not eligible:
        eligible = [row for row in rows if row["threshold"] == 0.0]
    best = max(
        eligible,
        key=lambda row: (
            int(row["positive_years"]),
            float(row["median_year_return"]),
            float(row["total_return"]),
            float(row["profit_factor"] or -math.inf),
            -float(row["threshold"]),
        ),
    )
    return best, frames[float(best["threshold"])], rows


def random_frames(
    pool: pd.DataFrame,
    dates: list[pd.Timestamp],
    runs: int,
) -> list[pd.DataFrame]:
    wanted = set(pd.to_datetime(dates))
    reduced = pool.loc[pool["signal_date"].isin(wanted)].copy()
    groups = {
        pd.Timestamp(date): group.index.to_numpy()
        for date, group in reduced.groupby("signal_date", sort=False)
    }
    output = []
    for run in range(runs):
        rng = np.random.default_rng(SEED + 1000 + run)
        selected = []
        for date in sorted(wanted):
            indices = groups.get(pd.Timestamp(date))
            if indices is not None and len(indices):
                selected.append(int(rng.choice(indices)))
        output.append(reduced.loc[selected].copy() if selected else reduced.head(0).copy())
    return output


def paired_random(
    eligible_pool: pd.DataFrame,
    decision_dates: list[pd.Timestamp],
    model_ids: set[str],
    price_panel: pd.DataFrame,
    calendar: pd.DataFrame,
    horizon: int,
    config: dict[str, Any],
    capital: float,
    runs: int,
) -> pd.DataFrame:
    selections = random_frames(eligible_pool, decision_dates, runs)
    random_ids = {
        str(value)
        for selection in selections
        for value in selection["instrument_id"].unique()
    }
    views = build_stock_views(price_panel, model_ids | random_ids)
    cache: dict[tuple[Any, ...], tuple[dict[str, Any] | None, str]] = {}
    rows = []
    for run, selection in enumerate(selections):
        _, metrics, _ = simulate_e1(
            selection, views, calendar, horizon, config, capital, cache
        )
        rows.append({"run": run, **metrics})
    return pd.DataFrame(rows)


def oracle_diagnostic(
    samples: pd.DataFrame,
    price_panel: pd.DataFrame,
    calendar: pd.DataFrame,
    horizon: int,
    config: dict[str, Any],
    capital: float,
    random_runs: int,
    stop_mode: str,
) -> dict[str, Any]:
    frame = samples.loc[
        samples["signal_date"].between(
            pd.Timestamp("2022-01-01"), pd.Timestamp("2024-12-31")
        )
    ].copy()
    frame["predicted_a"] = frame["actual_a"]
    frame["predicted_b"] = frame["actual_b"]
    frame["final_score"] = frame["actual_ratio"]
    frame["e1_stop_pct"] = e1_stop_values(frame, "actual_b", stop_mode)
    frame["e1_target_pct"] = TARGET_CAPTURE * frame["actual_a"]
    frame["planned_rr"] = frame["e1_target_pct"] / frame["e1_stop_pct"]
    frame["e1_feasible"] = (
        (frame["e1_stop_pct"] <= MAX_STOP)
        & (frame["planned_rr"] >= MIN_PLANNED_RR)
    )
    top1 = daily_top1(frame)
    signals = top1.loc[top1["e1_feasible"]]
    ids = {str(value) for value in signals["instrument_id"].unique()}
    views = build_stock_views(price_panel, ids)
    trades, metrics, dates = simulate_e1(
        signals, views, calendar, horizon, config, capital
    )
    eligible_pool = frame.loc[frame["e1_feasible"]]
    random = paired_random(
        eligible_pool,
        dates,
        ids,
        price_panel,
        calendar,
        horizon,
        config,
        capital,
        random_runs,
    )
    return {
        "metrics": metrics,
        "random_median": float(random["total_return"].median()),
        "random_p95": float(random["total_return"].quantile(0.95)),
        "trades": int(len(trades)),
    }


def feature_importance(
    model_a: lgb.LGBMRegressor,
    model_b: lgb.LGBMRegressor,
) -> pd.DataFrame:
    rows = []
    for name, model in (("PredA50", model_a), ("PredB75", model_b)):
        gain = model.booster_.feature_importance(importance_type="gain")
        total = float(gain.sum())
        for feature, value in zip(FEATURES, gain):
            rows.append(
                {
                    "model": name,
                    "feature": feature,
                    "gain_share": float(value / total) if total else 0.0,
                }
            )
    return pd.DataFrame(rows).sort_values(["model", "gain_share"], ascending=[True, False])


def pct(value: Any) -> str:
    if value is None or not np.isfinite(float(value)):
        return "NA"
    return f"{float(value):.2%}"


def num(value: Any, digits: int = 3) -> str:
    if value is None or not np.isfinite(float(value)):
        return "NA"
    return f"{float(value):.{digits}f}"


def write_report(path: Path, summary: dict[str, Any]) -> None:
    test = summary["quasi_oos_2025_plus"]
    run_label = "全量复核" if summary["version"].endswith("FULL") else "快速回测"
    stop_rule = (
        "clip(1.5×ATR20,4%,8%)"
        if summary["stop_mode"] == "atr_clipped"
        else "max(4%,PredB75,1.5×ATR20)，且不得超过8%"
    )
    lines = [
        f"# 严格a/b + E1单股票Top1{run_label}",
        "",
        "## 结论",
        "",
        f"**{summary['decision']}**",
        "",
        f"周期选择状态：{summary['selection']['status']}。",
        "",
        "## 2022—2024周期与阈值选择",
        "",
        "| H | 阈值 | 正收益年份 | 交易数 | 总收益 | 平均R | 盈利因子 | 随机中位数 | Oracle收益 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary["period_results"]:
        lines.append(
            f"| {row['horizon']} | {row['threshold']:.2f} | {row['positive_years']}/3 | "
            f"{int(row['trades'])} | {pct(row['total_return'])} | {num(row['average_r'])} | "
            f"{num(row['profit_factor'])} | {pct(row['random_return_median'])} | "
            f"{pct(row['oracle_total_return'])} |"
        )
    lines.extend(
        [
            "",
            "## 2025以后准样本外",
            "",
            f"- 诊断使用H={summary['selection']['horizon']}、FinalScore阈值={summary['selection']['threshold']:.2f}。",
            f"- 交易{int(test['portfolio']['trades'])}笔，总收益{pct(test['portfolio']['total_return'])}，平均R {num(test['portfolio']['average_r'])}。",
            f"- 盈利因子{num(test['portfolio']['profit_factor'])}，最大回撤{pct(test['portfolio']['max_drawdown'])}。",
            f"- Rank IC {num(test['diagnostics']['daily_rank_ic_mean'])}；Top1实际a/b均值{num(test['diagnostics']['top1_actual_ratio_mean'])}，候选池{num(test['diagnostics']['universe_actual_ratio_mean'])}。",
            f"- 配对随机中位数{pct(test['random_top1']['total_return_median'])}，策略随机百分位{pct(test['random_top1']['strategy_percentile'])}。",
            "",
            "## 规则",
            "",
            f"分别预测PredA50和PredB75，FinalScore=PredA50/max(PredB75,1%)。每日只取Top1。E1止损为{stop_rule}、止盈为0.8×PredA50，到H日整仓退出；全程单持仓、全进全出、不加仓、不减仓。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Strict a/b E1 Top1 backtest")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--start-year", type=int, default=2020)
    parser.add_argument("--random-runs", type=int, default=10)
    parser.add_argument("--capital", type=float, default=DEFAULT_CAPITAL)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--stop-mode", choices=STOP_MODES, default="predicted_b")
    parser.add_argument("--horizons", nargs="+", type=int, choices=HORIZONS, default=list(HORIZONS))
    parser.add_argument("--fixed-threshold", type=float, default=None)
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()
    if args.fixed_threshold is not None and args.fixed_threshold < 0:
        parser.error("--fixed-threshold must be non-negative")
    quick = not args.full
    horizons = tuple(dict.fromkeys(args.horizons))
    score_thresholds = (
        (float(args.fixed_threshold),)
        if args.fixed_threshold is not None
        else SCORE_THRESHOLDS
    )

    script_dir = Path(__file__).resolve().parent
    config = load_config(resolve_from(script_dir, args.config))
    data_root = resolve_from(script_dir, str(config["data_root"]))
    output_dir = (
        Path(args.output_dir).resolve()
        if args.output_dir
        else resolve_from(script_dir, str(config["output_dir"]))
        / ("ml_ab_e1_atr" if args.stop_mode == "atr_clipped" else "ml_ab_e1")
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    calendar_file = pd.read_parquet(data_root / "trading_calendar.parquet")
    end_year = int(pd.to_datetime(calendar_file["calendar_date"]).dt.year.max())
    del calendar_file
    panel, calendar, panel_audit = load_daily_panel(data_root, args.start_year, end_year)
    panel = add_daily_features(panel)
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
        panel["trade_date"] >= pd.Timestamp("2021-01-01"), price_columns
    ].copy()

    period_results = []
    threshold_rows = []
    fold_rows = []
    label_audits: dict[int, Any] = {}
    sample_audits: dict[int, Any] = {}
    oracle_audits: dict[int, Any] = {}
    validation_random_parts: list[pd.DataFrame] = []

    for horizon in horizons:
        panel, label_audit = add_ab_labels(panel, horizon)
        samples, sample_audit = build_samples(panel, calendar)
        label_audits[horizon] = label_audit
        sample_audits[horizon] = sample_audit
        oracle = oracle_diagnostic(
            samples,
            price_panel,
            calendar,
            horizon,
            config,
            float(args.capital),
            int(args.random_runs),
            args.stop_mode,
        )
        oracle_audits[horizon] = oracle
        scored_parts = []
        diagnostics = []
        for year in (2022, 2023, 2024):
            model_a, model_b, offsets, audit = fit_models(
                samples, year, horizon, quick
            )
            scored, diag = score_period(
                samples,
                model_a,
                model_b,
                offsets,
                pd.Timestamp(f"{year}-01-01"),
                pd.Timestamp(f"{year + 1}-01-01"),
                args.stop_mode,
            )
            scored_parts.append(scored)
            diagnostics.append(diag)
            fold_rows.append({"horizon": horizon, **audit, **diag})
            del model_a, model_b
            gc.collect()
        development = pd.concat(scored_parts, ignore_index=True)
        top1 = daily_top1(development)
        ids = {str(value) for value in top1["instrument_id"].unique()}
        views = build_stock_views(price_panel, ids)
        chosen, trades, candidates = choose_threshold(
            top1,
            views,
            calendar,
            horizon,
            config,
            float(args.capital),
            score_thresholds,
        )
        threshold_rows.extend(candidates)
        signals = top1.loc[
            top1["e1_feasible"]
            & (top1["final_score"] >= float(chosen["threshold"]))
        ]
        trades, metrics, dates = simulate_e1(
            signals, views, calendar, horizon, config, float(args.capital)
        )
        eligible_pool = development.loc[
            development["e1_feasible"]
            & (development["final_score"] >= float(chosen["threshold"]))
        ]
        random = paired_random(
            eligible_pool,
            dates,
            ids,
            price_panel,
            calendar,
            horizon,
            config,
            float(args.capital),
            int(args.random_runs),
        )
        random = random.copy()
        random.insert(0, "threshold", float(chosen["threshold"]))
        random.insert(0, "horizon", horizon)
        validation_random_parts.append(random)
        random_median = float(random["total_return"].median())
        result = {
            "horizon": horizon,
            "threshold": float(chosen["threshold"]),
            **metrics,
            "positive_years": int(chosen["positive_years"]),
            "median_year_return": float(chosen["median_year_return"]),
            "rank_ic_mean": float(np.mean([item["daily_rank_ic_mean"] for item in diagnostics])),
            "top1_ratio_beats_universe_years": int(
                sum(item["top1_actual_ratio_mean"] > item["universe_actual_ratio_mean"] for item in diagnostics)
            ),
            "random_return_median": random_median,
            "random_return_p05": float(random["total_return"].quantile(0.05)),
            "random_return_p25": float(random["total_return"].quantile(0.25)),
            "random_return_p75": float(random["total_return"].quantile(0.75)),
            "random_return_p95": float(random["total_return"].quantile(0.95)),
            "strategy_random_percentile": float(
                (random["total_return"] < metrics["total_return"]).mean()
            ),
            "beats_random_median": float(metrics["total_return"]) > random_median,
            "oracle_total_return": float(oracle["metrics"]["total_return"]),
            "oracle_random_median": float(oracle["random_median"]),
        }
        period_results.append(result)
        top1.assign(horizon=horizon).to_csv(
            output_dir / f"validation_top1_h{horizon}.csv", index=False, encoding="utf-8-sig"
        )
        trades.to_csv(
            output_dir / f"validation_trades_h{horizon}.csv", index=False, encoding="utf-8-sig"
        )
        log(
            f"H={horizon}: threshold={result['threshold']:.2f}, "
            f"return={result['total_return']:.2%}, random={random_median:.2%}, "
            f"oracle={result['oracle_total_return']:.2%}"
        )
        del samples, development, top1, views, random
        gc.collect()

    eligible_periods = [row for row in period_results if int(row["trades"]) >= 40]
    selection_is_diagnostic = not bool(eligible_periods)
    selection_pool = eligible_periods
    if selection_is_diagnostic:
        # 零交易方案不能因“收益为0”胜过少量亏损方案。若所有周期均未达到
        # 预注册的40笔门槛，只选有成交的周期做诊断，并明确标记为未选出正式周期。
        selection_pool = [row for row in period_results if int(row["trades"]) > 0]
        if not selection_pool:
            selection_pool = period_results
    selected = max(
        selection_pool,
        key=lambda row: (
            int(row["positive_years"]),
            int(bool(row["beats_random_median"])),
            int(row["top1_ratio_beats_universe_years"]),
            int(row["trades"]),
            float(row["median_year_return"]),
            float(row["total_return"]),
            -int(row["horizon"]),
        ),
    )
    selected_horizon = int(selected["horizon"])
    selected_threshold = float(selected["threshold"])
    log(f"Selected strict a/b design: {selected}")

    panel, final_label_audit = add_ab_labels(panel, selected_horizon)
    samples, final_sample_audit = build_samples(panel, calendar)
    final_a, final_b, final_offsets, final_fit = fit_models(
        samples, 2025, selected_horizon, quick
    )
    test_scored, test_diag = score_period(
        samples,
        final_a,
        final_b,
        final_offsets,
        pd.Timestamp("2025-01-01"),
        None,
        args.stop_mode,
    )
    test_top1 = daily_top1(test_scored)
    test_signals = test_top1.loc[
        test_top1["e1_feasible"] & (test_top1["final_score"] >= selected_threshold)
    ]
    test_ids = {str(value) for value in test_top1["instrument_id"].unique()}
    test_views = build_stock_views(price_panel, test_ids)
    test_trades, test_metrics, test_dates = simulate_e1(
        test_signals,
        test_views,
        calendar,
        selected_horizon,
        config,
        float(args.capital),
    )
    test_pool = test_scored.loc[
        test_scored["e1_feasible"] & (test_scored["final_score"] >= selected_threshold)
    ]
    test_random = paired_random(
        test_pool,
        test_dates,
        test_ids,
        price_panel,
        calendar,
        selected_horizon,
        config,
        float(args.capital),
        int(args.random_runs),
    )
    random_median = float(test_random["total_return"].median())
    percentile = float((test_random["total_return"] < test_metrics["total_return"]).mean())
    checks = {
        "development_positive_years_at_least_2": int(selected["positive_years"]) >= 2,
        "development_rank_ic_at_least_0_03": float(selected["rank_ic_mean"]) >= 0.03,
        "development_top1_ratio_beats_universe_at_least_2_years": int(selected["top1_ratio_beats_universe_years"]) >= 2,
        "development_trades_at_least_40": int(selected["trades"]) >= 40,
        "development_average_r_above_0_15": float(selected["average_r"] or math.nan) > 0.15,
        "development_profit_factor_above_1_20": float(selected["profit_factor"] or math.nan) > 1.20,
        "development_max_drawdown_better_than_minus_15pct": float(selected["max_drawdown"]) > -0.15,
        "development_return_beats_random_median": bool(selected["beats_random_median"]),
        "strict_single_position": int(test_metrics["max_concurrent_positions"]) <= 1,
    }
    passed = int(sum(bool(value) for value in checks.values()))
    if selection_is_diagnostic:
        decision = "严格a/b+E1未选出满足最低样本量的周期，当前快速版不可用"
    elif passed >= 8 and int(test_metrics["trades"]) >= 20:
        decision = "严格a/b+E1快速版有全量回测价值，但仍不可直接模拟盘或实盘"
    elif passed >= 6:
        decision = (
            "严格a/b+E1全量复核仅有弱研究价值，未通过冻结验收"
            if not quick
            else "严格a/b+E1只有弱研究价值，不建议立即全量训练"
        )
    else:
        decision = "严格a/b+E1快速版未显示可用优势"

    pd.DataFrame(period_results).to_csv(
        output_dir / "period_summary.csv", index=False, encoding="utf-8-sig"
    )
    pd.DataFrame(threshold_rows).to_csv(
        output_dir / "threshold_summary.csv", index=False, encoding="utf-8-sig"
    )
    pd.DataFrame(fold_rows).to_csv(
        output_dir / "walk_forward_folds.csv", index=False, encoding="utf-8-sig"
    )
    pd.concat(validation_random_parts, ignore_index=True).to_csv(
        output_dir / "validation_random_top1_runs.csv", index=False, encoding="utf-8-sig"
    )
    test_top1.to_csv(output_dir / "test_top1.csv", index=False, encoding="utf-8-sig")
    test_trades.to_csv(output_dir / "test_trades.csv", index=False, encoding="utf-8-sig")
    test_random.to_csv(output_dir / "random_top1_runs.csv", index=False, encoding="utf-8-sig")
    feature_importance(final_a, final_b).to_csv(
        output_dir / "feature_importance.csv", index=False, encoding="utf-8-sig"
    )
    joblib.dump(
        {
            "version": (
                f"STRICT_AB_E1_{'ATR_' if args.stop_mode == 'atr_clipped' else ''}{'QUICK' if quick else 'FULL'}"
            ),
            "horizon": selected_horizon,
            "score_threshold": selected_threshold,
            "features": FEATURES,
            "model_a50": final_a,
            "model_b75": final_b,
            "offsets": final_offsets,
            "e1": {
                "min_stop": MIN_STOP,
                "max_stop": MAX_STOP,
                "atr_multiplier": ATR_STOP_MULTIPLIER,
                "target_capture": TARGET_CAPTURE,
                "min_planned_rr": MIN_PLANNED_RR,
                "stop_mode": args.stop_mode,
            },
        },
        output_dir / "strict_ab_e1_bundle.joblib",
        compress=3,
    )
    summary = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "version": (
            f"STRICT_AB_E1_{'ATR_' if args.stop_mode == 'atr_clipped' else ''}{'QUICK' if quick else 'FULL'}"
        ),
        "stop_mode": args.stop_mode,
        "run_configuration": {
            "quick_training_date_sampling": quick,
            "horizons": list(horizons),
            "fixed_threshold": args.fixed_threshold,
        },
        "decision": decision,
        "initial_capital": float(args.capital),
        "random_runs": int(args.random_runs),
        "selection": {
            "horizon": selected_horizon,
            "threshold": selected_threshold,
            "status": (
                "未选出满足开发期至少40笔的正式周期；H仅为诊断回退"
                if selection_is_diagnostic
                else "通过最低样本量筛选的开发期选择"
            ),
        },
        "period_results": period_results,
        "quasi_oos_2025_plus": {
            "diagnostics": test_diag,
            "portfolio": test_metrics,
            "random_top1": {
                "runs": int(len(test_random)),
                "total_return_p05": float(test_random["total_return"].quantile(0.05)),
                "total_return_median": random_median,
                "total_return_p95": float(test_random["total_return"].quantile(0.95)),
                "strategy_percentile": percentile,
            },
        },
        "assessment": {"checks": checks, "passed": passed, "total": len(checks)},
        "panel_audit": panel_audit,
        "label_audits": label_audits,
        "sample_audits": sample_audits,
        "oracle_audits": oracle_audits,
        "final_label_audit": final_label_audit,
        "final_sample_audit": final_sample_audit,
        "final_fit_audit": final_fit,
    }
    write_json(output_dir / "summary.json", summary)
    write_report(output_dir / "report.md", summary)
    log(f"Decision: {decision}")
    log(f"Strict a/b outputs: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
