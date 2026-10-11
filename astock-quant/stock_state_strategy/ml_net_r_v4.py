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

from backtest_mfe_mae import build_stock_views, load_config, resolve_from
from ml_cprr_v2 import add_daily_features, load_daily_panel
from ml_cprr_v3 import (
    DEFAULT_CAPITAL,
    ENTRY_GAP_MAX,
    FEATURES,
    MAX_POSITION_WEIGHT,
    PROTECTIVE_STOP,
    RISK_PER_TRADE,
    TRAIL_DRAWDOWN,
    TRAIL_START,
    paired_random_baseline,
    simulate_single_position,
    trade_outcome,
)


HORIZONS = (20, 30, 40, 60)
SEED = 20260816
GATES = (
    ("none", -math.inf, 0.0),
    ("r_ge_0", 0.0, 0.0),
    ("r_ge_0_p_ge_52", 0.0, 0.52),
    ("r_ge_10_p_ge_52", 0.10, 0.52),
    ("r_ge_15_p_ge_55", 0.15, 0.55),
)


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


def add_executable_net_r_labels(
    panel: pd.DataFrame,
    horizon: int,
    buy_cost_rate: float,
    sell_cost_rate: float,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Vectorized labels matching the frozen V3 exit engine exactly."""

    log(f"Building H={horizon} executable net-R labels")
    count = len(panel)
    net_return = np.full(count, np.nan, dtype="float32")
    net_r = np.full(count, np.nan, dtype="float32")
    exit_idx_array = np.full(count, -1, dtype="int32")
    entry_gap = np.full(count, np.nan, dtype="float32")
    entry_executable = np.zeros(count, dtype=bool)
    exit_reason_code = np.full(count, -1, dtype="int8")
    incomplete = 0
    unbuyable = 0
    high_gap = 0
    delayed_time_exits = 0

    groups = panel.groupby("instrument_id", sort=False).indices
    for number, positions in enumerate(groups.values(), start=1):
        pos = np.asarray(positions, dtype=np.int64)
        length = len(pos)
        if length <= horizon + 1:
            continue
        adj_open = panel.loc[pos, "adj_open"].to_numpy(dtype=float)
        adj_high = panel.loc[pos, "adj_high"].to_numpy(dtype=float)
        adj_low = panel.loc[pos, "adj_low"].to_numpy(dtype=float)
        adj_close = panel.loc[pos, "adj_close"].to_numpy(dtype=float)
        raw_open = panel.loc[pos, "open"].to_numpy(dtype=float)
        raw_close = panel.loc[pos, "close"].to_numpy(dtype=float)
        status = panel.loc[pos, "limit_status"].to_numpy(dtype="int8")
        trade_idx = panel.loc[pos, "trade_idx"].to_numpy(dtype="int32")

        candidates = length - (horizon + 1)
        row_numbers = np.arange(candidates)
        signal_rows = pos[:candidates]
        entry = adj_open[1 : candidates + 1]
        entry_high = adj_high[1 : candidates + 1]
        entry_low = adj_low[1 : candidates + 1]
        gaps = raw_open[1 : candidates + 1] / raw_close[:candidates] - 1.0
        future_open = sliding_window_view(adj_open[2:], horizon)
        future_high = sliding_window_view(adj_high[2:], horizon)
        future_low = sliding_window_view(adj_low[2:], horizon)
        future_status = sliding_window_view(status[2:], horizon)
        end_close = adj_close[horizon + 1 :]
        end_indices = trade_idx[horizon + 1 :]

        continuous = end_indices - trade_idx[:candidates] == horizon + 1
        finite = (
            np.isfinite(entry)
            & (entry > 0)
            & np.isfinite(entry_high)
            & np.isfinite(entry_low)
            & np.isfinite(gaps)
            & np.isfinite(future_open).all(axis=1)
            & np.isfinite(future_high).all(axis=1)
            & np.isfinite(future_low).all(axis=1)
            & np.isfinite(end_close)
        )

        stop_scale = entry * (1.0 - PROTECTIVE_STOP)
        pending_entry_stop = entry_low <= stop_scale
        prior_peak = np.maximum.accumulate(
            np.concatenate(
                [entry_high[:, None], future_high[:, :-1]], axis=1
            ),
            axis=1,
        )
        trail_active = prior_peak >= entry[:, None] * (1.0 + TRAIL_START)
        trail_scale = prior_peak * (1.0 - TRAIL_DRAWDOWN)
        sellable = future_status != 6
        gap_stop = future_open <= stop_scale[:, None]
        gap_trail = trail_active & (future_open <= trail_scale)
        intraday_stop = future_low <= stop_scale[:, None]
        intraday_trail = trail_active & (future_low <= trail_scale)
        pending_event = pending_entry_stop[:, None] & sellable
        regular_event = sellable & (
            gap_stop | gap_trail | intraday_stop | intraday_trail
        )
        event = np.where(
            pending_entry_stop[:, None], pending_event, regular_event
        )
        has_event = event.any(axis=1)
        first_event = np.argmax(event, axis=1)
        exit_scale = np.full(candidates, np.nan, dtype=float)
        exit_local_idx = np.full(candidates, -1, dtype="int32")
        reason = np.full(candidates, -1, dtype="int8")

        event_rows = np.flatnonzero(has_event)
        if len(event_rows):
            cols = first_event[event_rows]
            exit_local_idx[event_rows] = event_rows + cols + 2
            pending_rows = event_rows[pending_entry_stop[event_rows]]
            if len(pending_rows):
                pending_cols = first_event[pending_rows]
                exit_scale[pending_rows] = future_open[pending_rows, pending_cols]
                reason[pending_rows] = 0

            regular_rows = event_rows[~pending_entry_stop[event_rows]]
            if len(regular_rows):
                regular_cols = first_event[regular_rows]
                row_open = future_open[regular_rows, regular_cols]
                use_gap_stop = gap_stop[regular_rows, regular_cols]
                use_gap_trail = (
                    ~use_gap_stop & gap_trail[regular_rows, regular_cols]
                )
                use_stop = (
                    ~use_gap_stop
                    & ~use_gap_trail
                    & intraday_stop[regular_rows, regular_cols]
                )
                use_trail = ~use_gap_stop & ~use_gap_trail & ~use_stop
                exit_scale[regular_rows[use_gap_stop]] = row_open[use_gap_stop]
                reason[regular_rows[use_gap_stop]] = 1
                exit_scale[regular_rows[use_gap_trail]] = row_open[use_gap_trail]
                reason[regular_rows[use_gap_trail]] = 2
                exit_scale[regular_rows[use_stop]] = stop_scale[
                    regular_rows[use_stop]
                ]
                reason[regular_rows[use_stop]] = 3
                exit_scale[regular_rows[use_trail]] = trail_scale[
                    regular_rows[use_trail], regular_cols[use_trail]
                ]
                reason[regular_rows[use_trail]] = 4

        no_event = ~has_event
        time_sellable = no_event & (future_status[:, -1] != 6)
        exit_scale[time_sellable] = end_close[time_sellable]
        exit_local_idx[time_sellable] = (
            row_numbers[time_sellable] + horizon + 1
        )
        reason[time_sellable] = 5

        delayed_rows = np.flatnonzero(
            no_event & (future_status[:, -1] == 6)
        )
        for row in delayed_rows:
            end_local = int(row + horizon + 1)
            later = np.flatnonzero(status[end_local + 1 :] != 6)
            if len(later):
                later_local = end_local + 1 + int(later[0])
                value = float(adj_open[later_local])
                if np.isfinite(value) and value > 0:
                    exit_scale[row] = value
                    exit_local_idx[row] = later_local
                    reason[row] = 6
                    delayed_time_exits += 1

        valid = (
            continuous
            & finite
            & np.isfinite(exit_scale)
            & (exit_scale > 0)
            & (exit_local_idx >= 0)
        )
        buyable = status[1 : candidates + 1] != 3
        acceptable_gap = gaps <= ENTRY_GAP_MAX
        executable = valid & buyable & acceptable_gap
        incomplete += int((~valid).sum())
        unbuyable += int((valid & ~buyable).sum())
        high_gap += int((valid & buyable & ~acceptable_gap).sum())

        realized = (
            exit_scale / entry * (1.0 - sell_cost_rate)
            - 1.0
            - buy_cost_rate
        )
        valid_rows = signal_rows[valid]
        net_return[valid_rows] = realized[valid].astype("float32")
        net_r[valid_rows] = (realized[valid] / PROTECTIVE_STOP).astype(
            "float32"
        )
        exit_idx_array[valid_rows] = trade_idx[exit_local_idx[valid]]
        exit_reason_code[valid_rows] = reason[valid]
        entry_gap[signal_rows[finite]] = gaps[finite].astype("float32")
        entry_executable[signal_rows[executable]] = True

        if number % 800 == 0 or number == len(groups):
            log(f"H={horizon} label progress {number:,}/{len(groups):,}")

    panel["actual_net_return"] = net_return
    panel["actual_net_r"] = net_r
    panel["profitable"] = (panel["actual_net_return"] > 0).astype("int8")
    panel["label_exit_idx"] = exit_idx_array
    panel["entry_gap"] = entry_gap
    panel["entry_executable"] = entry_executable
    panel["label_exit_reason_code"] = exit_reason_code
    return panel, {
        "horizon": horizon,
        "valid_label_rows": int(panel["actual_net_r"].notna().sum()),
        "entry_executable_rows": int(entry_executable.sum()),
        "incomplete_or_noncontinuous": incomplete,
        "unbuyable_t1": unbuyable,
        "entry_gap_over_3pct": high_gap,
        "delayed_time_exits": delayed_time_exits,
        "target": "net return under the exact frozen exit engine divided by 6% stop",
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
        & panel["actual_net_r"].notna()
        & (panel["label_exit_idx"] >= 0)
        & finite_features
    )
    columns = [
        "instrument_id",
        "trade_date",
        "trade_idx",
        "open",
        "atr20_pct",
        *FEATURES,
        "actual_net_return",
        "actual_net_r",
        "profitable",
        "label_exit_idx",
        "entry_gap",
        "entry_executable",
    ]
    samples = panel.loc[eligible, columns].copy()
    samples = samples.rename(columns={"trade_date": "signal_date"})
    end_date_map = calendar.set_index("trade_idx")["calendar_date"]
    samples["label_end_date"] = samples["label_exit_idx"].map(end_date_map)
    # Compatibility with the frozen trade engine and random selector.
    samples["actual_cprr"] = math.nan
    samples = samples.sort_values(
        ["signal_date", "instrument_id"]
    ).reset_index(drop=True)
    return samples, {
        "rows": int(len(samples)),
        "instruments": int(samples["instrument_id"].nunique()),
        "signal_dates": int(samples["signal_date"].nunique()),
        "executable_rate": float(samples["entry_executable"].mean()),
        "profitable_rate": float(samples["profitable"].mean()),
    }


def validate_label_alignment(
    samples: pd.DataFrame,
    price_panel: pd.DataFrame,
    calendar: pd.DataFrame,
    horizon: int,
    config: dict[str, Any],
    size: int = 120,
) -> dict[str, Any]:
    eligible = samples.loc[samples["entry_executable"]]
    chosen = eligible.sample(min(size, len(eligible)), random_state=SEED + horizon)
    ids = {str(value) for value in chosen["instrument_id"].unique()}
    views = build_stock_views(price_panel, ids)
    calendar_dates = pd.to_datetime(calendar["calendar_date"]).tolist()
    differences = []
    unmatched = 0
    for candidate in chosen.itertuples(index=False):
        outcome, _ = trade_outcome(candidate, views, calendar_dates, horizon)
        if outcome is None:
            unmatched += 1
            continue
        realized = (
            float(outcome["exit_scale"])
            / float(outcome["entry_scale"])
            * (1.0 - float(config["sell_cost_rate"]))
            - 1.0
            - float(config["buy_cost_rate"])
        ) / PROTECTIVE_STOP
        differences.append(abs(realized - float(candidate.actual_net_r)))
    audit = {
        "sampled": int(len(chosen)),
        "matched": int(len(differences)),
        "unmatched": unmatched,
        "max_abs_net_r_error": max(differences) if differences else math.inf,
        "mean_abs_net_r_error": float(np.mean(differences)) if differences else math.inf,
    }
    if unmatched or audit["max_abs_net_r_error"] > 1e-5:
        raise ValueError(f"V4 label/engine mismatch for H={horizon}: {audit}")
    return audit


def date_weights(frame: pd.DataFrame) -> np.ndarray:
    counts = frame.groupby("signal_date")["instrument_id"].transform("size")
    return (1.0 / counts).to_numpy(dtype=float)


def relevance(frame: pd.DataFrame) -> np.ndarray:
    pct_rank = frame.groupby("signal_date")["actual_net_r"].rank(
        pct=True, method="average"
    )
    return np.minimum(np.floor(pct_rank.to_numpy() * 8.0), 7).astype("int8")


def quick_date_sample(frame: pd.DataFrame, quick: bool) -> pd.DataFrame:
    if not quick:
        return frame
    dates = frame["signal_date"].drop_duplicates().to_numpy()[::3]
    return frame.loc[frame["signal_date"].isin(dates)].copy()


def base_params(quick: bool) -> dict[str, Any]:
    return {
        "n_estimators": 500 if quick else 1200,
        "learning_rate": 0.05 if quick else 0.03,
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


def fit_models(
    samples: pd.DataFrame,
    validation_year: int,
    horizon: int,
    quick: bool,
) -> tuple[lgb.LGBMRanker, lgb.LGBMRegressor, lgb.LGBMClassifier, dict[str, Any]]:
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
            f"Empty V4 train/calibration for H={horizon}, year={validation_year}"
        )
    groups = train.groupby("signal_date", sort=False).size().to_numpy()
    eval_groups = calibration.groupby("signal_date", sort=False).size().to_numpy()
    weights = date_weights(train)
    eval_weights = date_weights(calibration)
    params = base_params(quick)
    log(
        f"Fit V4 H={horizon} for {validation_year}: train={len(train):,}, "
        f"calibration={len(calibration):,}"
    )

    ranker = lgb.LGBMRanker(
        objective="lambdarank",
        metric="ndcg",
        lambdarank_truncation_level=10,
        **params,
    )
    ranker.fit(
        train[FEATURES],
        relevance(train),
        group=groups,
        eval_set=[(calibration[FEATURES], relevance(calibration))],
        eval_group=[eval_groups],
        eval_at=[1, 3, 5, 10],
        callbacks=[lgb.early_stopping(50, verbose=False)],
    )

    regressor = lgb.LGBMRegressor(objective="huber", alpha=0.90, **params)
    regressor.fit(
        train[FEATURES],
        train["actual_net_r"].clip(-2.0, 4.0),
        sample_weight=weights,
        eval_set=[
            (
                calibration[FEATURES],
                calibration["actual_net_r"].clip(-2.0, 4.0),
            )
        ],
        eval_sample_weight=[eval_weights],
        eval_metric="l1",
        callbacks=[lgb.early_stopping(50, verbose=False)],
    )

    classifier = lgb.LGBMClassifier(objective="binary", **params)
    classifier.fit(
        train[FEATURES],
        train["profitable"],
        sample_weight=weights,
        eval_set=[(calibration[FEATURES], calibration["profitable"])],
        eval_sample_weight=[eval_weights],
        eval_metric="binary_logloss",
        callbacks=[lgb.early_stopping(50, verbose=False)],
    )
    audit = {
        "validation_year": validation_year,
        "horizon": horizon,
        "train_rows": int(len(train)),
        "train_dates": int(len(groups)),
        "calibration_rows": int(len(calibration)),
        "calibration_dates": int(len(eval_groups)),
        "ranker_iterations": int(ranker.best_iteration_ or ranker.n_estimators),
        "regressor_iterations": int(
            regressor.best_iteration_ or regressor.n_estimators
        ),
        "classifier_iterations": int(
            classifier.best_iteration_ or classifier.n_estimators
        ),
    }
    return ranker, regressor, classifier, audit


def safe_spearman(x: pd.Series, y: pd.Series) -> float:
    if len(x) < 3 or x.nunique() < 2 or y.nunique() < 2:
        return math.nan
    return float(x.corr(y, method="spearman"))


def score_period(
    samples: pd.DataFrame,
    ranker: lgb.LGBMRanker,
    regressor: lgb.LGBMRegressor,
    classifier: lgb.LGBMClassifier,
    start: pd.Timestamp,
    end: pd.Timestamp | None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    mask = samples["signal_date"] >= start
    if end is not None:
        mask &= samples["signal_date"] < end
    frame = samples.loc[mask].copy()
    frame["rank_score"] = ranker.predict(
        frame[FEATURES], num_iteration=ranker.best_iteration_
    )
    frame["predicted_net_r"] = regressor.predict(
        frame[FEATURES], num_iteration=regressor.best_iteration_
    )
    frame["predicted_profit_probability"] = classifier.predict_proba(
        frame[FEATURES], num_iteration=classifier.best_iteration_
    )[:, 1]
    frame["predicted_cprr"] = frame["predicted_net_r"]
    daily_ic = []
    for _, group in frame.groupby("signal_date", sort=False):
        value = safe_spearman(group["rank_score"], group["actual_net_r"])
        if np.isfinite(value):
            daily_ic.append(value)
    top1 = (
        frame.sort_values(
            ["signal_date", "rank_score", "instrument_id"],
            ascending=[True, False, True],
        )
        .groupby("signal_date", sort=False)
        .head(1)
        .copy()
    )
    keep = [
        "instrument_id",
        "signal_date",
        "trade_idx",
        "open",
        "atr20_pct",
        "entry_gap",
        "entry_executable",
        "actual_cprr",
        "actual_net_return",
        "actual_net_r",
        "profitable",
        "rank_score",
        "predicted_net_r",
        "predicted_profit_probability",
        "predicted_cprr",
    ]
    top1 = top1[keep].sort_values("signal_date").reset_index(drop=True)
    return top1, {
        "rows": int(len(frame)),
        "signal_dates": int(frame["signal_date"].nunique()),
        "daily_rank_ic_mean": float(np.mean(daily_ic)) if daily_ic else math.nan,
        "daily_rank_ic_median": float(np.median(daily_ic)) if daily_ic else math.nan,
        "top1_actual_net_r_mean": float(top1["actual_net_r"].mean()),
        "top1_actual_net_r_median": float(top1["actual_net_r"].median()),
        "universe_actual_net_r_mean": float(frame["actual_net_r"].mean()),
        "universe_actual_net_r_median": float(frame["actual_net_r"].median()),
        "top1_profitable_rate": float(top1["profitable"].mean()),
        "universe_profitable_rate": float(frame["profitable"].mean()),
    }


def gate_signals(top1: pd.DataFrame, minimum_r: float, minimum_p: float) -> pd.DataFrame:
    mask = top1["predicted_profit_probability"] >= minimum_p
    if np.isfinite(minimum_r):
        mask &= top1["predicted_net_r"] >= minimum_r
    return top1.loc[mask]


def choose_gate(
    top1: pd.DataFrame,
    stock_views: dict[str, pd.DataFrame],
    calendar: pd.DataFrame,
    horizon: int,
    config: dict[str, Any],
    initial_capital: float,
    cache: dict[tuple[str, int, int], tuple[dict[str, Any] | None, str]],
) -> tuple[dict[str, Any], pd.DataFrame, list[dict[str, Any]]]:
    rows = []
    trade_frames: dict[str, pd.DataFrame] = {}
    base_trades = 0
    for name, minimum_r, minimum_p in GATES:
        signals = gate_signals(top1, minimum_r, minimum_p)
        trades, metrics, _ = simulate_single_position(
            signals,
            stock_views,
            calendar,
            horizon,
            config,
            initial_capital,
            cache,
        )
        if name == "none":
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
            "gate": name,
            "minimum_predicted_net_r": minimum_r,
            "minimum_profit_probability": minimum_p,
            **metrics,
            "positive_years": positive_years,
            "median_year_return": float(np.median(yearly_returns)),
            "year_2022_return": yearly_returns[0],
            "year_2023_return": yearly_returns[1],
            "year_2024_return": yearly_returns[2],
        }
        rows.append(row)
        trade_frames[name] = trades
    minimum_trades = max(12, int(math.ceil(base_trades * 0.50)))
    eligible = [row for row in rows if int(row["trades"]) >= minimum_trades]
    if not eligible:
        eligible = [row for row in rows if row["gate"] == "none"]
    best = max(
        eligible,
        key=lambda row: (
            int(row["positive_years"]),
            float(row["median_year_return"]),
            float(row["total_return"]),
            float(row["profit_factor"] or -math.inf),
            -GATES.index(
                next(item for item in GATES if item[0] == row["gate"])
            ),
        ),
    )
    return best, trade_frames[str(best["gate"])], rows


def feature_importance(model: Any, model_name: str) -> pd.DataFrame:
    gain = model.booster_.feature_importance(importance_type="gain")
    total = float(gain.sum())
    return pd.DataFrame(
        {
            "model": model_name,
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
    lines = [
        "# Net-R V4 单股票Top1快速回测",
        "",
        "## 结论",
        "",
        f"开发集选择H={selected['horizon']}、入场过滤={selected['gate']}。",
        f"**{summary['decision']}**",
        "",
        "## 2022—2024开发结果",
        "",
        "| H | 过滤 | 正收益年份 | 交易数 | 总收益 | 平均R | 盈利因子 | 随机中位数 |",
        "|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary["period_results"]:
        lines.append(
            f"| {row['horizon']} | {row['gate']} | {row['positive_years']}/3 | "
            f"{int(row['trades'])} | {pct(row['total_return'])} | "
            f"{num(row['average_r'])} | {num(row['profit_factor'])} | "
            f"{pct(row['random_return_median'])} |"
        )
    lines.extend(
        [
            "",
            "## 2025以后准样本外",
            "",
            f"- 交易{int(test['portfolio']['trades'])}笔，总收益{pct(test['portfolio']['total_return'])}，平均R {num(test['portfolio']['average_r'])}。",
            f"- 盈利因子{num(test['portfolio']['profit_factor'])}，最大回撤{pct(test['portfolio']['max_drawdown'])}。",
            f"- Rank IC {num(test['diagnostics']['daily_rank_ic_mean'])}；Top1实际净R均值{num(test['diagnostics']['top1_actual_net_r_mean'])}，候选池{num(test['diagnostics']['universe_actual_net_r_mean'])}。",
            f"- 配对随机中位数{pct(test['random_top1']['total_return_median'])}，策略随机百分位{pct(test['random_top1']['strategy_percentile'])}。",
            "",
            "## 规则与边界",
            "",
            "目标标签与实际成交引擎一致：6%保护止损、浮盈8%后5%移动退出、H日到期退出。严格单持仓、全进全出、不加仓、不减仓。2022—2024用于周期和过滤选择；2025以后仍是准样本外。快速版每3个训练日抽样一次。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Net-R V4 single-position Top1 walk-forward backtest"
    )
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--start-year", type=int, default=2020)
    parser.add_argument("--random-runs", type=int, default=10)
    parser.add_argument("--capital", type=float, default=DEFAULT_CAPITAL)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()
    quick = not args.full

    script_dir = Path(__file__).resolve().parent
    config = load_config(resolve_from(script_dir, args.config))
    data_root = resolve_from(script_dir, str(config["data_root"]))
    output_dir = (
        Path(args.output_dir).resolve()
        if args.output_dir
        else resolve_from(script_dir, str(config["output_dir"])) / "ml_v4"
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
    gate_rows: list[dict[str, Any]] = []
    fold_rows: list[dict[str, Any]] = []
    label_audits: dict[int, Any] = {}
    sample_audits: dict[int, Any] = {}
    alignment_audits: dict[int, Any] = {}

    for horizon in HORIZONS:
        panel, label_audit = add_executable_net_r_labels(
            panel,
            horizon,
            float(config["buy_cost_rate"]),
            float(config["sell_cost_rate"]),
        )
        samples, sample_audit = build_samples(panel, calendar)
        alignment_audit = validate_label_alignment(
            samples, price_panel, calendar, horizon, config
        )
        label_audits[horizon] = label_audit
        sample_audits[horizon] = sample_audit
        alignment_audits[horizon] = alignment_audit
        log(f"H={horizon} label alignment: {alignment_audit}")

        top1_parts = []
        diagnostics = []
        for year in (2022, 2023, 2024):
            ranker, regressor, classifier, fit_audit = fit_models(
                samples, year, horizon, quick
            )
            top1, diag = score_period(
                samples,
                ranker,
                regressor,
                classifier,
                pd.Timestamp(f"{year}-01-01"),
                pd.Timestamp(f"{year + 1}-01-01"),
            )
            top1_parts.append(top1)
            diagnostics.append(diag)
            fold_rows.append({"horizon": horizon, **fit_audit, **diag})
            del ranker, regressor, classifier
            gc.collect()

        validation_top1 = pd.concat(top1_parts, ignore_index=True)
        model_ids = {
            str(value) for value in validation_top1["instrument_id"].unique()
        }
        model_views = build_stock_views(price_panel, model_ids)
        cache: dict[
            tuple[str, int, int], tuple[dict[str, Any] | None, str]
        ] = {}
        chosen_gate, validation_trades, candidates = choose_gate(
            validation_top1,
            model_views,
            calendar,
            horizon,
            config,
            float(args.capital),
            cache,
        )
        gate_rows.extend(candidates)
        chosen_signals = gate_signals(
            validation_top1,
            float(chosen_gate["minimum_predicted_net_r"]),
            float(chosen_gate["minimum_profit_probability"]),
        )
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
                pd.Timestamp("2022-01-01"), pd.Timestamp("2024-12-31")
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
        random_median = float(random_runs["total_return"].median())
        result = {
            "horizon": horizon,
            "gate": str(chosen_gate["gate"]),
            "minimum_predicted_net_r": float(
                chosen_gate["minimum_predicted_net_r"]
            ),
            "minimum_profit_probability": float(
                chosen_gate["minimum_profit_probability"]
            ),
            **chosen_metrics,
            "positive_years": int(chosen_gate["positive_years"]),
            "median_year_return": float(chosen_gate["median_year_return"]),
            "rank_ic_mean": float(
                np.mean([item["daily_rank_ic_mean"] for item in diagnostics])
            ),
            "top1_net_r_beats_universe_years": int(
                sum(
                    item["top1_actual_net_r_mean"]
                    > item["universe_actual_net_r_mean"]
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
            f"V4 H={horizon}: gate={result['gate']}, "
            f"positive years={result['positive_years']}/3, "
            f"return={result['total_return']:.2%}, random={random_median:.2%}"
        )
        del samples, validation_top1, model_views, cache, random_runs
        gc.collect()

    selected = max(
        period_results,
        key=lambda row: (
            int(row["positive_years"]),
            int(bool(row["beats_random_median"])),
            int(row["top1_net_r_beats_universe_years"]),
            float(row["median_year_return"]),
            float(row["total_return"]),
            -int(row["horizon"]),
        ),
    )
    selected_horizon = int(selected["horizon"])
    log(f"Selected V4 design: {selected}")

    panel, final_label_audit = add_executable_net_r_labels(
        panel,
        selected_horizon,
        float(config["buy_cost_rate"]),
        float(config["sell_cost_rate"]),
    )
    samples, final_sample_audit = build_samples(panel, calendar)
    final_ranker, final_regressor, final_classifier, final_fit_audit = fit_models(
        samples, 2025, selected_horizon, quick
    )
    test_top1, test_diagnostics = score_period(
        samples,
        final_ranker,
        final_regressor,
        final_classifier,
        pd.Timestamp("2025-01-01"),
        None,
    )
    test_signals = gate_signals(
        test_top1,
        float(selected["minimum_predicted_net_r"]),
        float(selected["minimum_profit_probability"]),
    )
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
        "top1_net_r_mean_beats_universe": float(
            test_diagnostics["top1_actual_net_r_mean"]
        )
        > float(test_diagnostics["universe_actual_net_r_mean"]),
        "top1_profitable_rate_beats_universe": float(
            test_diagnostics["top1_profitable_rate"]
        )
        > float(test_diagnostics["universe_profitable_rate"]),
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
        decision = "V4快速版有继续全量回测价值，但仍不可直接模拟盘或实盘"
    elif passed >= 5:
        decision = "V4只有弱研究价值，不建议立即运行全量版"
    else:
        decision = "V4快速版未显示可用优势，停止该方向"

    pd.DataFrame(period_results).to_csv(
        output_dir / "period_summary.csv", index=False, encoding="utf-8-sig"
    )
    pd.DataFrame(gate_rows).to_csv(
        output_dir / "gate_summary.csv", index=False, encoding="utf-8-sig"
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
    pd.concat(
        [
            feature_importance(final_ranker, "ranker"),
            feature_importance(final_regressor, "regressor"),
            feature_importance(final_classifier, "classifier"),
        ],
        ignore_index=True,
    ).to_csv(
        output_dir / "feature_importance.csv", index=False, encoding="utf-8-sig"
    )
    joblib.dump(
        {
            "version": "NET_R_V4",
            "horizon": selected_horizon,
            "gate": str(selected["gate"]),
            "minimum_predicted_net_r": float(
                selected["minimum_predicted_net_r"]
            ),
            "minimum_profit_probability": float(
                selected["minimum_profit_probability"]
            ),
            "features": FEATURES,
            "ranker": final_ranker,
            "regressor": final_regressor,
            "classifier": final_classifier,
            "single_position": True,
            "add_or_reduce": False,
        },
        output_dir / "net_r_v4_bundle.joblib",
        compress=3,
    )
    summary = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "version": "NET_R_V4_QUICK" if quick else "NET_R_V4_FULL",
        "decision": decision,
        "initial_capital": float(args.capital),
        "random_runs": int(args.random_runs),
        "selection": {
            "horizon": selected_horizon,
            "gate": str(selected["gate"]),
            "minimum_predicted_net_r": float(
                selected["minimum_predicted_net_r"]
            ),
            "minimum_profit_probability": float(
                selected["minimum_profit_probability"]
            ),
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
        "panel_audit": panel_audit,
        "label_audits": label_audits,
        "sample_audits": sample_audits,
        "alignment_audits": alignment_audits,
        "final_label_audit": final_label_audit,
        "final_sample_audit": final_sample_audit,
        "final_fit_audit": final_fit_audit,
        "trade_rules": {
            "protective_stop": PROTECTIVE_STOP,
            "trail_start": TRAIL_START,
            "trail_drawdown": TRAIL_DRAWDOWN,
            "risk_per_trade": RISK_PER_TRADE,
            "max_position_weight": MAX_POSITION_WEIGHT,
            "single_position": True,
            "add_or_reduce": False,
        },
    }
    write_json(output_dir / "summary.json", summary)
    write_report(output_dir / "report.md", summary)
    log(f"Decision: {decision}")
    log(f"V4 outputs: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
