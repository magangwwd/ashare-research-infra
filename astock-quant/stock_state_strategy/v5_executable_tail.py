from __future__ import annotations

import argparse
import json
import math
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view
from pit_name_states import historical_name_states

from backtest_mfe_mae import (
    load_config,
    load_panel,
    read_year_partitions,
    resolve_from,
)


MIN_STOP = 0.04
MAX_STOP = 0.08
ATR_STOP_MULTIPLIER = 1.50
ENTRY_GAP_MAX = 0.03
EXIT_REASONS = {
    0: "ENTRY_DAY_STOP_NEXT_OPEN",
    1: "GAP_STOP",
    2: "INTRADAY_STOP",
    3: "TIME_EXIT",
    4: "TIME_EXIT_DELAYED",
}


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


def writable_output_dir(preferred: Path, fallback_name: str) -> Path:
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        probe = preferred / ".codex_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return preferred
    except OSError as exc:
        fallback = Path(tempfile.gettempdir()) / fallback_name
        fallback.mkdir(parents=True, exist_ok=True)
        print(
            f"Output directory is not writable ({exc}); using {fallback}",
            flush=True,
        )
        return fallback


def add_atr20_pct(panel: pd.DataFrame) -> pd.DataFrame:
    panel = panel.sort_values(["instrument_id", "trade_idx"]).copy()
    group = panel.groupby("instrument_id", sort=False, group_keys=False)
    previous_close = group["adj_close"].shift(1)
    true_range = np.maximum(
        panel["adj_high"] - panel["adj_low"],
        np.maximum(
            (panel["adj_high"] - previous_close).abs(),
            (panel["adj_low"] - previous_close).abs(),
        ),
    )
    panel["_true_range_v5"] = true_range
    panel["atr20_pct"] = panel.groupby(
        "instrument_id", sort=False
    )["_true_range_v5"].transform(
        lambda values: values.rolling(20, min_periods=20).mean()
    ) / panel["adj_close"]
    return panel.drop(columns="_true_range_v5")


def add_point_in_time_states(
    panel: pd.DataFrame,
    data_root: Path,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Attach exact limit-board and historical ST truth to the price panel."""

    start_year = int(panel["trade_date"].dt.year.min())
    end_year = int(panel["trade_date"].dt.year.max())
    limits = read_year_partitions(
        data_root,
        "daily_limits",
        start_year,
        end_year,
        ["instrument_id", "trade_date", "up_limit", "down_limit"],
    )
    limits["trade_date"] = pd.to_datetime(limits["trade_date"])
    duplicate_limits = int(
        limits.duplicated(["instrument_id", "trade_date"]).sum()
    )
    if duplicate_limits:
        raise ValueError(f"daily_limits has {duplicate_limits} duplicate keys")
    panel = panel.merge(
        limits,
        on=["instrument_id", "trade_date"],
        how="left",
        validate="one_to_one",
    )
    exact_known = (
        panel["up_limit"].notna()
        & panel["down_limit"].notna()
        & (panel["up_limit"] > 0)
        & (panel["down_limit"] > 0)
        & (panel["down_limit"] <= panel["up_limit"])
    )
    if not exact_known.all():
        raise ValueError(
            f"{int((~exact_known).sum())} panel rows lack valid exact daily limits"
        )
    flat_bar = (
        np.isclose(panel["open"], panel["high"], atol=0.0051, rtol=0)
        & np.isclose(panel["open"], panel["low"], atol=0.0051, rtol=0)
        & np.isclose(panel["open"], panel["close"], atol=0.0051, rtol=0)
    )
    exact_one_word_up = flat_bar & np.isclose(
        panel["open"], panel["up_limit"], atol=0.0051, rtol=0
    )
    exact_one_word_down = flat_bar & np.isclose(
        panel["open"], panel["down_limit"], atol=0.0051, rtol=0
    )
    provider_status = pd.to_numeric(panel["limit_status"], errors="coerce")
    panel["provider_limit_status"] = provider_status
    panel["limit_status"] = np.select(
        [exact_one_word_up, exact_one_word_down],
        [3, 6],
        default=0,
    ).astype("int8")

    history = pd.read_parquet(data_root / "name_history.parquet")
    name_states = historical_name_states(panel, history)
    state_known = name_states["historical_state_known"].to_numpy(dtype=bool)
    historical_is_st = name_states["historical_is_st"].to_numpy(dtype=bool)
    if not state_known.all():
        raise ValueError(
            f"{int((~state_known).sum())} panel rows lack point-in-time name/ST state"
        )
    panel["historical_state_known"] = state_known
    panel["historical_is_st"] = historical_is_st
    audit = {
        "daily_limit_rows": int(len(limits)),
        "exact_limit_missing_rows": int((~exact_known).sum()),
        "exact_one_word_up_rows": int(exact_one_word_up.sum()),
        "exact_one_word_down_rows": int(exact_one_word_down.sum()),
        "provider_limit_status_unknown_rows": int(
            (~provider_status.isin(range(7))).sum()
        ),
        "historical_state_missing_rows": int((~state_known).sum()),
        "historical_st_rows": int(historical_is_st.sum()),
        "source_rule": "exact daily limits + point-in-time name intervals",
    }
    return panel, audit


def _dense(values: np.ndarray, relative_idx: np.ndarray, length: int, fill: Any) -> np.ndarray:
    result = np.full(length, fill, dtype=values.dtype)
    result[relative_idx] = values
    return result


def add_executable_tail_labels(
    panel: pd.DataFrame,
    horizon: int,
    buy_cost_rate: float,
    sell_cost_rate: float,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build calendar-aligned V5 risk labels under a frozen, model-free exit rule.

    T close is the signal and T+1 open is the planned entry.  The protective stop
    is clip(1.5 * ATR20, 4%, 8%), where ATR20 is known on T.  A stop touched on
    the entry day is executed at the next sellable open.  Otherwise a sellable
    gap/intraday stop is honored.  If no stop occurs, the position exits at the H
    day close; a missing bar or one-word limit-down extends the label to the first
    later sellable open.
    """

    if horizon < 1:
        raise ValueError("horizon must be positive")
    required = {
        "instrument_id",
        "trade_idx",
        "open",
        "close",
        "adj_open",
        "adj_high",
        "adj_low",
        "adj_close",
        "limit_status",
        "atr20_pct",
        "historical_state_known",
        "historical_is_st",
    }
    missing = sorted(required - set(panel.columns))
    if missing:
        raise ValueError(f"Missing V5 label columns: {missing}")
    panel = panel.sort_values(["instrument_id", "trade_idx"]).reset_index(drop=True).copy()
    row_count = len(panel)
    float_output = {
        "v5_stop_pct": np.full(row_count, np.nan, dtype="float32"),
        "v5_entry_gap": np.full(row_count, np.nan, dtype="float32"),
        # Adjusted prices can be large after cumulative corporate-action factors;
        # retain float64 so the label can reconcile to the scalar execution engine
        # without a storage-rounding false positive.
        "v5_exit_scale": np.full(row_count, np.nan, dtype="float64"),
        "v5_net_return": np.full(row_count, np.nan, dtype="float32"),
        "v5_net_r": np.full(row_count, np.nan, dtype="float32"),
        "v5_tail_loss_pct": np.full(row_count, np.nan, dtype="float32"),
        "v5_tail_loss_r": np.full(row_count, np.nan, dtype="float32"),
        "v5_fixed_window_b": np.full(row_count, np.nan, dtype="float32"),
    }
    exit_idx_output = np.full(row_count, -1, dtype="int32")
    delay_output = np.full(row_count, -1, dtype="int16")
    missing_days_output = np.full(row_count, -1, dtype="int16")
    reason_output = np.full(row_count, -1, dtype="int8")
    entry_executable_output = np.zeros(row_count, dtype=bool)
    valid_output = np.zeros(row_count, dtype=bool)

    counters = {
        "candidate_rows": 0,
        "entry_executable_rows": 0,
        "valid_label_rows": 0,
        "entry_missing": 0,
        "entry_unbuyable": 0,
        "entry_gap_over_3pct": 0,
        "signal_historical_st": 0,
        "signal_state_unknown": 0,
        "unknown_state_paths": 0,
        "no_sellable_exit": 0,
        "delayed_time_exits": 0,
    }

    groups = panel.groupby("instrument_id", sort=False).indices
    for positions in groups.values():
        pos = np.asarray(positions, dtype=np.int64)
        observed_trade_idx = panel.loc[pos, "trade_idx"].to_numpy(dtype="int32")
        first_idx = int(observed_trade_idx.min())
        relative_idx = observed_trade_idx - first_idx
        dense_length = int(relative_idx.max()) + 1
        candidates = dense_length - horizon - 1
        if candidates <= 0:
            continue

        row_index = np.full(dense_length, -1, dtype="int64")
        row_index[relative_idx] = pos
        present = row_index >= 0

        def dense_float(column: str) -> np.ndarray:
            values = panel.loc[pos, column].to_numpy(dtype="float64")
            return _dense(values, relative_idx, dense_length, np.nan)

        raw_open = dense_float("open")
        raw_close = dense_float("close")
        adj_open = dense_float("adj_open")
        adj_high = dense_float("adj_high")
        adj_low = dense_float("adj_low")
        adj_close = dense_float("adj_close")
        atr = dense_float("atr20_pct")
        observed_status = panel.loc[pos, "limit_status"].to_numpy(dtype="int16")
        status = _dense(observed_status, relative_idx, dense_length, np.int16(-128))
        status_known = (status >= 0) & (status <= 6)
        observed_name_known = panel.loc[
            pos, "historical_state_known"
        ].to_numpy(dtype=bool)
        name_known = _dense(
            observed_name_known, relative_idx, dense_length, np.bool_(False)
        )
        observed_is_st = panel.loc[pos, "historical_is_st"].to_numpy(dtype=bool)
        is_st = _dense(observed_is_st, relative_idx, dense_length, np.bool_(False))

        signal_rows = row_index[:candidates]
        signal_present = signal_rows >= 0
        signal_name_known = name_known[:candidates]
        signal_is_st = is_st[:candidates]
        entry_present = present[1 : candidates + 1]
        entry = adj_open[1 : candidates + 1]
        entry_high = adj_high[1 : candidates + 1]
        entry_low = adj_low[1 : candidates + 1]
        entry_status = status[1 : candidates + 1]
        entry_state_known = status_known[1 : candidates + 1]
        signal_atr = atr[:candidates]
        gaps = raw_open[1 : candidates + 1] / raw_close[:candidates] - 1.0
        stop_pct = np.clip(
            ATR_STOP_MULTIPLIER * signal_atr,
            MIN_STOP,
            MAX_STOP,
        )
        stop_scale = entry * (1.0 - stop_pct)
        base = (
            signal_present
            & entry_present
            & np.isfinite(entry)
            & (entry > 0)
            & np.isfinite(entry_high)
            & np.isfinite(entry_low)
            & np.isfinite(signal_atr)
            & np.isfinite(gaps)
            & entry_state_known
            & signal_name_known
            & ~signal_is_st
        )
        entry_executable = base & (entry_status != 3) & (gaps <= ENTRY_GAP_MAX)
        present_signal_rows = signal_rows[signal_present]
        float_output["v5_stop_pct"][present_signal_rows] = stop_pct[signal_present]
        float_output["v5_entry_gap"][present_signal_rows] = gaps[signal_present]
        entry_executable_output[signal_rows[entry_executable]] = True
        counters["candidate_rows"] += int(signal_present.sum())
        counters["entry_executable_rows"] += int(entry_executable.sum())
        counters["entry_missing"] += int((signal_present & ~entry_present).sum())
        counters["entry_unbuyable"] += int((base & (entry_status == 3)).sum())
        counters["entry_gap_over_3pct"] += int(
            (base & (entry_status != 3) & (gaps > ENTRY_GAP_MAX)).sum()
        )
        counters["signal_historical_st"] += int(
            (signal_present & signal_name_known & signal_is_st).sum()
        )
        counters["signal_state_unknown"] += int(
            (signal_present & ~signal_name_known).sum()
        )

        future_open = sliding_window_view(adj_open[2:], horizon)
        future_low = sliding_window_view(adj_low[2:], horizon)
        future_present = sliding_window_view(present[2:], horizon)
        future_status = sliding_window_view(status[2:], horizon)
        future_known = (future_status >= 0) & (future_status <= 6)
        sellable = future_present & future_known & (future_status != 6)
        pending_entry_stop = entry_low <= stop_scale
        pending_event = pending_entry_stop[:, None] & sellable
        gap_stop = future_open <= stop_scale[:, None]
        intraday_stop = future_low <= stop_scale[:, None]
        regular_event = sellable & (gap_stop | intraday_stop)
        event = np.where(pending_entry_stop[:, None], pending_event, regular_event)
        event &= entry_executable[:, None]
        has_event = event.any(axis=1)
        first_event = np.argmax(event, axis=1)

        exit_scale = np.full(candidates, np.nan, dtype="float64")
        exit_relative_idx = np.full(candidates, -1, dtype="int32")
        reason = np.full(candidates, -1, dtype="int8")
        event_rows = np.flatnonzero(has_event)
        if len(event_rows):
            cols = first_event[event_rows]
            exit_relative_idx[event_rows] = event_rows + cols + 2
            pending_rows = event_rows[pending_entry_stop[event_rows]]
            if len(pending_rows):
                pending_cols = first_event[pending_rows]
                exit_scale[pending_rows] = future_open[pending_rows, pending_cols]
                reason[pending_rows] = 0
            regular_rows = event_rows[~pending_entry_stop[event_rows]]
            if len(regular_rows):
                regular_cols = first_event[regular_rows]
                use_gap = gap_stop[regular_rows, regular_cols]
                exit_scale[regular_rows[use_gap]] = future_open[
                    regular_rows[use_gap], regular_cols[use_gap]
                ]
                reason[regular_rows[use_gap]] = 1
                intraday_rows = regular_rows[~use_gap]
                exit_scale[intraday_rows] = stop_scale[intraday_rows]
                reason[intraday_rows] = 2

        no_event = entry_executable & ~has_event
        end_present = future_present[:, -1]
        end_known = future_known[:, -1]
        end_sellable = no_event & end_present & end_known & (future_status[:, -1] != 6)
        exit_scale[end_sellable] = adj_close[horizon + 1 : horizon + 1 + candidates][end_sellable]
        exit_relative_idx[end_sellable] = np.flatnonzero(end_sellable) + horizon + 1
        reason[end_sellable] = 3

        delayed_rows = np.flatnonzero(no_event & ~end_sellable)
        for row in delayed_rows:
            end_relative = int(row + horizon + 1)
            later = np.flatnonzero(
                present[end_relative + 1 :]
                & status_known[end_relative + 1 :]
                & (status[end_relative + 1 :] != 6)
            )
            if len(later):
                exit_relative = end_relative + 1 + int(later[0])
                exit_scale[row] = adj_open[exit_relative]
                exit_relative_idx[row] = exit_relative
                reason[row] = 4
                counters["delayed_time_exits"] += 1

        held_lows = np.concatenate([entry_low[:, None], future_low], axis=1)
        held_lows = np.where(np.isfinite(held_lows), held_lows, np.inf)
        prefix_low = np.minimum.accumulate(held_lows, axis=1)
        fixed_window_low = prefix_low[:, -1]
        path_min = np.full(candidates, np.nan, dtype="float64")
        missing_days = np.zeros(candidates, dtype="int32")
        unknown_state = ~entry_state_known
        missing_cumulative = np.cumsum(~future_present, axis=1, dtype="int32")
        unknown_cumulative = np.cumsum(
            future_present & ~future_known, axis=1, dtype="int32"
        )

        if len(event_rows):
            cols = first_event[event_rows]
            prior_low = prefix_low[event_rows, cols]
            path_min[event_rows] = np.minimum(prior_low, exit_scale[event_rows])
            missing_days[event_rows] = missing_cumulative[event_rows, cols]
            unknown_state[event_rows] |= unknown_cumulative[event_rows, cols] > 0
        time_rows = np.flatnonzero(end_sellable)
        if len(time_rows):
            path_min[time_rows] = prefix_low[time_rows, -1]
            missing_days[time_rows] = missing_cumulative[time_rows, -1]
            unknown_state[time_rows] |= unknown_cumulative[time_rows, -1] > 0
        for row in delayed_rows:
            exit_relative = int(exit_relative_idx[row])
            if exit_relative < 0:
                continue
            end_relative = int(row + horizon + 1)
            prior_slice = slice(end_relative + 1, exit_relative)
            later_lows = adj_low[prior_slice]
            finite_lows = later_lows[np.isfinite(later_lows)]
            minimum = float(prefix_low[row, -1])
            if len(finite_lows):
                minimum = min(minimum, float(finite_lows.min()))
            path_min[row] = min(minimum, float(exit_scale[row]))
            missing_days[row] = int(missing_cumulative[row, -1]) + int(
                (~present[prior_slice]).sum()
            )
            unknown_state[row] |= bool(
                (present[prior_slice] & ~status_known[prior_slice]).any()
            )

        has_exit = np.isfinite(exit_scale) & (exit_scale > 0) & (exit_relative_idx >= 0)
        label_valid = entry_executable & has_exit & ~unknown_state & np.isfinite(path_min)
        counters["unknown_state_paths"] += int((entry_executable & unknown_state).sum())
        counters["no_sellable_exit"] += int((entry_executable & ~has_exit).sum())
        valid_rows = np.flatnonzero(label_valid)
        if not len(valid_rows):
            continue
        destination = signal_rows[valid_rows]
        realized = (
            exit_scale[valid_rows] / entry[valid_rows] * (1.0 - sell_cost_rate)
            - 1.0
            - buy_cost_rate
        )
        tail_return = (
            path_min[valid_rows] / entry[valid_rows] * (1.0 - sell_cost_rate)
            - 1.0
            - buy_cost_rate
        )
        tail_loss = np.maximum(-tail_return, 0.0)
        fixed_b = np.maximum(
            1.0 - fixed_window_low[valid_rows] / entry[valid_rows], 0.0
        )
        float_output["v5_exit_scale"][destination] = exit_scale[valid_rows]
        float_output["v5_net_return"][destination] = realized
        float_output["v5_net_r"][destination] = realized / stop_pct[valid_rows]
        float_output["v5_tail_loss_pct"][destination] = tail_loss
        float_output["v5_tail_loss_r"][destination] = tail_loss / stop_pct[valid_rows]
        float_output["v5_fixed_window_b"][destination] = fixed_b
        exit_idx_output[destination] = exit_relative_idx[valid_rows] + first_idx
        delay_output[destination] = np.maximum(
            exit_relative_idx[valid_rows] - (valid_rows + horizon + 1), 0
        ).astype("int16")
        missing_days_output[destination] = missing_days[valid_rows].astype("int16")
        reason_output[destination] = reason[valid_rows]
        valid_output[destination] = True
        counters["valid_label_rows"] += int(len(valid_rows))

    for column, values in float_output.items():
        panel[column] = values
    panel["v5_exit_idx"] = exit_idx_output
    panel["v5_delay_sessions"] = delay_output
    panel["v5_missing_held_sessions"] = missing_days_output
    panel["v5_exit_reason_code"] = reason_output
    panel["v5_entry_executable"] = entry_executable_output
    panel["v5_label_valid"] = valid_output
    counters["delayed_time_exits"] = int(
        (reason_output[valid_output] == 4).sum()
    )
    audit = {
        "horizon": horizon,
        **counters,
        "exit_reason_counts": {
            EXIT_REASONS[code]: int((reason_output == code).sum())
            for code in EXIT_REASONS
        },
        "delayed_exit_rate": (
            float((reason_output[valid_output] == 4).mean())
            if valid_output.any()
            else None
        ),
        "labels_with_missing_held_sessions": int(
            (missing_days_output[valid_output] > 0).sum()
        ),
        "definition": (
            "T+1 open; stop=clip(1.5*ATR20,4%,8%); no profit target; "
            "H-day close or first later sellable open; costs included"
        ),
    }
    return panel, audit


def scalar_tail_outcome(
    stock: pd.DataFrame,
    signal_idx: int,
    atr20_pct: float,
    horizon: int,
    buy_cost_rate: float,
    sell_cost_rate: float,
) -> tuple[dict[str, Any] | None, str]:
    if not np.isfinite(atr20_pct):
        return None, "INVALID_ATR"
    stock = stock.set_index("trade_idx", drop=False).sort_index()

    def row_at(idx: int) -> pd.Series | None:
        if idx not in stock.index:
            return None
        row = stock.loc[idx]
        if isinstance(row, pd.DataFrame):
            raise ValueError(f"Duplicate trade_idx {idx}")
        return row

    signal = row_at(signal_idx)
    entry_idx = signal_idx + 1
    end_idx = signal_idx + horizon + 1
    entry = row_at(entry_idx)
    if signal is None or entry is None:
        return None, "ENTRY_MISSING"
    if not bool(signal["historical_state_known"]):
        return None, "UNKNOWN_HISTORICAL_NAME_STATE"
    if bool(signal["historical_is_st"]):
        return None, "HISTORICAL_ST"
    entry_status = int(entry["limit_status"])
    if entry_status not in range(7):
        return None, "UNKNOWN_LIMIT_STATUS"
    if entry_status == 3:
        return None, "ENTRY_UNBUYABLE"
    gap = float(entry["open"]) / float(signal["close"]) - 1.0
    if not np.isfinite(gap) or gap > ENTRY_GAP_MAX:
        return None, "ENTRY_GAP_OVER_3PCT"
    entry_scale = float(entry["adj_open"])
    stop_pct = float(
        np.clip(ATR_STOP_MULTIPLIER * atr20_pct, MIN_STOP, MAX_STOP)
    )
    stop_scale = entry_scale * (1.0 - stop_pct)
    pending_entry_stop = float(entry["adj_low"]) <= stop_scale
    path_min = float(entry["adj_low"])
    missing_sessions = 0
    exit_idx: int | None = None
    exit_scale: float | None = None
    reason_code = -1

    for current_idx in range(entry_idx + 1, end_idx + 1):
        current = row_at(current_idx)
        if current is None:
            missing_sessions += 1
            continue
        status = int(current["limit_status"])
        if status not in range(7):
            return None, "UNKNOWN_LIMIT_STATUS"
        sellable = status != 6
        current_open = float(current["adj_open"])
        current_low = float(current["adj_low"])
        if pending_entry_stop and sellable:
            exit_idx, exit_scale, reason_code = current_idx, current_open, 0
            path_min = min(path_min, current_open)
            break
        if sellable and current_open <= stop_scale:
            exit_idx, exit_scale, reason_code = current_idx, current_open, 1
            path_min = min(path_min, current_open)
            break
        if sellable and current_low <= stop_scale:
            exit_idx, exit_scale, reason_code = current_idx, stop_scale, 2
            path_min = min(path_min, stop_scale)
            break
        path_min = min(path_min, current_low)

    if exit_idx is None:
        end = row_at(end_idx)
        if end is not None and int(end["limit_status"]) in range(7) and int(end["limit_status"]) != 6:
            exit_idx, exit_scale, reason_code = end_idx, float(end["adj_close"]), 3
        else:
            if end is not None and int(end["limit_status"]) not in range(7):
                return None, "UNKNOWN_LIMIT_STATUS"
            latest_idx = int(stock.index.max())
            for current_idx in range(end_idx + 1, latest_idx + 1):
                current = row_at(current_idx)
                if current is None:
                    missing_sessions += 1
                    continue
                status = int(current["limit_status"])
                if status not in range(7):
                    return None, "UNKNOWN_LIMIT_STATUS"
                if status == 6:
                    path_min = min(path_min, float(current["adj_low"]))
                    continue
                exit_idx = current_idx
                exit_scale = float(current["adj_open"])
                reason_code = 4
                path_min = min(path_min, exit_scale)
                break
    if exit_idx is None or exit_scale is None:
        return None, "NO_SELLABLE_EXIT"

    fixed_lows = stock.loc[
        (stock.index >= entry_idx) & (stock.index <= end_idx), "adj_low"
    ].to_numpy(dtype=float)
    fixed_low = float(np.nanmin(fixed_lows)) if len(fixed_lows) else math.nan
    net_return = exit_scale / entry_scale * (1.0 - sell_cost_rate) - 1.0 - buy_cost_rate
    tail_return = path_min / entry_scale * (1.0 - sell_cost_rate) - 1.0 - buy_cost_rate
    tail_loss = max(-tail_return, 0.0)
    return {
        "v5_stop_pct": stop_pct,
        "v5_entry_gap": gap,
        "v5_exit_scale": exit_scale,
        "v5_exit_idx": exit_idx,
        "v5_delay_sessions": max(exit_idx - end_idx, 0),
        "v5_missing_held_sessions": missing_sessions,
        "v5_exit_reason_code": reason_code,
        "v5_net_return": net_return,
        "v5_net_r": net_return / stop_pct,
        "v5_tail_loss_pct": tail_loss,
        "v5_tail_loss_r": tail_loss / stop_pct,
        "v5_fixed_window_b": max(1.0 - fixed_low / entry_scale, 0.0),
    }, "FILLED"


def reconcile_labels(
    panel: pd.DataFrame,
    sample_size: int,
    horizon: int,
    buy_cost_rate: float,
    sell_cost_rate: float,
    seed: int = 20260817,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    valid = panel.loc[panel["v5_label_valid"]].copy()
    if valid.empty:
        return pd.DataFrame(), {"checked": 0, "passed": False, "reason": "NO_VALID_LABELS"}
    sample = valid.sample(n=min(sample_size, len(valid)), random_state=seed)
    instruments = set(sample["instrument_id"].astype(str))
    views = {
        str(instrument_id): frame.copy()
        for instrument_id, frame in panel.loc[
            panel["instrument_id"].astype(str).isin(instruments)
        ].groupby("instrument_id", sort=False)
    }
    numeric = [
        "v5_stop_pct",
        "v5_entry_gap",
        "v5_exit_scale",
        "v5_net_return",
        "v5_net_r",
        "v5_tail_loss_pct",
        "v5_tail_loss_r",
        "v5_fixed_window_b",
    ]
    exact = [
        "v5_exit_idx",
        "v5_delay_sessions",
        "v5_missing_held_sessions",
        "v5_exit_reason_code",
    ]
    rows = []
    max_error = {column: 0.0 for column in numeric}
    mismatches = 0
    for candidate in sample.itertuples(index=False):
        outcome, status = scalar_tail_outcome(
            views[str(candidate.instrument_id)],
            int(candidate.trade_idx),
            float(candidate.atr20_pct),
            horizon,
            buy_cost_rate,
            sell_cost_rate,
        )
        passed = status == "FILLED" and outcome is not None
        details: dict[str, Any] = {}
        if passed and outcome is not None:
            for column in numeric:
                observed = float(getattr(candidate, column))
                expected = float(outcome[column])
                error = abs(observed - expected)
                max_error[column] = max(max_error[column], error)
                details[f"{column}_error"] = error
                passed &= error <= 2e-6
            for column in exact:
                observed = int(getattr(candidate, column))
                expected = int(outcome[column])
                details[f"{column}_match"] = observed == expected
                passed &= observed == expected
        if not passed:
            mismatches += 1
        rows.append(
            {
                "instrument_id": candidate.instrument_id,
                "signal_date": candidate.trade_date,
                "trade_idx": int(candidate.trade_idx),
                "reference_status": status,
                "passed": bool(passed),
                **details,
            }
        )
    result = pd.DataFrame(rows)
    audit = {
        "checked": int(len(result)),
        "mismatches": mismatches,
        "passed": mismatches == 0,
        "max_absolute_errors": max_error,
        "tolerance": 2e-6,
    }
    return result, audit


def write_report(path: Path, summary: dict[str, Any]) -> None:
    label = summary.get("label_audit", {})
    reconciliation = summary.get("reconciliation", {})
    rows = [
        "# V5可执行尾部风险标签审计",
        "",
        "## 结论",
        "",
        f"**{summary['decision']}**",
        "",
        summary["decision_detail"],
        "",
    ]
    if label:
        rows.extend(
            [
                "## 标签概况",
                "",
                f"- 周期：H={label['horizon']}。",
                f"- 可执行入场：{label['entry_executable_rows']:,}；有效标签：{label['valid_label_rows']:,}。",
                f"- T日历史ST/*ST排除：{label['signal_historical_st']:,}；历史名称状态未知：{label['signal_state_unknown']:,}。",
                f"- 到期延迟退出：{label['delayed_time_exits']:,}。",
                f"- 持仓期间含缺失开市日的标签：{label['labels_with_missing_held_sessions']:,}。",
                f"- 找不到可卖退出：{label['no_sellable_exit']:,}；路径状态未知：{label['unknown_state_paths']:,}。",
                "",
                "## 逐笔对账",
                "",
                f"随机检查 {reconciliation.get('checked', 0)} 条，错配 {reconciliation.get('mismatches', 0)} 条；"
                f"结果：{'通过' if reconciliation.get('passed') else '失败'}。",
                "",
            ]
        )
    rows.extend(
        [
            "## 标签定义",
            "",
            "T日收盘产生信号，T+1开盘计划买入；止损为 `clip(1.5×ATR20,4%,8%)`。买入日触发止损时等待下一可卖开盘；否则按可卖日跳空/盘中止损。未触发止损时在第H日收盘退出；当日缺行情或一字跌停时，延伸到首个状态已知且可卖的开盘。`v5_tail_loss_pct` 计算实际持有路径最低可观察价格对应的费用后尾部损失。",
            "",
            "该标签没有止盈条件，也不使用任何模型预测，因此可作为独立风险目标；缺少历史ST、停复牌和精确涨跌停价时只能标记为研究用途。",
        ]
    )
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and reconcile V5 tail labels")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--start-year", type=int, default=2020)
    parser.add_argument("--horizon", type=int, default=20)
    parser.add_argument("--sample-size", type=int, default=200)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--state-audit", default=None)
    parser.add_argument("--allow-incomplete-state-data", action="store_true")
    parser.add_argument("--write-labels", action="store_true")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    config = load_config(resolve_from(script_dir, args.config))
    data_root = resolve_from(script_dir, str(config["data_root"]))
    preferred_output_dir = (
        Path(args.output_dir).resolve()
        if args.output_dir
        else resolve_from(script_dir, str(config["output_dir"])) / "v5_tail_label_audit"
    )
    output_dir = writable_output_dir(
        preferred_output_dir, "demo01_v5_tail_label_audit_latest"
    )
    state_audit_path = (
        Path(args.state_audit).resolve()
        if args.state_audit
        else resolve_from(script_dir, str(config["output_dir"]))
        / "data_state_audit"
        / "summary.json"
    )
    state_audit = (
        json.loads(state_audit_path.read_text(encoding="utf-8"))
        if state_audit_path.exists()
        else {"ready_for_v5_labels": False, "blockers": ["未找到历史状态审计"]}
    )
    state_ready = bool(state_audit.get("ready_for_v5_labels"))
    if not state_ready and not args.allow_incomplete_state_data:
        summary = {
            "generated_at": pd.Timestamp.now().isoformat(),
            "decision": "BLOCKED",
            "decision_detail": "历史状态数据未通过审计，未生成真实数据标签。",
            "state_audit": state_audit,
            "label_audit": {},
            "reconciliation": {},
        }
        write_json(output_dir / "summary.json", summary)
        write_report(output_dir / "report.md", summary)
        print(f"V5 tail labels blocked -> {output_dir}", flush=True)
        return 0

    panel, _, calendar, panel_audit = load_panel(
        data_root, pd.Timestamp(f"{args.start_year}-01-01")
    )
    panel, point_in_time_audit = add_point_in_time_states(panel, data_root)
    panel_audit["point_in_time_states"] = point_in_time_audit
    panel = add_atr20_pct(panel)
    panel, label_audit = add_executable_tail_labels(
        panel,
        args.horizon,
        float(config["buy_cost_rate"]),
        float(config["sell_cost_rate"]),
    )
    reconciliation_rows, reconciliation = reconcile_labels(
        panel,
        args.sample_size,
        args.horizon,
        float(config["buy_cost_rate"]),
        float(config["sell_cost_rate"]),
    )
    reconciliation_rows.to_csv(
        output_dir / "reconciliation_sample.csv", index=False, encoding="utf-8-sig"
    )
    valid = panel.loc[panel["v5_label_valid"]]
    sample_columns = [
        "instrument_id",
        "trade_date",
        "trade_idx",
        "atr20_pct",
        "v5_stop_pct",
        "v5_exit_idx",
        "v5_delay_sessions",
        "v5_missing_held_sessions",
        "v5_exit_reason_code",
        "v5_net_return",
        "v5_net_r",
        "v5_tail_loss_pct",
        "v5_tail_loss_r",
        "v5_fixed_window_b",
    ]
    valid.head(1000)[sample_columns].to_csv(
        output_dir / "label_sample.csv", index=False, encoding="utf-8-sig"
    )
    if args.write_labels:
        valid[sample_columns].to_parquet(
            output_dir / "v5_tail_labels.parquet", index=False, compression="zstd"
        )
    if not reconciliation.get("passed"):
        decision = "FAILED_RECONCILIATION"
        detail = "向量化标签与逐笔参考引擎不一致，禁止进入特征审计。"
    elif state_ready:
        decision = "READY_FOR_FEATURE_AUDIT"
        detail = "历史状态数据和逐笔对账均通过，可进入特征信息审计。"
    else:
        decision = "RESEARCH_ONLY"
        detail = "逐笔对账通过，但历史状态数据不完整；结果不能作为训练或交易验收依据。"
    summary = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "decision": decision,
        "decision_detail": detail,
        "state_data_ready": state_ready,
        "state_blockers": state_audit.get("blockers", []),
        "horizon": args.horizon,
        "panel_audit": panel_audit,
        "label_audit": label_audit,
        "reconciliation": reconciliation,
        "calendar_end": str(pd.to_datetime(calendar["calendar_date"]).max().date()),
        "labels_written": bool(args.write_labels),
    }
    write_json(output_dir / "summary.json", summary)
    write_report(output_dir / "report.md", summary)
    print(f"V5 tail label audit: {decision} -> {output_dir}", flush=True)
    return 0 if reconciliation.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
