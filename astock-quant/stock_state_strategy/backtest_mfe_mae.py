from __future__ import annotations

import argparse
import json
import math
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from data_source_paths import resolve_project_path

import numpy as np
import pandas as pd


def log(message: str) -> None:
    now = time.strftime("%H:%M:%S")
    print(f"[{now}] {message}", flush=True)


def load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_from(base: Path, value: str) -> Path:
    return resolve_project_path(base, value)


def read_year_partitions(
    data_root: Path,
    dataset: str,
    start_year: int,
    end_year: int,
    columns: list[str],
) -> pd.DataFrame:
    paths: list[Path] = []
    for year in range(start_year, end_year + 1):
        paths.extend(
            sorted(
                (data_root / dataset / f"year={year}").glob(
                    "month=*/data.parquet"
                )
            )
        )
    if not paths:
        raise FileNotFoundError(f"No partitions found for {dataset}")
    frames: list[pd.DataFrame] = []
    for number, path in enumerate(paths, start=1):
        last_error: Exception | None = None
        for attempt in range(1, 5):
            try:
                frames.append(pd.read_parquet(path, columns=columns))
                last_error = None
                break
            except OSError as exc:
                last_error = exc
                log(
                    f"Transient read failure {dataset} {path.parent}: "
                    f"attempt {attempt}/4: {exc}"
                )
                if attempt < 4:
                    time.sleep(2 ** (attempt - 1))
        if last_error is not None:
            raise OSError(
                f"Unable to read {dataset} partition {path} after 4 attempts"
            ) from last_error
        if number % 12 == 0 or number == len(paths):
            log(f"Read {dataset}: {number}/{len(paths)} monthly partitions")
    return pd.concat(frames, ignore_index=True)


def load_panel(
    data_root: Path,
    feature_start: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    instruments = pd.read_parquet(data_root / "instruments.parquet")
    calendar = pd.read_parquet(data_root / "trading_calendar.parquet")
    calendar["calendar_date"] = pd.to_datetime(calendar["calendar_date"])
    calendar = (
        calendar.loc[calendar["is_open"]]
        .sort_values("calendar_date")
        .drop_duplicates("calendar_date")
        .reset_index(drop=True)
    )
    start_year = feature_start.year
    end_year = int(calendar["calendar_date"].dt.year.max())

    log("Reading monthly price partitions")
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
    prices["trade_date"] = pd.to_datetime(prices["trade_date"])
    prices = prices.loc[
        (prices["trade_date"] >= feature_start)
        & prices["is_quality_valid"].fillna(False)
    ].copy()

    log("Reading adjustment factors and daily limit states")
    factors = read_year_partitions(
        data_root,
        "adjustment_factors",
        start_year,
        end_year,
        ["instrument_id", "trade_date", "adjustment_factor"],
    )
    factors["trade_date"] = pd.to_datetime(factors["trade_date"])
    factors = factors.loc[factors["trade_date"] >= feature_start]

    metrics = read_year_partitions(
        data_root,
        "daily_metrics",
        start_year,
        end_year,
        ["instrument_id", "trade_date", "limit_status"],
    )
    metrics["trade_date"] = pd.to_datetime(metrics["trade_date"])
    metrics = metrics.loc[metrics["trade_date"] >= feature_start]

    panel = prices.merge(
        factors,
        on=["instrument_id", "trade_date"],
        how="left",
        validate="one_to_one",
    ).merge(
        metrics,
        on=["instrument_id", "trade_date"],
        how="left",
        validate="one_to_one",
    )

    audit = {
        "price_rows_after_start": int(len(prices)),
        "panel_rows": int(len(panel)),
        "duplicate_keys": int(
            panel.duplicated(["instrument_id", "trade_date"]).sum()
        ),
        "missing_adjustment_factor": int(
            panel["adjustment_factor"].isna().sum()
        ),
        "missing_limit_status": int(panel["limit_status"].isna().sum()),
        "date_min": str(panel["trade_date"].min().date()),
        "date_max": str(panel["trade_date"].max().date()),
        "instruments": int(panel["instrument_id"].nunique()),
    }
    if audit["duplicate_keys"]:
        raise ValueError("Duplicate panel keys found")
    panel = panel.loc[
        panel["adjustment_factor"].notna()
        & (panel["adjustment_factor"] > 0)
    ].copy()
    panel["limit_status"] = panel["limit_status"].fillna(-1).astype("int8")

    date_to_idx = pd.Series(
        np.arange(len(calendar), dtype="int32"),
        index=calendar["calendar_date"],
    )
    panel["trade_idx"] = panel["trade_date"].map(date_to_idx)
    if panel["trade_idx"].isna().any():
        raise ValueError("Price dates not found in trading calendar")
    panel["trade_idx"] = panel["trade_idx"].astype("int32")

    for field in ("open", "high", "low", "close"):
        panel[f"adj_{field}"] = panel[field] * panel["adjustment_factor"]

    panel = panel.sort_values(
        ["instrument_id", "trade_idx"]
    ).reset_index(drop=True)
    return panel, instruments, calendar, audit


def latest_complete_signal_idx(calendar: pd.DataFrame) -> int:
    # T signal, T+1 entry, then T+2 ... T+21 are 20 sellable sessions.
    if len(calendar) < 22:
        raise ValueError("Trading calendar is too short")
    return len(calendar) - 22


def build_signal_dates(
    calendar: pd.DataFrame,
    start: pd.Timestamp,
    cutoff_idx: int,
) -> set[pd.Timestamp]:
    usable = calendar.loc[
        (calendar.index <= cutoff_idx)
        & (calendar["calendar_date"] >= start),
        "calendar_date",
    ].copy()
    frame = pd.DataFrame({"date": usable})
    frame["week"] = frame["date"].dt.to_period("W-FRI")
    return set(frame.groupby("week", observed=True)["date"].max())


def build_features(
    panel: pd.DataFrame,
    instruments: pd.DataFrame,
    calendar: pd.DataFrame,
    signal_dates: set[pd.Timestamp],
    config: dict[str, Any],
) -> pd.DataFrame:
    calendar_values = calendar["calendar_date"].to_numpy(
        dtype="datetime64[ns]"
    )
    listing_idx: dict[str, int] = {}
    for row in instruments[["instrument_id", "list_date"]].itertuples(
        index=False
    ):
        if pd.isna(row.list_date):
            listing_idx[row.instrument_id] = 0
        else:
            listing_idx[row.instrument_id] = int(
                np.searchsorted(
                    calendar_values,
                    np.datetime64(pd.Timestamp(row.list_date)),
                    side="left",
                )
            )

    records: list[pd.DataFrame] = []
    signal_array = np.array(sorted(signal_dates), dtype="datetime64[ns]")
    minimum_recent_observations = int(
        config["minimum_recent_observations"]
    )
    if not 2 <= minimum_recent_observations <= 20:
        raise ValueError(
            "minimum_recent_observations must be between 2 and 20"
        )
    total_groups = panel["instrument_id"].nunique()
    log(f"Building rolling features for {total_groups:,} instruments")

    for number, (instrument_id, group) in enumerate(
        panel.groupby("instrument_id", sort=False), start=1
    ):
        group = group.sort_values("trade_idx")
        adj_close = group["adj_close"]
        previous = adj_close.shift(1)
        true_range = pd.concat(
            [
                group["adj_high"] - group["adj_low"],
                (group["adj_high"] - previous).abs(),
                (group["adj_low"] - previous).abs(),
            ],
            axis=1,
        ).max(axis=1)

        feature = pd.DataFrame(index=group.index)
        feature["ma60"] = adj_close.rolling(60, min_periods=60).mean()
        feature["ma120"] = adj_close.rolling(120, min_periods=120).mean()
        feature["ret60"] = adj_close / adj_close.shift(60) - 1.0
        feature["high20"] = group["adj_high"].rolling(
            20, min_periods=20
        ).max()
        feature["drawdown20"] = 1.0 - adj_close / feature["high20"]
        volume5 = group["volume_shares"].rolling(5, min_periods=5).mean()
        volume20 = group["volume_shares"].rolling(
            20, min_periods=20
        ).mean()
        feature["volume_ratio_5_20"] = volume5 / volume20.replace(0, np.nan)
        feature["atr20_pct"] = (
            true_range.rolling(20, min_periods=20).mean() / adj_close
        )
        feature["amount20"] = group["turnover_cny"].rolling(
            20, min_periods=20
        ).mean()
        feature["recent_coverage_ok"] = (
            group["trade_idx"]
            - group["trade_idx"].shift(
                minimum_recent_observations - 1
            )
            <= 19
        )

        mask = group["trade_date"].to_numpy(dtype="datetime64[ns]")
        mask = np.isin(mask, signal_array)
        if not mask.any():
            continue
        selected = pd.DataFrame(
            {
                "instrument_id": instrument_id,
                "signal_date": group.loc[mask, "trade_date"].to_numpy(),
                "signal_idx": group.loc[mask, "trade_idx"].to_numpy(),
                "adj_close": group.loc[mask, "adj_close"].to_numpy(),
                "ma60": feature.loc[mask, "ma60"].to_numpy(),
                "ma120": feature.loc[mask, "ma120"].to_numpy(),
                "ret60": feature.loc[mask, "ret60"].to_numpy(),
                "drawdown20": feature.loc[mask, "drawdown20"].to_numpy(),
                "volume_ratio_5_20": feature.loc[
                    mask, "volume_ratio_5_20"
                ].to_numpy(),
                "atr20_pct": feature.loc[mask, "atr20_pct"].to_numpy(),
                "amount20": feature.loc[mask, "amount20"].to_numpy(),
                "recent_coverage_ok": feature.loc[
                    mask, "recent_coverage_ok"
                ].to_numpy(),
            }
        )
        selected["listing_sessions"] = (
            selected["signal_idx"] - listing_idx.get(instrument_id, 0)
        )
        records.append(selected)
        if number % 750 == 0:
            log(f"Feature progress: {number:,}/{total_groups:,}")

    features = pd.concat(records, ignore_index=True)
    features["signal_date"] = pd.to_datetime(features["signal_date"])
    features["rs60_rank"] = features.groupby("signal_date")[
        "ret60"
    ].rank(pct=True)

    eligible = features.loc[
        features["recent_coverage_ok"].fillna(False)
        & (features["listing_sessions"] >= config["min_listing_sessions"])
        & (features["adj_close"] > features["ma60"])
        & (features["ma60"] > features["ma120"])
        & (
            features["rs60_rank"]
            >= config["relative_strength_min_rank"]
        )
        & features["drawdown20"].between(
            config["drawdown20_min"], config["drawdown20_max"]
        )
        & (
            features["volume_ratio_5_20"]
            < config["volume_ratio_max"]
        )
        & features["atr20_pct"].between(
            config["atr20_pct_min"], config["atr20_pct_max"]
        )
        & (
            features["amount20"]
            >= config["min_average_turnover_cny"]
        )
    ].copy()

    def cross_section_z(values: pd.Series) -> pd.Series:
        std = values.std(ddof=0)
        if not np.isfinite(std) or std == 0:
            return pd.Series(0.0, index=values.index)
        return (values - values.mean()) / std

    for column in (
        "rs60_rank",
        "atr20_pct",
        "drawdown20",
        "volume_ratio_5_20",
    ):
        eligible[f"z_{column}"] = eligible.groupby(
            "signal_date", group_keys=False
        )[column].transform(cross_section_z)

    eligible["score"] = (
        0.4 * eligible["z_rs60_rank"]
        - 0.2 * eligible["z_atr20_pct"]
        - 0.2 * eligible["z_drawdown20"]
        - 0.2 * eligible["z_volume_ratio_5_20"]
    )
    eligible = eligible.sort_values(
        ["signal_date", "score", "instrument_id"],
        ascending=[True, False, True],
    ).reset_index(drop=True)
    log(
        f"Eligible candidates: {len(eligible):,} across "
        f"{eligible['signal_date'].nunique():,} signal dates"
    )
    return eligible


def build_stock_views(
    panel: pd.DataFrame,
    instrument_ids: set[str],
) -> dict[str, pd.DataFrame]:
    columns = [
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
    subset = panel.loc[panel["instrument_id"].isin(instrument_ids), columns]
    views: dict[str, pd.DataFrame] = {}
    for instrument_id, group in subset.groupby("instrument_id", sort=False):
        views[instrument_id] = group.drop(columns="instrument_id").set_index(
            "trade_idx", drop=True
        ).sort_index()
    return views


def row_at(stock: pd.DataFrame, trade_idx: int) -> pd.Series | None:
    if trade_idx not in stock.index:
        return None
    row = stock.loc[trade_idx]
    if isinstance(row, pd.DataFrame):
        raise ValueError("Duplicate instrument/trade_idx in stock view")
    return row


def compute_outcome(
    candidate: Any,
    stock: pd.DataFrame,
    calendar_dates: list[pd.Timestamp],
    target_pct: float,
    stop_pct: float,
) -> dict[str, Any] | None:
    signal_idx = int(candidate.signal_idx)
    entry_idx = signal_idx + 1
    end_idx = signal_idx + 21
    entry = row_at(stock, entry_idx)
    if entry is None or int(entry["limit_status"]) == 3:
        return None
    entry_scale = float(entry["adj_open"])
    entry_raw = float(entry["open"])
    if not np.isfinite(entry_scale) or entry_scale <= 0:
        return None

    exit_indices = list(range(signal_idx + 2, end_idx + 1))
    exit_window = stock.loc[stock.index.intersection(exit_indices)]
    if exit_window.empty:
        return None

    high_values = exit_window["adj_high"].astype(float)
    peak_idx = int(high_values.idxmax())
    mfe20 = max(0.0, float(high_values.loc[peak_idx]) / entry_scale - 1.0)
    full_path = stock.loc[
        (stock.index >= entry_idx) & (stock.index <= end_idx)
    ]
    mae20 = max(
        0.0,
        1.0 - float(full_path["adj_low"].min()) / entry_scale,
    )
    pre_peak = stock.loc[
        (stock.index >= entry_idx) & (stock.index <= peak_idx)
    ]
    mae_pre = max(
        0.0,
        1.0 - float(pre_peak["adj_low"].min()) / entry_scale,
    )
    risk_floor = max(0.5 * float(candidate.atr20_pct), 0.01)
    q_path = min(10.0, mfe20 / max(mae_pre, risk_floor))

    end_row = row_at(stock, end_idx)
    return_day20 = (
        float(end_row["adj_close"]) / entry_scale - 1.0
        if end_row is not None
        else np.nan
    )

    target_scale = entry_scale * (1.0 + target_pct)
    stop_scale = entry_scale * (1.0 - stop_pct)
    pending_entry_day_stop = float(entry["adj_low"]) <= stop_scale
    exit_idx: int | None = None
    exit_scale: float | None = None
    exit_reason = ""
    exit_timing = ""

    for current_idx in exit_indices:
        current = row_at(stock, current_idx)
        if current is None:
            continue
        limit_status = int(current["limit_status"])
        current_open = float(current["adj_open"])
        current_high = float(current["adj_high"])
        current_low = float(current["adj_low"])

        if pending_entry_day_stop:
            if limit_status == 6:
                continue
            exit_idx = current_idx
            exit_scale = current_open
            exit_reason = "ENTRY_DAY_STOP_NEXT_OPEN"
            exit_timing = "OPEN"
            break

        if current_open <= stop_scale:
            if limit_status == 6:
                continue
            exit_idx = current_idx
            exit_scale = current_open
            exit_reason = "GAP_STOP"
            exit_timing = "OPEN"
            break
        if current_open >= target_scale:
            exit_idx = current_idx
            exit_scale = current_open
            exit_reason = "GAP_TARGET"
            exit_timing = "OPEN"
            break

        hit_stop = current_low <= stop_scale
        hit_target = current_high >= target_scale
        if hit_stop:
            if limit_status == 6:
                continue
            exit_idx = current_idx
            exit_scale = stop_scale
            exit_reason = (
                "BOTH_STOP_FIRST" if hit_target else "INTRADAY_STOP"
            )
            exit_timing = "INTRADAY"
            break
        if hit_target:
            exit_idx = current_idx
            exit_scale = target_scale
            exit_reason = "INTRADAY_TARGET"
            exit_timing = "INTRADAY"
            break

    if exit_idx is None:
        current = row_at(stock, end_idx)
        if current is not None and int(current["limit_status"]) != 6:
            exit_idx = end_idx
            exit_scale = float(current["adj_close"])
            exit_reason = "TIME_CLOSE"
            exit_timing = "CLOSE"
        else:
            for later_idx in stock.index[stock.index > end_idx]:
                later = row_at(stock, int(later_idx))
                if later is not None and int(later["limit_status"]) != 6:
                    exit_idx = int(later_idx)
                    exit_scale = float(later["adj_open"])
                    exit_reason = "TIME_DELAYED_OPEN"
                    exit_timing = "OPEN"
                    break
    if exit_idx is None or exit_scale is None:
        return None

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
        "target_pct": target_pct,
        "stop_pct": stop_pct,
        "atr20_pct": float(candidate.atr20_pct),
        "score": float(candidate.score),
        "mfe20": mfe20,
        "mae20": mae20,
        "mae_pre": mae_pre,
        "q_path": q_path,
        "peak_offset_sessions": peak_idx - entry_idx,
        "return_day20": return_day20,
        "entry_day_stop_breach": pending_entry_day_stop,
    }


def build_outcomes(
    candidates: pd.DataFrame,
    stock_views: dict[str, pd.DataFrame],
    calendar: pd.DataFrame,
    barrier: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, int]]:
    records: list[dict[str, Any]] = []
    calendar_dates = calendar["calendar_date"].tolist()
    no_outcome = 0
    for number, candidate in enumerate(
        candidates.itertuples(index=False), start=1
    ):
        outcome = compute_outcome(
            candidate,
            stock_views[candidate.instrument_id],
            calendar_dates,
            float(barrier["target_pct"]),
            float(barrier["stop_pct"]),
        )
        if outcome is None:
            no_outcome += 1
        else:
            records.append(outcome)
        if number % 5000 == 0:
            log(
                f"Outcome {barrier['name']} progress: "
                f"{number:,}/{len(candidates):,}"
            )
    outcomes = pd.DataFrame(records)
    audit = {
        "candidate_rows": int(len(candidates)),
        "outcome_rows": int(len(outcomes)),
        "no_fill_or_incomplete": int(no_outcome),
    }
    return outcomes, audit


def summarize_trades(trades: pd.DataFrame) -> dict[str, float | int]:
    if trades.empty:
        return {
            "trades": 0,
            "win_rate": np.nan,
            "average_net_return": np.nan,
            "average_r": np.nan,
            "profit_factor": np.nan,
        }
    positive = trades.loc[trades["pnl_cny"] > 0, "pnl_cny"].sum()
    negative = -trades.loc[trades["pnl_cny"] < 0, "pnl_cny"].sum()
    profit_factor = positive / negative if negative > 0 else np.inf
    return {
        "trades": int(len(trades)),
        "win_rate": float((trades["pnl_cny"] > 0).mean()),
        "average_net_return": float(trades["net_return"].mean()),
        "average_r": float(trades["net_r"].mean()),
        "profit_factor": float(profit_factor),
    }


def portfolio_metrics(
    curve: pd.DataFrame,
    trades: pd.DataFrame,
    initial_capital: float,
) -> dict[str, float | int]:
    trade_stats = summarize_trades(trades)
    if curve.empty:
        return {
            **trade_stats,
            "total_return": np.nan,
            "cagr": np.nan,
            "max_drawdown": np.nan,
            "sharpe": np.nan,
            "final_equity": initial_capital,
        }
    equity = curve["equity"].astype(float)
    total_return = float(equity.iloc[-1] / initial_capital - 1.0)
    elapsed_days = max(
        1, (curve["trade_date"].iloc[-1] - curve["trade_date"].iloc[0]).days
    )
    cagr = float((equity.iloc[-1] / initial_capital) ** (365.25 / elapsed_days) - 1.0)
    drawdown = equity / equity.cummax() - 1.0
    daily_return = equity.pct_change().dropna()
    sharpe = (
        float(np.sqrt(252.0) * daily_return.mean() / daily_return.std(ddof=0))
        if len(daily_return) > 1 and daily_return.std(ddof=0) > 0
        else np.nan
    )
    return {
        **trade_stats,
        "total_return": total_return,
        "cagr": cagr,
        "max_drawdown": float(drawdown.min()),
        "sharpe": sharpe,
        "final_equity": float(equity.iloc[-1]),
        "max_concurrent_positions": int(curve["active_positions"].max()),
    }


def simulate_portfolio(
    outcomes: pd.DataFrame,
    stock_views: dict[str, pd.DataFrame],
    calendar: pd.DataFrame,
    config: dict[str, Any],
    random_mode: bool = False,
    seed: int | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float | int]]:
    if outcomes.empty:
        empty = pd.DataFrame()
        return empty, empty, portfolio_metrics(
            empty, empty, float(config["initial_capital"])
        )

    entry_groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for record in outcomes.to_dict("records"):
        entry_groups[int(record["entry_idx"])].append(record)
    rng = np.random.default_rng(seed)

    initial_capital = float(config["initial_capital"])
    cash = initial_capital
    active: dict[str, dict[str, Any]] = {}
    completed: list[dict[str, Any]] = []
    curve_records: list[dict[str, Any]] = []
    min_idx = min(entry_groups)
    max_idx = int(outcomes["exit_idx"].max())
    buy_cost_rate = float(config["buy_cost_rate"])
    sell_cost_rate = float(config["sell_cost_rate"])

    def update_marks(trade_idx: int, field: str) -> None:
        for instrument_id, position in active.items():
            stock_row = row_at(stock_views[instrument_id], trade_idx)
            if stock_row is not None:
                position["last_mark_scale"] = float(stock_row[field])

    def current_equity() -> float:
        return cash + sum(
            position["units"] * position["last_mark_scale"]
            for position in active.values()
        )

    def close_position(instrument_id: str) -> None:
        nonlocal cash
        position = active.pop(instrument_id)
        outcome = position["outcome"]
        proceeds = (
            position["units"]
            * float(outcome["exit_scale"])
            * (1.0 - sell_cost_rate)
        )
        cash += proceeds
        pnl = proceeds - position["notional"] - position["buy_cost"]
        net_return = pnl / position["notional"]
        completed.append(
            {
                **outcome,
                "shares": position["shares"],
                "notional_cny": position["notional"],
                "planned_risk_cny": position["planned_risk"],
                "buy_cost_cny": position["buy_cost"],
                "sell_proceeds_cny": proceeds,
                "pnl_cny": pnl,
                "net_return": net_return,
                "net_r": net_return / float(outcome["stop_pct"]),
            }
        )

    for trade_idx in range(min_idx, max_idx + 1):
        update_marks(trade_idx, "adj_open")

        open_exits = [
            instrument_id
            for instrument_id, position in active.items()
            if int(position["outcome"]["exit_idx"]) == trade_idx
            and position["outcome"]["exit_timing"] == "OPEN"
        ]
        for instrument_id in open_exits:
            close_position(instrument_id)

        candidates = list(entry_groups.get(trade_idx, []))
        if candidates:
            if random_mode:
                rng.shuffle(candidates)
            else:
                candidates.sort(
                    key=lambda value: (
                        -float(value["score"]), value["instrument_id"]
                    )
                )
            opened = 0
            for outcome in candidates:
                if opened >= int(config["max_new_positions_per_signal"]):
                    break
                if len(active) >= int(config["max_positions"]):
                    break
                instrument_id = outcome["instrument_id"]
                if instrument_id in active:
                    continue

                equity = current_equity()
                active_risk = sum(
                    position["planned_risk"] for position in active.values()
                )
                available_heat = (
                    equity * float(config["portfolio_heat_limit"])
                    - active_risk
                )
                risk_budget = min(
                    equity * float(config["risk_per_trade"]),
                    available_heat,
                )
                if risk_budget <= 0:
                    break
                gap_buffer_pct = max(
                    0.5 * float(outcome["atr20_pct"]), 0.01
                )
                effective_risk_pct = (
                    float(outcome["stop_pct"]) + gap_buffer_pct
                )
                target_notional = min(
                    equity * float(config["max_position_weight"]),
                    risk_budget / effective_risk_pct,
                )
                raw_entry = float(outcome["entry_raw_price"])
                affordable = cash / (1.0 + buy_cost_rate)
                target_notional = min(target_notional, affordable)
                shares = math.floor(target_notional / raw_entry / 100.0) * 100
                if shares < 100:
                    continue
                notional = shares * raw_entry
                buy_cost = notional * buy_cost_rate
                units = notional / float(outcome["entry_scale"])
                planned_risk = notional * effective_risk_pct
                cash -= notional + buy_cost
                active[instrument_id] = {
                    "outcome": outcome,
                    "shares": shares,
                    "notional": notional,
                    "buy_cost": buy_cost,
                    "planned_risk": planned_risk,
                    "units": units,
                    "last_mark_scale": float(outcome["entry_scale"]),
                }
                opened += 1

        later_exits = [
            instrument_id
            for instrument_id, position in active.items()
            if int(position["outcome"]["exit_idx"]) == trade_idx
            and position["outcome"]["exit_timing"] != "OPEN"
        ]
        for instrument_id in later_exits:
            close_position(instrument_id)

        update_marks(trade_idx, "adj_close")
        curve_records.append(
            {
                "trade_date": calendar.loc[trade_idx, "calendar_date"],
                "equity": current_equity(),
                "cash": cash,
                "active_positions": len(active),
            }
        )

    curve = pd.DataFrame(curve_records)
    trades = pd.DataFrame(completed)
    metrics = portfolio_metrics(curve, trades, initial_capital)
    return trades, curve, metrics


def period_metrics(
    curve: pd.DataFrame,
    trades: pd.DataFrame,
    initial_capital: float,
    start: pd.Timestamp,
    end: pd.Timestamp | None,
) -> dict[str, float | int]:
    mask = curve["trade_date"] >= start
    if end is not None:
        mask &= curve["trade_date"] <= end
    period_curve = curve.loc[mask].copy()
    before = curve.loc[curve["trade_date"] < start, "equity"]
    base = float(before.iloc[-1]) if not before.empty else initial_capital
    trade_mask = trades["signal_date"] >= start
    if end is not None:
        trade_mask &= trades["signal_date"] <= end
    period_trades = trades.loc[trade_mask].copy()
    if period_curve.empty:
        return {
            **summarize_trades(period_trades),
            "total_return": np.nan,
            "max_drawdown": np.nan,
        }
    values = pd.concat(
        [pd.Series([base]), period_curve["equity"].reset_index(drop=True)]
    )
    drawdown = values / values.cummax() - 1.0
    return {
        **summarize_trades(period_trades),
        "total_return": float(period_curve["equity"].iloc[-1] / base - 1.0),
        "max_drawdown": float(drawdown.min()),
    }


def yearly_returns(
    curve: pd.DataFrame,
    initial_capital: float,
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    previous = initial_capital
    for year, group in curve.groupby(curve["trade_date"].dt.year):
        ending = float(group["equity"].iloc[-1])
        records.append(
            {
                "year": int(year),
                "starting_equity": previous,
                "ending_equity": ending,
                "return": ending / previous - 1.0,
            }
        )
        previous = ending
    return pd.DataFrame(records)


def finite_or_none(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, dict):
        return {key: finite_or_none(item) for key, item in value.items()}
    if isinstance(value, list):
        return [finite_or_none(item) for item in value]
    return value


def pct(value: Any) -> str:
    return "NA" if value is None or not np.isfinite(value) else f"{value:.2%}"


def num(value: Any, digits: int = 3) -> str:
    return "NA" if value is None or not np.isfinite(value) else f"{value:.{digits}f}"


def write_report(
    path: Path,
    summary: dict[str, Any],
    parameter_table: pd.DataFrame,
    yearly: pd.DataFrame,
) -> None:
    primary = summary["primary"]
    test = summary["periods"]["test"]
    labels = summary["label_diagnostics"]
    random_stats = summary["random_baseline"]
    verdict = summary["verdict"]
    lines = [
        "# A股主板MFE/MAE快速回测报告",
        "",
        f"生成时间：{summary['generated_at']}",
        "",
        "## 结论",
        "",
        verdict["text"],
        "",
        "## 主参数（+8%/-4%）",
        "",
        "| 指标 | 全期 | 2025年至测试截止 |",
        "|---|---:|---:|",
        f"| 交易数 | {primary['trades']} | {test['trades']} |",
        f"| 总收益 | {pct(primary['total_return'])} | {pct(test['total_return'])} |",
        f"| 平均R | {num(primary['average_r'])} | {num(test['average_r'])} |",
        f"| 胜率 | {pct(primary['win_rate'])} | {pct(test['win_rate'])} |",
        f"| 盈利因子 | {num(primary['profit_factor'])} | {num(test['profit_factor'])} |",
        f"| 最大回撤 | {pct(primary['max_drawdown'])} | {pct(test['max_drawdown'])} |",
        "",
        "## 标签诊断",
        "",
        f"- 合格候选标签数：{labels['candidate_count']}。",
        f"- 实际组合交易标签数：{labels['executed_count']}。",
        f"- 候选池Q_path中位数：{num(labels['candidate_q_median'])}。",
        f"- 执行交易Q_path中位数：{num(labels['executed_q_median'])}。",
        f"- 候选池“未来MFE≥8%且上涨前MAE≤4%”比例：{pct(labels['candidate_good_rate'])}。",
        f"- 执行交易对应比例：{pct(labels['executed_good_rate'])}。",
        "",
        "## 参数稳健性",
        "",
        "| 障碍 | 交易数 | 总收益 | 平均R | 盈利因子 | 最大回撤 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in parameter_table.itertuples(index=False):
        lines.append(
            f"| {row.barrier} | {row.trades} | {pct(row.total_return)} | "
            f"{num(row.average_r)} | {num(row.profit_factor)} | "
            f"{pct(row.max_drawdown)} |"
        )
    lines.extend(
        [
            "",
            "## 随机基准",
            "",
            f"- 重复次数：{random_stats['runs']}。",
            f"- 随机基准总收益中位数：{pct(random_stats['total_return_median'])}。",
            f"- 随机基准总收益5%—95%区间：{pct(random_stats['total_return_p05'])}至{pct(random_stats['total_return_p95'])}。",
            f"- 策略总收益在随机基准中的百分位：{pct(random_stats['strategy_percentile'])}。",
            f"- 随机基准测试期平均R中位数：{num(random_stats['test_average_r_median'])}。",
            "",
            "## 年度收益",
            "",
            "| 年份 | 收益 | 期末权益 |",
            "|---:|---:|---:|",
        ]
    )
    for row in yearly.itertuples(index=False):
        lines.append(
            f"| {row.year} | {pct(row.return_)} | {row.ending_equity:,.2f} |"
        )
    lines.extend(
        [
            "",
            "## 已知问题",
            "",
            "- 数据没有历史证券简称/ST状态，第一版没有精确排除历史ST。",
            "- 数据没有停复牌事件表；无行情日按不可交易处理，但不能解释停牌原因。",
            "- `limit_status`只能近似模拟一字涨跌停，日线不能还原排队成交。",
            "- 没有历史时点行业分类，因此未执行行业持仓上限。",
            "- 没有时点财务数据，本轮只验证价格量特征。",
            "- 组合使用日线调整价格按收盘盯市；盘中最大回撤可能高于报告值。",
            "",
            "## 输出说明",
            "",
            "完整参数、候选、交易、权益曲线、随机基准和机器可读摘要均在同目录保存。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", default="config.json", help="Path to JSON config"
    )
    args = parser.parse_args()
    script_dir = Path(__file__).resolve().parent
    config_path = resolve_from(script_dir, args.config)
    config = load_config(config_path)
    data_root = resolve_from(script_dir, config["data_root"])
    output_dir = resolve_from(script_dir, config["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)

    feature_start = pd.Timestamp(config["feature_start"])
    signal_start = pd.Timestamp(config["signal_start"])
    validation_start = pd.Timestamp(config["validation_start"])
    test_start = pd.Timestamp(config["test_start"])

    panel, instruments, calendar, data_audit = load_panel(
        data_root, feature_start
    )
    cutoff_idx = latest_complete_signal_idx(calendar)
    cutoff_date = calendar.loc[cutoff_idx, "calendar_date"]
    signal_dates = build_signal_dates(
        calendar, signal_start, cutoff_idx
    )
    log(
        f"Data through {calendar['calendar_date'].max().date()}, "
        f"latest complete signal date {cutoff_date.date()}"
    )

    candidates = build_features(
        panel, instruments, calendar, signal_dates, config
    )
    candidates.to_csv(
        output_dir / "eligible_candidates.csv", index=False, encoding="utf-8-sig"
    )
    if candidates.empty:
        raise RuntimeError("No eligible candidates under current filters")

    instrument_ids = set(candidates["instrument_id"])
    stock_views = build_stock_views(panel, instrument_ids)
    del panel

    outcomes_by_barrier: dict[str, pd.DataFrame] = {}
    outcome_audits: dict[str, Any] = {}
    parameter_rows: list[dict[str, Any]] = []
    strategy_results: dict[str, Any] = {}

    for barrier in config["barriers"]:
        name = barrier["name"]
        log(f"Computing path labels and barrier outcomes: {name}")
        outcomes, outcome_audit = build_outcomes(
            candidates, stock_views, calendar, barrier
        )
        outcomes_by_barrier[name] = outcomes
        outcome_audits[name] = outcome_audit
        outcomes.to_csv(
            output_dir / f"candidate_outcomes_{name}.csv",
            index=False,
            encoding="utf-8-sig",
        )
        log(f"Simulating strategy portfolio: {name}")
        trades, curve, metrics = simulate_portfolio(
            outcomes, stock_views, calendar, config
        )
        trades.to_csv(
            output_dir / f"strategy_trades_{name}.csv",
            index=False,
            encoding="utf-8-sig",
        )
        curve.to_csv(
            output_dir / f"equity_curve_{name}.csv",
            index=False,
            encoding="utf-8-sig",
        )
        strategy_results[name] = {
            "trades": trades,
            "curve": curve,
            "metrics": metrics,
        }
        parameter_rows.append({"barrier": name, **metrics})

    parameter_table = pd.DataFrame(parameter_rows)
    parameter_table.to_csv(
        output_dir / "parameter_summary.csv",
        index=False,
        encoding="utf-8-sig",
    )

    primary_name = config["primary_barrier"]
    primary_outcomes = outcomes_by_barrier[primary_name]
    primary_result = strategy_results[primary_name]
    primary_trades = primary_result["trades"]
    primary_curve = primary_result["curve"]
    initial_capital = float(config["initial_capital"])

    periods = {
        "development": period_metrics(
            primary_curve,
            primary_trades,
            initial_capital,
            signal_start,
            validation_start - pd.Timedelta(days=1),
        ),
        "validation": period_metrics(
            primary_curve,
            primary_trades,
            initial_capital,
            validation_start,
            test_start - pd.Timedelta(days=1),
        ),
        "test": period_metrics(
            primary_curve,
            primary_trades,
            initial_capital,
            test_start,
            None,
        ),
    }

    log(
        f"Running {config['random_baseline_runs']} matched random baselines"
    )
    random_rows: list[dict[str, Any]] = []
    for run in range(int(config["random_baseline_runs"])):
        random_trades, random_curve, random_metrics = simulate_portfolio(
            primary_outcomes,
            stock_views,
            calendar,
            config,
            random_mode=True,
            seed=int(config["random_seed"]) + run,
        )
        random_test = period_metrics(
            random_curve,
            random_trades,
            initial_capital,
            test_start,
            None,
        )
        random_rows.append(
            {
                "run": run,
                **random_metrics,
                "test_total_return": random_test["total_return"],
                "test_average_r": random_test["average_r"],
                "test_profit_factor": random_test["profit_factor"],
            }
        )
        if (run + 1) % 20 == 0:
            log(f"Random baseline progress: {run + 1}")
    random_runs = pd.DataFrame(random_rows)
    random_runs.to_csv(
        output_dir / "random_baseline_runs.csv",
        index=False,
        encoding="utf-8-sig",
    )

    random_summary = {
        "runs": int(len(random_runs)),
        "total_return_median": float(random_runs["total_return"].median()),
        "total_return_p05": float(random_runs["total_return"].quantile(0.05)),
        "total_return_p95": float(random_runs["total_return"].quantile(0.95)),
        "strategy_percentile": float(
            (random_runs["total_return"] < primary_result["metrics"]["total_return"]).mean()
        ),
        "test_average_r_median": float(
            random_runs["test_average_r"].median()
        ),
        "test_total_return_median": float(
            random_runs["test_total_return"].median()
        ),
    }

    executed_keys = primary_trades[["instrument_id", "signal_date"]].drop_duplicates()
    executed_labels = primary_outcomes.merge(
        executed_keys,
        on=["instrument_id", "signal_date"],
        how="inner",
        validate="one_to_one",
    )
    candidate_good = (
        (primary_outcomes["mfe20"] >= 0.08)
        & (primary_outcomes["mae_pre"] <= 0.04)
    )
    executed_good = (
        (executed_labels["mfe20"] >= 0.08)
        & (executed_labels["mae_pre"] <= 0.04)
    )
    label_diagnostics = {
        "candidate_count": int(len(primary_outcomes)),
        "executed_count": int(len(executed_labels)),
        "candidate_q_median": float(primary_outcomes["q_path"].median()),
        "executed_q_median": float(executed_labels["q_path"].median()),
        "candidate_good_rate": float(candidate_good.mean()),
        "executed_good_rate": float(executed_good.mean()),
    }

    yearly = yearly_returns(primary_curve, initial_capital)
    yearly_for_report = yearly.rename(columns={"return": "return_"})
    yearly.to_csv(
        output_dir / "yearly_returns.csv", index=False, encoding="utf-8-sig"
    )

    test = periods["test"]
    parameter_positive = int((parameter_table["average_r"] > 0).sum())
    criteria = {
        "test_trades_at_least_200": test["trades"] >= 200,
        "test_average_r_above_0_15": (
            np.isfinite(test["average_r"]) and test["average_r"] > 0.15
        ),
        "test_profit_factor_above_1_20": (
            np.isfinite(test["profit_factor"])
            and test["profit_factor"] > 1.20
        ),
        "test_drawdown_below_15pct": (
            np.isfinite(test["max_drawdown"])
            and test["max_drawdown"] > -0.15
        ),
        "label_good_rate_lift_5pp": (
            label_diagnostics["executed_good_rate"]
            - label_diagnostics["candidate_good_rate"]
            >= 0.05
        ),
        "q_median_lift_20pct": (
            label_diagnostics["executed_q_median"]
            >= 1.2 * label_diagnostics["candidate_q_median"]
        ),
        "all_barrier_average_r_positive": parameter_positive
        == len(parameter_table),
        "strategy_above_random_median": random_summary[
            "strategy_percentile"
        ]
        > 0.50,
    }
    passed = sum(bool(value) for value in criteria.values())
    if test["trades"] < 200:
        verdict_text = (
            "第一版结果只能视为探索性证据：最终测试期交易数不足200笔，"
            "即使收益指标为正也不能据此确认策略有效。"
        )
    elif passed >= 6:
        verdict_text = (
            "第一版结果显示策略具有继续研究价值，但仍需补齐历史ST/停牌信息并进行滚动样本外验证。"
        )
    else:
        verdict_text = (
            "第一版结果未达到预设的大部分可行性标准，暂不支持直接进入模拟盘；应先检查筛选逻辑、样本量和执行约束。"
        )

    summary = {
        "generated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
        "config": config,
        "data_audit": data_audit,
        "latest_data_date": str(calendar["calendar_date"].max().date()),
        "latest_complete_signal_date": str(cutoff_date.date()),
        "signal_dates": int(len(signal_dates)),
        "eligible_candidates": int(len(candidates)),
        "outcome_audits": outcome_audits,
        "primary_barrier": primary_name,
        "primary": primary_result["metrics"],
        "periods": periods,
        "label_diagnostics": label_diagnostics,
        "random_baseline": random_summary,
        "criteria": criteria,
        "criteria_passed": passed,
        "criteria_total": len(criteria),
        "verdict": {"text": verdict_text},
    }
    summary = finite_or_none(summary)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_report(
        output_dir / "report.md",
        summary,
        parameter_table,
        yearly_for_report,
    )
    log(f"Completed. Report: {output_dir / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
