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

from backtest_mfe_mae import build_stock_views, load_config, resolve_from, row_at
from ml_cprr_v2 import add_daily_features, load_daily_panel


HORIZONS = (20, 30, 40, 60)
COST_BUFFER = 0.0035
CPRR_CAP = 10.0
ENTRY_GAP_MAX = 0.03
PROTECTIVE_STOP = 0.06
TRAIL_START = 0.08
TRAIL_DRAWDOWN = 0.05
RISK_PER_TRADE = 0.01
MAX_POSITION_WEIGHT = 0.25
DEFAULT_CAPITAL = 200_000.0
SEED = 20260816
THRESHOLDS = (0.0, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0)

# Deliberately exclude V2's market_ret20_median and market_breadth20.  The
# target is a same-day cross-sectional ranking, not a market-timing model.
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


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(json_value(value), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def add_horizon_labels(
    panel: pd.DataFrame,
    horizon: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Add continuous-calendar, T+1 executable path labels.

    Labels are calculated before the liquidity filter.  An unbuyable T+1 row
    remains in the ranking universe, but is excluded from model fitting.  This
    prevents an unbuyable predicted Top1 from silently becoming Top2.
    """

    log(f"Building H={horizon} executable labels")
    count = len(panel)
    mfe = np.full(count, np.nan, dtype="float32")
    mae_pre = np.full(count, np.nan, dtype="float32")
    terminal = np.full(count, np.nan, dtype="float32")
    label_end_idx = np.full(count, -1, dtype="int32")
    peak_offset = np.full(count, -1, dtype="int16")
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
        adj_high = panel.loc[pos, "adj_high"].to_numpy(dtype=float)
        adj_low = panel.loc[pos, "adj_low"].to_numpy(dtype=float)
        adj_open = panel.loc[pos, "adj_open"].to_numpy(dtype=float)
        adj_close = panel.loc[pos, "adj_close"].to_numpy(dtype=float)
        raw_open = panel.loc[pos, "open"].to_numpy(dtype=float)
        raw_close = panel.loc[pos, "close"].to_numpy(dtype=float)
        trade_idx = panel.loc[pos, "trade_idx"].to_numpy(dtype="int32")
        limit_status = panel.loc[pos, "limit_status"].to_numpy(dtype="int8")

        candidates = length - (horizon + 1)
        signal_rows = pos[:candidates]
        entry = adj_open[1 : candidates + 1]
        high_windows = sliding_window_view(adj_high[2:], horizon)
        low_windows = sliding_window_view(adj_low[1:], horizon + 1)
        peak_positions = np.argmax(high_windows, axis=1)
        row_numbers = np.arange(candidates)
        peak_high = high_windows[row_numbers, peak_positions]
        cumulative_low = np.minimum.accumulate(low_windows, axis=1)
        pre_peak_low = cumulative_low[row_numbers, peak_positions + 1]
        end_close = adj_close[horizon + 1 :]
        end_indices = trade_idx[horizon + 1 :]
        gaps = raw_open[1 : candidates + 1] / raw_close[:candidates] - 1.0

        continuous = end_indices - trade_idx[:candidates] == horizon + 1
        finite = (
            np.isfinite(entry)
            & (entry > 0)
            & np.isfinite(peak_high)
            & np.isfinite(pre_peak_low)
            & np.isfinite(end_close)
            & np.isfinite(gaps)
        )
        valid = continuous & finite
        buyable = limit_status[1 : candidates + 1] != 3
        acceptable_gap = gaps <= ENTRY_GAP_MAX
        executable = valid & buyable & acceptable_gap
        incomplete += int((~continuous | ~finite).sum())
        unbuyable += int((valid & ~buyable).sum())
        high_gap += int((valid & buyable & ~acceptable_gap).sum())

        valid_rows = signal_rows[valid]
        mfe[valid_rows] = np.maximum(
            peak_high[valid] / entry[valid] - 1.0, 0.0
        ).astype("float32")
        mae_pre[valid_rows] = np.maximum(
            1.0 - pre_peak_low[valid] / entry[valid], 0.0
        ).astype("float32")
        terminal[valid_rows] = (
            end_close[valid] / entry[valid] - 1.0 - COST_BUFFER
        ).astype("float32")
        label_end_idx[valid_rows] = end_indices[valid]
        peak_offset[valid_rows] = (peak_positions[valid] + 1).astype("int16")
        entry_gap[signal_rows[finite]] = gaps[finite].astype("float32")
        entry_executable[signal_rows[executable]] = True

        if number % 800 == 0 or number == len(groups):
            log(f"H={horizon} label progress {number:,}/{len(groups):,}")

    panel["mfe_exec"] = mfe
    panel["mae_pre_exec"] = mae_pre
    panel["terminal_net_return"] = terminal
    panel["label_end_idx"] = label_end_idx
    panel["peak_offset_sessions"] = peak_offset
    panel["entry_gap"] = entry_gap
    panel["entry_executable"] = entry_executable
    reward = np.maximum(panel["mfe_exec"] - COST_BUFFER, 0.0)
    risk = np.maximum.reduce(
        [
            (panel["mae_pre_exec"] + COST_BUFFER).to_numpy(dtype=float),
            (0.5 * panel["atr20_pct"]).to_numpy(dtype=float),
            np.full(len(panel), 0.01),
        ]
    )
    panel["actual_cprr"] = np.minimum(reward / risk, CPRR_CAP).astype(
        "float32"
    )
    return panel, {
        "horizon": horizon,
        "valid_label_rows": int(panel["mfe_exec"].notna().sum()),
        "entry_executable_rows": int(entry_executable.sum()),
        "incomplete_or_noncontinuous": incomplete,
        "unbuyable_t1": unbuyable,
        "entry_gap_over_3pct": high_gap,
        "definition": (
            f"T close signal; T+1 open entry; next {horizon} sellable sessions; "
            "MFE minus 0.35% cost divided by max(MAE_pre+cost, 0.5*ATR20, 1%)"
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
        & panel["actual_cprr"].notna()
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
        "mfe_exec",
        "mae_pre_exec",
        "terminal_net_return",
        "actual_cprr",
        "label_end_idx",
        "peak_offset_sessions",
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
    }


def date_weights(frame: pd.DataFrame) -> np.ndarray:
    counts = frame.groupby("signal_date")["instrument_id"].transform("size")
    return (1.0 / counts).to_numpy(dtype=float)


def relevance(frame: pd.DataFrame) -> np.ndarray:
    pct_rank = frame.groupby("signal_date")["actual_cprr"].rank(
        pct=True, method="average"
    )
    return np.minimum(np.floor(pct_rank.to_numpy() * 8.0), 7).astype("int8")


def ranker_params(quick: bool) -> dict[str, Any]:
    return {
        "objective": "lambdarank",
        "metric": "ndcg",
        "n_estimators": 450 if quick else 1200,
        "learning_rate": 0.05 if quick else 0.03,
        "num_leaves": 31,
        "max_depth": 7,
        "min_child_samples": 150,
        "subsample": 0.80,
        "subsample_freq": 1,
        "colsample_bytree": 0.85,
        "reg_alpha": 0.10,
        "reg_lambda": 1.0,
        "lambdarank_truncation_level": 10,
        "random_state": SEED,
        "n_jobs": -1,
        "verbosity": -1,
    }


def quick_date_sample(frame: pd.DataFrame, stride: int) -> pd.DataFrame:
    if stride <= 1:
        return frame
    dates = frame["signal_date"].drop_duplicates().to_numpy()[::stride]
    return frame.loc[frame["signal_date"].isin(dates)].copy()


def fit_ranker(
    samples: pd.DataFrame,
    validation_year: int,
    horizon: int,
    quick: bool,
) -> tuple[lgb.LGBMRanker, IsotonicRegression, dict[str, Any]]:
    validation_start = pd.Timestamp(f"{validation_year}-01-01")
    # Six calendar months leave a usable mature-label calibration block even
    # for H=60, while label_end_date still prevents boundary leakage.
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
    train = quick_date_sample(train, 5 if quick else 1)
    train = train.sort_values(["signal_date", "instrument_id"]).reset_index(
        drop=True
    )
    calibration = calibration.sort_values(
        ["signal_date", "instrument_id"]
    ).reset_index(drop=True)
    if train.empty or calibration.empty:
        raise ValueError(f"Empty train/calibration for validation {validation_year}")

    train_y = relevance(train)
    calibration_y = relevance(calibration)
    train_group = train.groupby("signal_date", sort=False).size().to_numpy()
    calibration_group = (
        calibration.groupby("signal_date", sort=False).size().to_numpy()
    )
    log(
        f"Fit ranker for {validation_year}: train={len(train):,} "
        f"({len(train_group)} dates), calibration={len(calibration):,}"
    )
    model = lgb.LGBMRanker(**ranker_params(quick))
    model.fit(
        train[FEATURES],
        train_y,
        group=train_group,
        eval_set=[(calibration[FEATURES], calibration_y)],
        eval_group=[calibration_group],
        eval_at=[1, 3, 5, 10],
        callbacks=[lgb.early_stopping(50, verbose=False)],
    )
    calibration_score = model.predict(
        calibration[FEATURES], num_iteration=model.best_iteration_
    )
    calibrator = IsotonicRegression(
        y_min=0.0,
        y_max=CPRR_CAP,
        increasing=True,
        out_of_bounds="clip",
    )
    if np.unique(calibration_score).size < 2:
        constant = float(
            np.average(
                calibration["actual_cprr"],
                weights=date_weights(calibration),
            )
        )
        calibrator.fit([0.0, 1.0], [constant, constant])
    else:
        calibrator.fit(
            calibration_score,
            calibration["actual_cprr"],
            sample_weight=date_weights(calibration),
        )
    return model, calibrator, {
        "validation_year": validation_year,
        "train_rows": int(len(train)),
        "train_dates": int(len(train_group)),
        "calibration_rows": int(len(calibration)),
        "calibration_dates": int(len(calibration_group)),
        "best_iteration": int(model.best_iteration_ or model.n_estimators),
    }


def safe_spearman(x: pd.Series, y: pd.Series) -> float:
    if len(x) < 3 or x.nunique() < 2 or y.nunique() < 2:
        return math.nan
    return float(x.corr(y, method="spearman"))


def score_period(
    samples: pd.DataFrame,
    model: lgb.LGBMRanker,
    calibrator: IsotonicRegression,
    start: pd.Timestamp,
    end: pd.Timestamp | None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    mask = samples["signal_date"] >= start
    if end is not None:
        mask &= samples["signal_date"] < end
    frame = samples.loc[mask].copy()
    score = model.predict(frame[FEATURES], num_iteration=model.best_iteration_)
    frame["rank_score"] = score
    frame["predicted_cprr"] = calibrator.predict(score)
    daily_ic = []
    for _, group in frame.groupby("signal_date", sort=False):
        value = safe_spearman(group["rank_score"], group["actual_cprr"])
        if np.isfinite(value):
            daily_ic.append(value)
    ordered = frame.sort_values(
        ["signal_date", "rank_score", "instrument_id"],
        ascending=[True, False, True],
    )
    top1 = ordered.groupby("signal_date", sort=False).head(1).copy()
    keep = [
        "instrument_id",
        "signal_date",
        "trade_idx",
        "open",
        "atr20_pct",
        "entry_gap",
        "entry_executable",
        "mfe_exec",
        "mae_pre_exec",
        "terminal_net_return",
        "actual_cprr",
        "rank_score",
        "predicted_cprr",
    ]
    top1 = top1[keep].sort_values("signal_date").reset_index(drop=True)
    return top1, {
        "rows": int(len(frame)),
        "signal_dates": int(frame["signal_date"].nunique()),
        "daily_rank_ic_mean": float(np.mean(daily_ic)) if daily_ic else math.nan,
        "daily_rank_ic_median": float(np.median(daily_ic)) if daily_ic else math.nan,
        "top1_actual_cprr_mean": float(top1["actual_cprr"].mean()),
        "top1_actual_cprr_median": float(top1["actual_cprr"].median()),
        "universe_actual_cprr_mean": float(frame["actual_cprr"].mean()),
        "universe_actual_cprr_median": float(frame["actual_cprr"].median()),
        "top1_terminal_mean": float(top1["terminal_net_return"].mean()),
        "top1_terminal_median": float(top1["terminal_net_return"].median()),
        "universe_terminal_mean": float(frame["terminal_net_return"].mean()),
        "universe_terminal_median": float(frame["terminal_net_return"].median()),
    }


def trade_outcome(
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
    if not np.isfinite(entry_scale) or entry_scale <= 0:
        return None, "INVALID_ENTRY_PRICE"

    stop_scale = entry_scale * (1.0 - PROTECTIVE_STOP)
    peak_scale = float(entry["adj_high"])
    pending_entry_stop = float(entry["adj_low"]) <= stop_scale
    exit_idx: int | None = None
    exit_scale: float | None = None
    exit_timing = ""
    exit_reason = ""

    for current_idx in range(entry_idx + 1, end_idx + 1):
        current = row_at(stock, current_idx)
        if current is None:
            continue
        limit_status = int(current["limit_status"])
        sellable = limit_status != 6
        current_open = float(current["adj_open"])
        current_high = float(current["adj_high"])
        current_low = float(current["adj_low"])
        trail_active = peak_scale >= entry_scale * (1.0 + TRAIL_START)
        trail_scale = peak_scale * (1.0 - TRAIL_DRAWDOWN)

        if pending_entry_stop and sellable:
            exit_idx, exit_scale = current_idx, current_open
            exit_timing, exit_reason = "OPEN", "ENTRY_DAY_STOP_NEXT_OPEN"
            break
        if sellable and current_open <= stop_scale:
            exit_idx, exit_scale = current_idx, current_open
            exit_timing, exit_reason = "OPEN", "GAP_PROTECTIVE_STOP"
            break
        if sellable and trail_active and current_open <= trail_scale:
            exit_idx, exit_scale = current_idx, current_open
            exit_timing, exit_reason = "OPEN", "GAP_TRAILING_EXIT"
            break
        if sellable and current_low <= stop_scale:
            exit_idx, exit_scale = current_idx, stop_scale
            exit_timing, exit_reason = "INTRADAY", "PROTECTIVE_STOP"
            break
        if sellable and trail_active and current_low <= trail_scale:
            exit_idx, exit_scale = current_idx, trail_scale
            exit_timing, exit_reason = "INTRADAY", "TRAILING_EXIT"
            break
        peak_scale = max(peak_scale, current_high)

    if exit_idx is None:
        current = row_at(stock, end_idx)
        if current is not None and int(current["limit_status"]) != 6:
            exit_idx = end_idx
            exit_scale = float(current["adj_close"])
            exit_timing, exit_reason = "CLOSE", "TIME_EXIT"
        else:
            for later_idx in stock.index[stock.index > end_idx]:
                later = row_at(stock, int(later_idx))
                if later is not None and int(later["limit_status"]) != 6:
                    exit_idx = int(later_idx)
                    exit_scale = float(later["adj_open"])
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
        "stop_pct": PROTECTIVE_STOP,
        "atr20_pct": float(candidate.atr20_pct),
        "predicted_cprr": float(getattr(candidate, "predicted_cprr", math.nan)),
        "actual_cprr": float(candidate.actual_cprr),
        "horizon": horizon,
    }, "FILLED"


def empty_metrics(initial_capital: float) -> dict[str, Any]:
    return {
        "trades": 0,
        "total_return": 0.0,
        "cagr": 0.0,
        "average_r": math.nan,
        "profit_factor": math.nan,
        "win_rate": math.nan,
        "max_drawdown": 0.0,
        "final_equity": initial_capital,
        "skipped_holding_signals": 0,
        "cancelled_entries": 0,
        "max_concurrent_positions": 0,
    }


def simulate_single_position(
    signals: pd.DataFrame,
    stock_views: dict[str, pd.DataFrame],
    calendar: pd.DataFrame,
    horizon: int,
    config: dict[str, Any],
    initial_capital: float,
    cache: dict[tuple[str, int, int], tuple[dict[str, Any] | None, str]] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any], list[pd.Timestamp]]:
    if signals.empty:
        return pd.DataFrame(), empty_metrics(initial_capital), []
    cache = {} if cache is None else cache
    calendar_dates = pd.to_datetime(calendar["calendar_date"]).tolist()
    equity = initial_capital
    equity_marks = [initial_capital]
    trades: list[dict[str, Any]] = []
    considered_dates: list[pd.Timestamp] = []
    busy_until = -1
    skipped = 0
    cancellations = 0
    cancellation_reasons: dict[str, int] = {}
    buy_cost_rate = float(config["buy_cost_rate"])
    sell_cost_rate = float(config["sell_cost_rate"])

    for candidate in signals.sort_values("signal_date").itertuples(index=False):
        signal_idx = int(candidate.trade_idx)
        if signal_idx < busy_until:
            skipped += 1
            continue
        considered_dates.append(pd.Timestamp(candidate.signal_date))
        key = (str(candidate.instrument_id), signal_idx, horizon)
        if key not in cache:
            cache[key] = trade_outcome(
                candidate, stock_views, calendar_dates, horizon
            )
        outcome, reason = cache[key]
        if outcome is None:
            cancellations += 1
            cancellation_reasons[reason] = cancellation_reasons.get(reason, 0) + 1
            continue

        gap_buffer = max(0.5 * float(outcome["atr20_pct"]), 0.01)
        effective_risk = PROTECTIVE_STOP + gap_buffer
        target_notional = min(
            equity * MAX_POSITION_WEIGHT,
            equity * RISK_PER_TRADE / effective_risk,
        )
        affordable = equity / (1.0 + buy_cost_rate)
        target_notional = min(target_notional, affordable)
        shares = math.floor(
            target_notional / float(outcome["entry_raw_price"]) / 100.0
        ) * 100
        if shares < 100:
            cancellations += 1
            cancellation_reasons["ROUND_LOT_TOO_SMALL"] = (
                cancellation_reasons.get("ROUND_LOT_TOO_SMALL", 0) + 1
            )
            continue
        notional = shares * float(outcome["entry_raw_price"])
        buy_cost = notional * buy_cost_rate
        sell_proceeds = (
            notional
            * float(outcome["exit_scale"])
            / float(outcome["entry_scale"])
            * (1.0 - sell_cost_rate)
        )
        pnl = sell_proceeds - notional - buy_cost
        net_return = pnl / notional
        equity += pnl
        equity_marks.append(equity)
        trades.append(
            {
                **outcome,
                "shares": shares,
                "notional_cny": notional,
                "buy_cost_cny": buy_cost,
                "sell_proceeds_cny": sell_proceeds,
                "pnl_cny": pnl,
                "net_return": net_return,
                "net_r": net_return / PROTECTIVE_STOP,
                "equity_after": equity,
            }
        )
        busy_until = int(outcome["exit_idx"])

    trade_frame = pd.DataFrame(trades)
    if trade_frame.empty:
        metrics = empty_metrics(initial_capital)
    else:
        returns = trade_frame["net_return"]
        gains = float(trade_frame.loc[trade_frame["pnl_cny"] > 0, "pnl_cny"].sum())
        losses = float(-trade_frame.loc[trade_frame["pnl_cny"] < 0, "pnl_cny"].sum())
        marks = pd.Series(equity_marks, dtype=float)
        drawdown = marks / marks.cummax() - 1.0
        elapsed_days = max(
            (pd.Timestamp(trade_frame["exit_date"].iloc[-1])
             - pd.Timestamp(trade_frame["entry_date"].iloc[0])).days,
            1,
        )
        total_return = equity / initial_capital - 1.0
        metrics = {
            "trades": int(len(trade_frame)),
            "total_return": total_return,
            "cagr": (1.0 + total_return) ** (365.25 / elapsed_days) - 1.0,
            "average_r": float(trade_frame["net_r"].mean()),
            "profit_factor": gains / losses if losses > 0 else math.inf,
            "win_rate": float((returns > 0).mean()),
            "max_drawdown": float(drawdown.min()),
            "final_equity": equity,
            "skipped_holding_signals": skipped,
            "cancelled_entries": cancellations,
            "cancellation_reasons": cancellation_reasons,
            "max_concurrent_positions": 1,
        }
    metrics["skipped_holding_signals"] = skipped
    metrics["cancelled_entries"] = cancellations
    metrics["cancellation_reasons"] = cancellation_reasons
    metrics["decision_dates"] = int(len(considered_dates))
    return trade_frame, metrics, considered_dates


def random_signal_frames(
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
    frames = []
    for run in range(runs):
        rng = np.random.default_rng(SEED + 1000 + run)
        chosen = []
        for date in sorted(wanted):
            indices = groups.get(pd.Timestamp(date))
            if indices is not None and len(indices):
                chosen.append(int(rng.choice(indices)))
        frame = reduced.loc[chosen].copy() if chosen else reduced.head(0).copy()
        frame["predicted_cprr"] = math.nan
        frames.append(frame)
    return frames


def choose_threshold(
    top1: pd.DataFrame,
    stock_views: dict[str, pd.DataFrame],
    calendar: pd.DataFrame,
    horizon: int,
    config: dict[str, Any],
    initial_capital: float,
    cache: dict[tuple[str, int, int], tuple[dict[str, Any] | None, str]],
) -> tuple[float, pd.DataFrame, list[dict[str, Any]]]:
    rows = []
    trade_frames: dict[float, pd.DataFrame] = {}
    base_trades = 0
    for threshold in THRESHOLDS:
        signals = top1.loc[top1["predicted_cprr"] >= threshold]
        trades, metrics, _ = simulate_single_position(
            signals,
            stock_views,
            calendar,
            horizon,
            config,
            initial_capital,
            cache,
        )
        if threshold == 0.0:
            base_trades = int(metrics["trades"])
        yearly_returns = []
        positive_years = 0
        for year in (2022, 2023, 2024):
            year_signals = signals.loc[signals["signal_date"].dt.year == year]
            _, year_metrics, _ = simulate_single_position(
                year_signals,
                stock_views,
                calendar,
                horizon,
                config,
                initial_capital,
                cache,
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
        trade_frames[threshold] = trades
    minimum_trades = max(12, int(math.ceil(base_trades * 0.50)))
    eligible = [row for row in rows if int(row["trades"]) >= minimum_trades]
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
    threshold = float(best["threshold"])
    return threshold, trade_frames[threshold], rows


def paired_random_baseline(
    pool: pd.DataFrame,
    decision_dates: list[pd.Timestamp],
    model_ids: set[str],
    price_panel: pd.DataFrame,
    calendar: pd.DataFrame,
    horizon: int,
    config: dict[str, Any],
    initial_capital: float,
    runs: int,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    frames = random_signal_frames(pool, decision_dates, runs)
    random_ids = {
        str(value)
        for frame in frames
        for value in frame["instrument_id"].unique()
    }
    stock_views = build_stock_views(price_panel, model_ids | random_ids)
    cache: dict[tuple[str, int, int], tuple[dict[str, Any] | None, str]] = {}
    records = []
    for run, frame in enumerate(frames):
        _, metrics, _ = simulate_single_position(
            frame,
            stock_views,
            calendar,
            horizon,
            config,
            initial_capital,
            cache,
        )
        records.append({"run": run, **metrics})
    return pd.DataFrame(records), stock_views


def feature_importance(model: lgb.LGBMRanker) -> pd.DataFrame:
    gain = model.booster_.feature_importance(importance_type="gain")
    total = float(gain.sum())
    return pd.DataFrame(
        {
            "feature": FEATURES,
            "gain_share": gain / total if total else np.zeros(len(gain)),
        }
    ).sort_values("gain_share", ascending=False)


def pct(value: Any) -> str:
    if value is None or not np.isfinite(float(value)):
        return "NA"
    return f"{float(value):.2%}"


def num(value: Any, digits: int = 3) -> str:
    if value is None or not np.isfinite(float(value)):
        return "NA"
    return f"{float(value):.{digits}f}"


def write_report(path: Path, summary: dict[str, Any]) -> None:
    selected = summary["selection"]
    test = summary["quasi_oos_2025_plus"]
    random = test["random_top1"]
    rows = summary["period_results"]
    lines = [
        "# FinalCPRR V3 单股票Top1快速回测",
        "",
        "## 结论",
        "",
        f"开发集选择 H={selected['horizon']}，预测CPRR入场阈值={selected['threshold']:.2f}。",
        f"**{summary['decision']}**",
        "",
        "## 2022—2024周期选择（仅开发）",
        "",
        "| H | 阈值 | 正收益年份 | 交易数 | 总收益 | 盈利因子 | 最大回撤 | 随机中位数 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['horizon']} | {row['threshold']:.2f} | {row['positive_years']}/3 | "
            f"{int(row['trades'])} | {pct(row['total_return'])} | "
            f"{num(row['profit_factor'])} | {pct(row['max_drawdown'])} | "
            f"{pct(row['random_return_median'])} |"
        )
    lines.extend(
        [
            "",
            "## 2025以后准样本外结果",
            "",
            f"- 完成交易：{int(test['portfolio']['trades'])}笔。",
            f"- 总收益：{pct(test['portfolio']['total_return'])}；平均R：{num(test['portfolio']['average_r'])}。",
            f"- 盈利因子：{num(test['portfolio']['profit_factor'])}；最大回撤：{pct(test['portfolio']['max_drawdown'])}。",
            f"- 配对随机Top1中位数：{pct(random['total_return_median'])}；策略位于随机第{pct(random['strategy_percentile'])}分位。",
            f"- 日截面Rank IC均值：{num(test['diagnostics']['daily_rank_ic_mean'])}。",
            "",
            "## 交易约束",
            "",
            "同一时间最多持有一只；空仓时只接受当日Top1；一次买入目标股数，退出一次全部卖清；不加仓、不补仓、不减仓、不换股。T+1一字涨停或高开超过3%取消且不使用第2名。固定6%保护止损，浮盈8%后使用5%回撤移动退出，到H日整仓退出；一字跌停时顺延。",
            "",
            "## 解释边界",
            "",
            "2022—2024同时承担周期和阈值选择，因此只是开发结果。2025—2026此前已被研究，只能称准样本外。真正验收仍需2026-08-11以后新增数据。本快速版训练日按每5个交易日抽取一次；若结果有明显优势，才值得运行全量版。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="FinalCPRR V3 single-position Top1 walk-forward backtest"
    )
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--start-year", type=int, default=2020)
    parser.add_argument("--random-runs", type=int, default=10)
    parser.add_argument("--capital", type=float, default=DEFAULT_CAPITAL)
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Optional runtime output directory (useful when a cloud drive is read-only)",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Use every training date instead of the quick every-fifth-date sample",
    )
    args = parser.parse_args()
    quick = not args.full

    script_dir = Path(__file__).resolve().parent
    config = load_config(resolve_from(script_dir, args.config))
    data_root = resolve_from(script_dir, str(config["data_root"]))
    output_dir = (
        Path(args.output_dir).resolve()
        if args.output_dir
        else resolve_from(script_dir, str(config["output_dir"])) / "ml_v3"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    calendar_file = pd.read_parquet(data_root / "trading_calendar.parquet")
    end_year = int(pd.to_datetime(calendar_file["calendar_date"]).dt.year.max())
    del calendar_file
    panel, calendar, panel_audit = load_daily_panel(
        data_root, args.start_year, end_year
    )
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

    period_results: list[dict[str, Any]] = []
    threshold_rows: list[dict[str, Any]] = []
    fold_rows: list[dict[str, Any]] = []
    label_audits: dict[int, Any] = {}
    sample_audits: dict[int, Any] = {}

    for horizon in HORIZONS:
        panel, label_audit = add_horizon_labels(panel, horizon)
        samples, sample_audit = build_samples(panel, calendar)
        label_audits[horizon] = label_audit
        sample_audits[horizon] = sample_audit
        top1_parts = []
        diagnostics = []
        for year in (2022, 2023, 2024):
            model, calibrator, fit_audit = fit_ranker(
                samples, year, horizon, quick
            )
            top1, diag = score_period(
                samples,
                model,
                calibrator,
                pd.Timestamp(f"{year}-01-01"),
                pd.Timestamp(f"{year + 1}-01-01"),
            )
            top1_parts.append(top1)
            diagnostics.append({"horizon": horizon, "year": year, **diag})
            fold_rows.append({"horizon": horizon, **fit_audit, **diag})
            del model, calibrator
            gc.collect()
        validation_top1 = pd.concat(top1_parts, ignore_index=True)
        model_ids = {str(value) for value in validation_top1["instrument_id"].unique()}
        model_views = build_stock_views(price_panel, model_ids)
        cache: dict[tuple[str, int, int], tuple[dict[str, Any] | None, str]] = {}
        threshold, validation_trades, candidates = choose_threshold(
            validation_top1,
            model_views,
            calendar,
            horizon,
            config,
            float(args.capital),
            cache,
        )
        threshold_rows.extend(candidates)
        chosen_signals = validation_top1.loc[
            validation_top1["predicted_cprr"] >= threshold
        ]
        validation_trades, chosen_metrics, decision_dates = simulate_single_position(
            chosen_signals,
            model_views,
            calendar,
            horizon,
            config,
            float(args.capital),
            cache,
        )
        development_pool = samples.loc[
            samples["signal_date"].between(
                pd.Timestamp("2022-01-01"),
                pd.Timestamp("2024-12-31"),
            )
        ]
        random_runs, _ = paired_random_baseline(
            development_pool,
            decision_dates,
            model_ids,
            price_panel,
            calendar,
            horizon,
            config,
            float(args.capital),
            int(args.random_runs),
        )
        chosen_row = next(
            row for row in candidates if float(row["threshold"]) == threshold
        )
        random_median = float(random_runs["total_return"].median())
        result = {
            "horizon": horizon,
            "threshold": threshold,
            **chosen_metrics,
            "positive_years": int(chosen_row["positive_years"]),
            "median_year_return": float(chosen_row["median_year_return"]),
            "year_2022_return": float(chosen_row["year_2022_return"]),
            "year_2023_return": float(chosen_row["year_2023_return"]),
            "year_2024_return": float(chosen_row["year_2024_return"]),
            "rank_ic_mean": float(
                np.mean([item["daily_rank_ic_mean"] for item in diagnostics])
            ),
            "top1_cprr_beats_universe_years": int(
                sum(
                    item["top1_actual_cprr_mean"]
                    > item["universe_actual_cprr_mean"]
                    for item in diagnostics
                )
            ),
            "random_return_median": random_median,
            "beats_random_median": float(chosen_metrics["total_return"])
            > random_median,
        }
        period_results.append(result)
        validation_top1.assign(horizon=horizon).to_csv(
            output_dir / f"validation_top1_h{horizon}.csv",
            index=False,
            encoding="utf-8-sig",
        )
        validation_trades.to_csv(
            output_dir / f"validation_trades_h{horizon}.csv",
            index=False,
            encoding="utf-8-sig",
        )
        log(
            f"H={horizon}: threshold={threshold:.2f}, "
            f"positive years={result['positive_years']}/3, "
            f"return={result['total_return']:.2%}, random median={random_median:.2%}"
        )
        del samples, validation_top1, model_views, cache, random_runs
        gc.collect()

    selected = max(
        period_results,
        key=lambda row: (
            int(row["positive_years"]),
            int(bool(row["beats_random_median"])),
            int(row["top1_cprr_beats_universe_years"]),
            float(row["median_year_return"]),
            float(row["total_return"]),
            -int(row["horizon"]),
        ),
    )
    selected_horizon = int(selected["horizon"])
    selected_threshold = float(selected["threshold"])
    log(
        f"Selected H={selected_horizon}, predicted CPRR threshold="
        f"{selected_threshold:.2f} from 2022-2024 only"
    )

    panel, selected_label_audit = add_horizon_labels(panel, selected_horizon)
    samples, selected_sample_audit = build_samples(panel, calendar)
    final_model, final_calibrator, final_fit_audit = fit_ranker(
        samples, 2025, selected_horizon, quick
    )
    test_top1, test_diagnostics = score_period(
        samples,
        final_model,
        final_calibrator,
        pd.Timestamp("2025-01-01"),
        None,
    )
    test_signals = test_top1.loc[
        test_top1["predicted_cprr"] >= selected_threshold
    ]
    test_ids = {str(value) for value in test_top1["instrument_id"].unique()}
    test_views = build_stock_views(price_panel, test_ids)
    test_cache: dict[
        tuple[str, int, int], tuple[dict[str, Any] | None, str]
    ] = {}
    test_trades, test_metrics, test_dates = simulate_single_position(
        test_signals,
        test_views,
        calendar,
        selected_horizon,
        config,
        float(args.capital),
        test_cache,
    )
    test_pool = samples.loc[samples["signal_date"] >= pd.Timestamp("2025-01-01")]
    test_random_runs, _ = paired_random_baseline(
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
    random_median = float(test_random_runs["total_return"].median())
    strategy_percentile = float(
        (test_random_runs["total_return"] < test_metrics["total_return"]).mean()
    )
    checks = {
        "rank_ic_at_least_0_03": float(test_diagnostics["daily_rank_ic_mean"])
        >= 0.03,
        "top1_cprr_mean_beats_universe": float(
            test_diagnostics["top1_actual_cprr_mean"]
        )
        > float(test_diagnostics["universe_actual_cprr_mean"]),
        "top1_terminal_mean_beats_universe": float(
            test_diagnostics["top1_terminal_mean"]
        )
        > float(test_diagnostics["universe_terminal_mean"]),
        "average_r_above_0_15": float(test_metrics["average_r"] or math.nan)
        > 0.15,
        "profit_factor_above_1_20": float(
            test_metrics["profit_factor"] or math.nan
        )
        > 1.20,
        "max_drawdown_better_than_minus_15pct": float(
            test_metrics["max_drawdown"]
        )
        > -0.15,
        "return_beats_random_median": float(test_metrics["total_return"])
        > random_median,
        "strict_single_position": int(test_metrics["max_concurrent_positions"])
        <= 1,
    }
    passed = int(sum(bool(value) for value in checks.values()))
    if passed >= 7 and int(test_metrics["trades"]) >= 30:
        decision = "快速版有继续全量回测价值，但仍不可直接模拟盘或实盘"
    elif passed >= 5:
        decision = "仅有弱研究价值；先看失败项，不应直接扩大训练"
    else:
        decision = "快速版未显示可用优势，不建议投入全量训练"

    pd.DataFrame(period_results).to_csv(
        output_dir / "period_summary.csv", index=False, encoding="utf-8-sig"
    )
    pd.DataFrame(threshold_rows).to_csv(
        output_dir / "threshold_summary.csv", index=False, encoding="utf-8-sig"
    )
    pd.DataFrame(fold_rows).to_csv(
        output_dir / "walk_forward_folds.csv", index=False, encoding="utf-8-sig"
    )
    test_top1.to_csv(
        output_dir / "test_top1.csv", index=False, encoding="utf-8-sig"
    )
    test_trades.to_csv(
        output_dir / "test_trades.csv", index=False, encoding="utf-8-sig"
    )
    test_random_runs.to_csv(
        output_dir / "random_top1_runs.csv", index=False, encoding="utf-8-sig"
    )
    feature_importance(final_model).to_csv(
        output_dir / "feature_importance.csv", index=False, encoding="utf-8-sig"
    )
    joblib.dump(
        {
            "version": "FinalCPRR_V3",
            "horizon": selected_horizon,
            "entry_threshold": selected_threshold,
            "features": FEATURES,
            "model": final_model,
            "calibrator": final_calibrator,
            "trade_rules": {
                "single_position": True,
                "add_or_reduce": False,
                "protective_stop": PROTECTIVE_STOP,
                "trail_start": TRAIL_START,
                "trail_drawdown": TRAIL_DRAWDOWN,
                "max_position_weight": MAX_POSITION_WEIGHT,
                "risk_per_trade": RISK_PER_TRADE,
            },
        },
        output_dir / "final_cprr_v3_bundle.joblib",
        compress=3,
    )
    summary = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "version": "FinalCPRR_V3_QUICK" if quick else "FinalCPRR_V3_FULL",
        "decision": decision,
        "quick_mode": quick,
        "initial_capital": float(args.capital),
        "random_runs": int(args.random_runs),
        "selection_period": "2022-01-01 through 2024-12-31",
        "selection": {
            "horizon": selected_horizon,
            "threshold": selected_threshold,
        },
        "period_results": period_results,
        "quasi_oos_2025_plus": {
            "diagnostics": test_diagnostics,
            "portfolio": test_metrics,
            "random_top1": {
                "runs": int(len(test_random_runs)),
                "total_return_p05": float(
                    test_random_runs["total_return"].quantile(0.05)
                ),
                "total_return_median": random_median,
                "total_return_p95": float(
                    test_random_runs["total_return"].quantile(0.95)
                ),
                "strategy_percentile": strategy_percentile,
            },
        },
        "assessment": {"checks": checks, "passed": passed, "total": len(checks)},
        "features": FEATURES,
        "fit_audit": final_fit_audit,
        "panel_audit": panel_audit,
        "label_audits": label_audits,
        "sample_audits": sample_audits,
        "selected_label_audit": selected_label_audit,
        "selected_sample_audit": selected_sample_audit,
        "known_limitations": [
            "Historical ST membership is unavailable; current-name ST is an approximation.",
            "Quick mode trains on every fifth date.",
            "2025+ is quasi-OOS because it was studied previously.",
            "Trade-level drawdown is used; intratrade mark-to-market drawdown is not yet reconstructed.",
        ],
    }
    write_json(output_dir / "summary.json", summary)
    write_report(output_dir / "report.md", summary)
    log(f"Decision: {decision}")
    log(f"V3 outputs: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
