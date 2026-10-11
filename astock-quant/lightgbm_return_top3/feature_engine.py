from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from TechnicalPatternScanner.utils import add_indicators, load_all

BASE_DIR = Path(__file__).resolve().parent.parent
UNIVERSE_CSV = BASE_DIR / "filtered_stocks.csv"
INDUSTRY_CSV = BASE_DIR / "industry_map.csv"
MARKET_FEATURE_CSV = BASE_DIR / "market_meta" / "daily_market_features.csv"
EVENT_FEATURE_CSV = BASE_DIR / "event_data" / "daily_event_features.csv"

HOLD_DAYS = 20
MIN_LIST_DAYS = 250
STALE_DAYS = 10
TEST_SAMPLE_SIZE = 120
IC_MIN_OBS = 30

TARGET_COLUMN = "fwd_ret_20d"
DRAWDOWN_TARGET_COLUMN = "fwd_drawdown_20d"
INDUSTRY_COLUMN = "industry"
INDUSTRY_CODE_COLUMN = "industry_code"
SIGNAL_ELIGIBILITY_COLUMN = "signal_eligible"
TARGET_READY_COLUMN = "target_ready"
ENTRY_EXECUTABLE_COLUMN = "entry_executable"
TURNOVER_COLUMN = "TurnoverRate"

TURNOVER_FEATURE_COLUMNS = [
    "turnover_rate",
    "turnover_ma5",
    "turnover_ma20",
    "turnover_ratio_5_20",
    "turnover_change_1d",
    "turnover_rank_60d",
]

PRICE_BEHAVIOR_FEATURE_COLUMNS = [
    "behavior_clv",
    "behavior_upper_shadow",
    "behavior_lower_shadow",
    "behavior_amplitude",
    "behavior_amplitude20",
    "behavior_volume_z20",
    "behavior_turnover_z20",
    "behavior_gap_from_high60",
    "behavior_range_width60",
    "behavior_support_reclaim",
    "behavior_failed_breakout",
    "behavior_high_volume_stall",
    "behavior_crowding_risk",
    "behavior_anchor_pressure_risk",
    "behavior_panic_reversal",
    "behavior_support_break",
    "behavior_pressure_score",
    "behavior_support_score",
    "behavior_crowding_score",
]

MARKET_META_FEATURE_COLUMNS = [
    "market_turnover_rate",
    "market_turnover_rate_f",
    "market_volume_ratio",
    "market_pe_ttm",
    "market_pb",
    "market_ps_ttm",
    "market_dv_ttm",
    "market_total_mv",
    "market_circ_mv",
    "market_total_share",
    "market_float_share",
    "market_free_share",
    "market_log_total_mv",
    "market_log_circ_mv",
    "market_log_float_share",
    "market_log_free_share",
    "market_inv_pe_ttm",
    "market_inv_pb",
    "market_inv_ps_ttm",
    "market_circ_mv_to_total_mv",
]

EVENT_FEATURE_COLUMNS = [
    "ann_count_5d",
    "ann_count_20d",
    "ann_risk_count_20d",
    "ann_positive_count_20d",
    "news_count_5d",
    "news_count_20d",
    "news_risk_count_20d",
    "news_positive_count_20d",
    "research_count_20d",
    "research_count_60d",
    "research_buy_count_60d",
]

AUXILIARY_FEATURE_COLUMNS = MARKET_META_FEATURE_COLUMNS + EVENT_FEATURE_COLUMNS

RAW_FEATURE_COLUMNS = [
    "mom_5d",
    "mom_10d",
    "mom_20d",
    "mom_60d",
    "reversal_1d",
    "ma20_over_ma60",
    "close_over_ma20",
    "close_over_ma60",
    "ma20_slope_5d",
    "ma60_slope_20d",
    "trend_quality_20d",
    "pullback_to_ma20",
    "pullback_to_ma60",
    "rsi14",
    "macd",
    "macd_hist",
    "macd_hist_slope_3d",
    "adx14",
    "di_spread",
    "atr_pct_14",
    "atr_pct_20",
    "low_vol_20d",
    "low_range_20d",
    "bb_width20",
    "bb_position20",
    "range_pos_20d",
    "range_pos_60d",
    "volume_dryup_5v20",
    "volume_dryup_20d",
    "vol_cv_20d",
    "liquidity_20d",
    "amount_mom_20d",
    *TURNOVER_FEATURE_COLUMNS,
    *AUXILIARY_FEATURE_COLUMNS,
    "gap_from_high20",
    "zscore_20d",
    *PRICE_BEHAVIOR_FEATURE_COLUMNS,
]

CROSS_FEATURE_COLUMNS = [
    "low_vol_x_low_rsi",
    "low_vol_x_pullback",
    "volume_dryup_x_reversal",
    "trend_quality_x_low_vol",
    "range_pos_x_reversal",
]

ALL_FEATURE_COLUMNS = RAW_FEATURE_COLUMNS + CROSS_FEATURE_COLUMNS


def get_feature_columns(
    include_turnover: bool = True,
    include_auxiliary: bool = True,
    include_state: bool = True,
) -> list[str]:
    """Return the model feature universe, optionally excluding turnover-rate factors."""
    excluded: set[str] = set()
    if not include_turnover:
        excluded.update(TURNOVER_FEATURE_COLUMNS)
    if not include_auxiliary:
        excluded.update(AUXILIARY_FEATURE_COLUMNS)
    if not include_state:
        excluded.update(PRICE_BEHAVIOR_FEATURE_COLUMNS)
    return [feature for feature in ALL_FEATURE_COLUMNS if feature not in excluded]


@dataclass(frozen=True)
class FeatureBuildConfig:
    sample_size: int | None = None
    sample_seed: int = 42
    workers: int = 8
    min_avg_amount: float = 20_000_000.0
    include_auxiliary_features: bool = True
    test: bool = False
    verbose: bool = True


def _safe_div(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator / denominator.replace(0, np.nan)


def _clip01(series: pd.Series) -> pd.Series:
    return series.clip(lower=0, upper=1)


def _prefix_bucket(ts_code: str) -> str:
    if ts_code.startswith(("600", "601", "603", "605")):
        return "SH_main"
    if ts_code.startswith(("000", "001", "002")):
        return "SZ_main"
    return "other"


def _build_range_position(close: pd.Series, window: int) -> pd.Series:
    rolling_min = close.rolling(window, min_periods=window).min()
    rolling_max = close.rolling(window, min_periods=window).max()
    return _safe_div(close - rolling_min, rolling_max - rolling_min)


def _rolling_zscore(close: pd.Series, window: int) -> pd.Series:
    mean = close.rolling(window, min_periods=window).mean()
    std = close.rolling(window, min_periods=window).std().replace(0, np.nan)
    return (close - mean) / std


def _load_auxiliary_features(path: Path, feature_columns: list[str]) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=feature_columns)
    df = pd.read_csv(path, dtype={"ts_code": str})
    if df.empty or "Date" not in df.columns or "ts_code" not in df.columns:
        return pd.DataFrame(columns=feature_columns)
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df["ts_code"] = df["ts_code"].astype(str)
    df = df.dropna(subset=["Date", "ts_code"])
    for column in feature_columns:
        if column not in df.columns:
            df[column] = np.nan
        df[column] = pd.to_numeric(df[column], errors="coerce")
    return (
        df[["Date", "ts_code"] + feature_columns]
        .drop_duplicates(["Date", "ts_code"], keep="last")
        .set_index(["Date", "ts_code"])
        .sort_index()
    )


def _attach_auxiliary_features(panel: pd.DataFrame, verbose: bool = True) -> pd.DataFrame:
    out = panel.copy()
    sources = [
        (MARKET_FEATURE_CSV, MARKET_META_FEATURE_COLUMNS, "market_meta"),
        (EVENT_FEATURE_CSV, EVENT_FEATURE_COLUMNS, "event_data"),
    ]
    for path, columns, label in sources:
        aux = _load_auxiliary_features(path, columns)
        if not aux.empty:
            out = out.join(aux, how="left")
            if verbose:
                matched = int(out[columns].notna().any(axis=1).sum())
                print(f"[feature_engine] 已拼接 {label}: rows_with_data={matched}", flush=True)
        elif verbose:
            print(f"[feature_engine] 未发现 {label} 辅助特征文件：{path}", flush=True)
        for column in columns:
            if column not in out.columns:
                out[column] = np.nan
    return out


def load_universe_metadata(
    sample_size: int | None = None,
    sample_seed: int = 42,
    test: bool = False,
) -> pd.DataFrame:
    meta = pd.read_csv(UNIVERSE_CSV, encoding="utf-8-sig")
    meta["ts_code"] = meta["ts_code"].astype(str)
    meta["name"] = meta.get("name", "").fillna("").astype(str)
    meta["list_date"] = pd.to_datetime(
        meta.get("list_date"),
        format="%Y%m%d",
        errors="coerce",
    )

    industry = pd.read_csv(INDUSTRY_CSV)
    industry["ts_code"] = industry["ts_code"].astype(str)
    industry["industry"] = industry["industry"].fillna("").astype(str)

    merged = meta.merge(industry[["ts_code", "industry"]], on="ts_code", how="left")
    merged["industry"] = merged["industry"].replace("", np.nan)
    merged["industry"] = merged.apply(
        lambda row: row["industry"] if pd.notna(row["industry"]) else _prefix_bucket(row["ts_code"]),
        axis=1,
    )

    if test and sample_size is None:
        sample_size = TEST_SAMPLE_SIZE
    if sample_size is not None:
        sample_n = min(sample_size, len(merged))
        merged = merged.sample(n=sample_n, random_state=sample_seed)

    merged = merged.sort_values("ts_code").reset_index(drop=True)
    return merged


def build_market_calendar(price_panel: pd.DataFrame) -> pd.DatetimeIndex:
    return price_panel.index.get_level_values("Date").unique().sort_values()


def _trade_dates_from_positions(
    positions: np.ndarray,
    trading_dates: pd.DatetimeIndex,
    offset: int,
) -> pd.DatetimeIndex:
    values = np.full(len(positions), np.datetime64("NaT"), dtype="datetime64[ns]")
    valid = (positions >= 0) & (positions + offset < len(trading_dates))
    values[valid] = trading_dates.values[positions[valid] + offset]
    return pd.DatetimeIndex(values)


def _compute_single_stock_frame(
    ts_code: str,
    df: pd.DataFrame,
    meta_row: pd.Series,
    trading_dates: pd.DatetimeIndex,
    latest_market_date: pd.Timestamp,
    min_avg_amount: float,
) -> pd.DataFrame | None:
    if df.empty:
        return None
    if (latest_market_date - df.index.max()).days > STALE_DAYS:
        return None

    enriched = add_indicators(df)
    close = enriched["Close"].astype(float)
    high = enriched["High"].astype(float)
    low = enriched["Low"].astype(float)
    volume = enriched["Volume"].astype(float)
    if TURNOVER_COLUMN in enriched.columns:
        turnover = enriched[TURNOVER_COLUMN].astype(float).replace([np.inf, -np.inf], np.nan)
    else:
        turnover = pd.Series(np.nan, index=enriched.index, dtype=float)
    ret_1d = close.pct_change()

    amount = close * volume
    avg_amount_20d = amount.rolling(20, min_periods=20).mean()
    abs_path_20d = ret_1d.abs().rolling(20, min_periods=20).sum().replace(0, np.nan)
    bb_range = (enriched["BB_upper"] - enriched["BB_lower"]).replace(0, np.nan)

    frame = pd.DataFrame(index=enriched.index)
    frame["signal_close"] = close
    frame["signal_volume"] = volume
    frame["avg_amount_20d"] = avg_amount_20d
    frame["amount"] = amount
    frame["name"] = str(meta_row.get("name", ""))
    frame[INDUSTRY_COLUMN] = str(meta_row.get(INDUSTRY_COLUMN, _prefix_bucket(ts_code)))

    frame["mom_5d"] = close.pct_change(5)
    frame["mom_10d"] = close.pct_change(10)
    frame["mom_20d"] = close.pct_change(20)
    frame["mom_60d"] = close.pct_change(60)
    frame["reversal_1d"] = -ret_1d
    frame["ma20_over_ma60"] = _safe_div(enriched["MA20"], enriched["MA60"]) - 1.0
    frame["close_over_ma20"] = _safe_div(close, enriched["MA20"]) - 1.0
    frame["close_over_ma60"] = _safe_div(close, enriched["MA60"]) - 1.0
    frame["ma20_slope_5d"] = _safe_div(enriched["MA20"], enriched["MA20"].shift(5)) - 1.0
    frame["ma60_slope_20d"] = _safe_div(enriched["MA60"], enriched["MA60"].shift(20)) - 1.0
    frame["trend_quality_20d"] = _safe_div(close.pct_change(20).abs(), abs_path_20d).clip(0, 1)
    frame["pullback_to_ma20"] = _safe_div(enriched["MA20"] - close, enriched["MA20"])
    frame["pullback_to_ma60"] = _safe_div(enriched["MA60"] - close, enriched["MA60"])
    frame["rsi14"] = enriched["RSI14"]
    frame["macd"] = enriched["MACD"]
    frame["macd_hist"] = enriched["MACD_hist"]
    frame["macd_hist_slope_3d"] = enriched["MACD_hist"] - enriched["MACD_hist"].shift(3)
    frame["adx14"] = enriched["ADX14"]
    frame["di_spread"] = enriched["DI_plus14"] - enriched["DI_minus14"]
    frame["atr_pct_14"] = _safe_div(enriched["ATR14"], close)
    frame["atr_pct_20"] = _safe_div(enriched["ATR20"], close)
    frame["low_vol_20d"] = -ret_1d.rolling(20, min_periods=20).std()
    frame["low_range_20d"] = -_safe_div(high - low, close).rolling(20, min_periods=20).mean()
    frame["bb_width20"] = enriched["BB_width20"]
    frame["bb_position20"] = _safe_div(close - enriched["BB_lower"], bb_range)
    frame["range_pos_20d"] = _build_range_position(close, 20)
    frame["range_pos_60d"] = _build_range_position(close, 60)
    frame["volume_dryup_5v20"] = -_safe_div(volume.rolling(5, min_periods=5).mean(), volume.rolling(20, min_periods=20).mean())
    frame["volume_dryup_20d"] = -_safe_div(volume, enriched["Vol_MA20"])
    frame["vol_cv_20d"] = _safe_div(volume.rolling(20, min_periods=20).std(), volume.rolling(20, min_periods=20).mean())
    frame["liquidity_20d"] = np.log1p(avg_amount_20d)
    frame["amount_mom_20d"] = _safe_div(avg_amount_20d, avg_amount_20d.shift(20)) - 1.0
    turnover_ma5 = turnover.rolling(5, min_periods=5).mean()
    turnover_ma20 = turnover.rolling(20, min_periods=20).mean()
    frame["turnover_rate"] = turnover
    frame["turnover_ma5"] = turnover_ma5
    frame["turnover_ma20"] = turnover_ma20
    frame["turnover_ratio_5_20"] = _safe_div(turnover_ma5, turnover_ma20) - 1.0
    frame["turnover_change_1d"] = turnover - turnover.shift(1)
    frame["turnover_rank_60d"] = turnover.rolling(60, min_periods=20).rank(pct=True)
    frame["gap_from_high20"] = _safe_div(close, high.rolling(20, min_periods=20).max()) - 1.0
    frame["zscore_20d"] = _rolling_zscore(close, 20)

    prev_high20 = high.shift(1).rolling(20, min_periods=20).max()
    prev_low20 = low.shift(1).rolling(20, min_periods=20).min()
    high60 = high.rolling(60, min_periods=60).max()
    low60 = low.rolling(60, min_periods=60).min()
    daily_range = (high - low).replace(0, np.nan)
    clv = _safe_div(close - low, daily_range)
    upper_shadow = _safe_div(high - np.maximum(enriched["Open"], close), daily_range)
    lower_shadow = _safe_div(np.minimum(enriched["Open"], close) - low, daily_range)
    amplitude = _safe_div(high - low, close)
    volume_ma20 = volume.rolling(20, min_periods=20).mean()
    volume_std20 = volume.rolling(20, min_periods=20).std(ddof=0)
    volume_z20 = _safe_div(volume - volume_ma20, volume_std20)
    turnover_std20 = turnover.rolling(20, min_periods=8).std(ddof=0)
    turnover_z20 = _safe_div(turnover - turnover.rolling(20, min_periods=8).mean(), turnover_std20)
    gap_from_high60 = _safe_div(close, high60) - 1.0
    range_width60 = _safe_div(high60, low60) - 1.0

    support_reclaim = ((low < prev_low20) & (close > prev_low20)).astype(float)
    failed_breakout = (
        (high > prev_high20 * 1.005)
        & (close < prev_high20)
        & (clv < 0.5)
    ).astype(float)
    high_volume_stall = (
        (frame["range_pos_60d"] >= 0.75)
        & (volume_z20 >= 1.0)
        & (clv <= 0.45)
        & (upper_shadow >= 0.20)
    ).astype(float)
    crowding_risk = (
        (frame["mom_5d"] >= 0.12)
        & (frame["range_pos_60d"] >= 0.75)
        & ((volume_z20 >= 1.0) | (turnover_z20 >= 1.0))
    ).astype(float)
    anchor_pressure_risk = (
        (gap_from_high60 >= -0.03)
        & (clv < 0.50)
        & (upper_shadow >= 0.20)
    ).astype(float)
    panic_reversal = (
        (
            (low < prev_low20 * 0.995)
            & (close > prev_low20)
            & (lower_shadow >= 0.25)
            & (volume_z20 >= 0.5)
        )
        | (
            (ret_1d <= -0.06)
            & (lower_shadow >= 0.30)
            & (clv >= 0.45)
            & (volume_z20 >= 0.8)
        )
    ).astype(float)
    support_break = ((close < prev_low20) & (volume_z20 >= 0.5)).astype(float)

    frame["behavior_clv"] = clv
    frame["behavior_upper_shadow"] = upper_shadow
    frame["behavior_lower_shadow"] = lower_shadow
    frame["behavior_amplitude"] = amplitude
    frame["behavior_amplitude20"] = amplitude.rolling(20, min_periods=20).mean()
    frame["behavior_volume_z20"] = volume_z20
    frame["behavior_turnover_z20"] = turnover_z20
    frame["behavior_gap_from_high60"] = gap_from_high60
    frame["behavior_range_width60"] = range_width60
    frame["behavior_support_reclaim"] = support_reclaim
    frame["behavior_failed_breakout"] = failed_breakout
    frame["behavior_high_volume_stall"] = high_volume_stall
    frame["behavior_crowding_risk"] = crowding_risk
    frame["behavior_anchor_pressure_risk"] = anchor_pressure_risk
    frame["behavior_panic_reversal"] = panic_reversal
    frame["behavior_support_break"] = support_break
    frame["behavior_pressure_score"] = (
        0.30 * _clip01(frame["range_pos_60d"])
        + 0.20 * _clip01(upper_shadow)
        + 0.20 * _clip01(1 - clv)
        + 0.15 * _clip01(volume_z20 / 3.0)
        + 0.15 * (gap_from_high60 > -0.05).astype(float)
    )
    frame["behavior_support_score"] = (
        0.30 * _clip01(lower_shadow)
        + 0.25 * _clip01(clv)
        + 0.25 * support_reclaim
        + 0.20 * _clip01(frame["range_pos_20d"])
    )
    frame["behavior_crowding_score"] = (
        0.25 * _clip01(frame["mom_5d"] / 0.20)
        + 0.20 * _clip01(volume_z20 / 3.0)
        + 0.20 * _clip01(turnover_z20 / 3.0)
        + 0.20 * _clip01(frame["behavior_amplitude20"] / 0.12)
        + 0.15 * _clip01(frame["range_pos_60d"])
    )

    _reversal_5d = -close.pct_change(5)
    _low_rsi14 = 100.0 - enriched["RSI14"]
    frame["low_vol_x_low_rsi"] = frame["low_vol_20d"] * _low_rsi14
    frame["low_vol_x_pullback"] = frame["low_vol_20d"] * frame["pullback_to_ma20"]
    frame["volume_dryup_x_reversal"] = frame["volume_dryup_5v20"] * _reversal_5d
    frame["trend_quality_x_low_vol"] = frame["trend_quality_20d"] * frame["low_vol_20d"]
    frame["range_pos_x_reversal"] = frame["range_pos_60d"] * _reversal_5d

    date_positions = trading_dates.get_indexer(frame.index)
    entry_dates = _trade_dates_from_positions(date_positions, trading_dates, 1)
    exit_dates = _trade_dates_from_positions(date_positions, trading_dates, HOLD_DAYS)

    entry_open = pd.Series(enriched["Open"].reindex(entry_dates).to_numpy(dtype=float), index=frame.index)
    entry_volume = pd.Series(enriched["Volume"].reindex(entry_dates).to_numpy(dtype=float), index=frame.index)
    entry_close = pd.Series(enriched["Close"].reindex(entry_dates).to_numpy(dtype=float), index=frame.index)
    exit_close = pd.Series(enriched["Close"].reindex(exit_dates).to_numpy(dtype=float), index=frame.index)

    signal_close_np = close.to_numpy(dtype=float)
    list_date = meta_row.get("list_date")
    if pd.isna(list_date):
        listed_days = np.full(len(frame), 0.0, dtype=float)
    else:
        listed_days = (frame.index - pd.Timestamp(list_date)).days.astype(float)

    next_open_ret = entry_open / signal_close_np - 1.0
    fwd_ret_hold = exit_close / entry_open - 1.0

    frame["entry_date"] = entry_dates
    frame["exit_date"] = exit_dates
    frame["entry_open_1d"] = entry_open
    frame["entry_volume_1d"] = entry_volume
    frame["entry_limit_up_1d"] = next_open_ret >= 0.095
    frame[TARGET_COLUMN] = fwd_ret_hold

    # Forward minimum close during holding window: reindex to trading calendar,
    # then compute min(close[t+1..t+HOLD_DAYS]) via shifted rolling min.
    close_td = close.reindex(trading_dates)
    fwd_min_td = (
        close_td.shift(-1)
        .rolling(window=HOLD_DAYS, min_periods=HOLD_DAYS)
        .min()
        .shift(-(HOLD_DAYS - 1))
    )
    fwd_min_close = pd.Series(
        fwd_min_td.reindex(frame.index).to_numpy(dtype=float), index=frame.index
    )
    frame[DRAWDOWN_TARGET_COLUMN] = _safe_div(fwd_min_close - entry_open, entry_open)

    frame["listed_days"] = listed_days
    signal_eligible = (
        (listed_days >= MIN_LIST_DAYS)
        & frame["avg_amount_20d"].fillna(0).ge(min_avg_amount)
        & frame["signal_volume"].fillna(0).gt(0)
        & frame["signal_close"].fillna(0).gt(0)
    )
    target_ready = (
        signal_eligible
        & pd.notna(frame["entry_date"])
        & pd.notna(frame["exit_date"])
        & entry_open.map(np.isfinite)
        & exit_close.map(np.isfinite)
        & (entry_open > 0)
        & (exit_close > 0)
    )
    entry_executable = (
        target_ready
        & entry_volume.map(np.isfinite)
        & (entry_volume > 0)
        & (~frame["entry_limit_up_1d"].fillna(True))
    )
    frame[SIGNAL_ELIGIBILITY_COLUMN] = signal_eligible
    frame[TARGET_READY_COLUMN] = target_ready
    frame["live_eligible"] = signal_eligible
    frame[ENTRY_EXECUTABLE_COLUMN] = entry_executable

    frame["ts_code"] = ts_code
    frame.index.name = "Date"
    frame = frame.reset_index().set_index(["Date", "ts_code"]).sort_index()

    float_cols = frame.select_dtypes(include=["float64"]).columns
    if len(float_cols):
        frame[float_cols] = frame[float_cols].astype(np.float32)
    return frame


def build_feature_panel(
    config: FeatureBuildConfig,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DatetimeIndex]:
    metadata = load_universe_metadata(
        sample_size=config.sample_size,
        sample_seed=config.sample_seed,
        test=config.test,
    )

    price_panel = load_all(
        ts_codes=metadata["ts_code"].tolist(),
        workers=config.workers,
        verbose=config.verbose,
    )
    if price_panel.empty:
        raise RuntimeError("No price data loaded from daily_data.")

    trading_dates = build_market_calendar(price_panel)
    latest_market_date = trading_dates.max()
    metadata_idx = metadata.set_index("ts_code")

    codes = metadata["ts_code"].tolist()
    total = len(codes)
    if config.verbose:
        print(f"[feature_engine] 构建特征面板 {total} 只股票...", flush=True)

    # Pre-extract per-stock DataFrames sequentially to avoid concurrent MultiIndex access
    stock_dfs: dict[str, pd.DataFrame] = {}
    for ts_code in codes:
        if ts_code not in metadata_idx.index:
            continue
        try:
            stock_dfs[ts_code] = price_panel.loc[ts_code]
        except KeyError:
            pass

    def _process(ts_code: str) -> pd.DataFrame | None:
        return _compute_single_stock_frame(
            ts_code=ts_code,
            df=stock_dfs[ts_code],
            meta_row=metadata_idx.loc[ts_code],
            trading_dates=trading_dates,
            latest_market_date=latest_market_date,
            min_avg_amount=config.min_avg_amount,
        )

    parts: list[pd.DataFrame] = []
    completed = 0
    n_queued = len(stock_dfs)
    with ThreadPoolExecutor(max_workers=config.workers) as executor:
        futures = {executor.submit(_process, ts_code): ts_code for ts_code in stock_dfs}
        for future in as_completed(futures):
            completed += 1
            part = future.result()
            if part is not None and not part.empty:
                parts.append(part)
            if config.verbose and (completed % 200 == 0 or completed == n_queued):
                print(f"[feature_engine] 已处理 {completed}/{total}", flush=True)

    if not parts:
        raise RuntimeError("Feature panel is empty after preprocessing.")

    panel = pd.concat(parts).sort_index()
    if config.include_auxiliary_features:
        panel = _attach_auxiliary_features(panel, verbose=config.verbose)
    else:
        for column in AUXILIARY_FEATURE_COLUMNS:
            if column not in panel.columns:
                panel[column] = np.nan
    industry_codes = pd.Categorical(panel[INDUSTRY_COLUMN])
    panel[INDUSTRY_CODE_COLUMN] = industry_codes.codes.astype(np.int16)
    return panel, metadata, trading_dates


def winsorize_quantile(series: pd.Series, lower: float = 0.01, upper: float = 0.99) -> pd.Series:
    valid = series.dropna()
    if valid.empty:
        return series
    lo, hi = valid.quantile([lower, upper])
    return series.clip(lower=lo, upper=hi)


def rank_to_unit_interval(series: pd.Series) -> pd.Series:
    valid = series.dropna()
    if valid.empty:
        return series
    return series.rank(method="average", pct=True)


def winsorize_and_rank(series: pd.Series) -> pd.Series:
    return rank_to_unit_interval(winsorize_quantile(series))


def rank_feature_panel(panel: pd.DataFrame, feature_columns: list[str]) -> pd.DataFrame:
    out = panel.copy()
    for column in feature_columns:
        out[column] = out.groupby(level="Date")[column].transform(winsorize_and_rank)
    float_cols = out[feature_columns].select_dtypes(include=["float64"]).columns
    if len(float_cols):
        out[float_cols] = out[float_cols].astype(np.float32)
    return out


def spearman_ic(frame: pd.DataFrame, factor: str, target: str = TARGET_COLUMN, min_obs: int = IC_MIN_OBS) -> float:
    sub = frame[[factor, target]].dropna()
    if len(sub) < min_obs or sub[factor].nunique() < 5 or sub[target].nunique() < 5:
        return np.nan
    factor_rank = sub[factor].rank(method="average")
    target_rank = sub[target].rank(method="average")
    return float(factor_rank.corr(target_rank))


def compute_ic_summary(
    panel: pd.DataFrame,
    feature_columns: list[str],
    target: str = TARGET_COLUMN,
    min_obs: int = IC_MIN_OBS,
) -> pd.DataFrame:
    daily_ics: dict[str, list[float]] = {feature: [] for feature in feature_columns}
    for _, date_frame in panel.groupby(level="Date", sort=True):
        frame = date_frame.reset_index(level="Date", drop=True)
        for feature in feature_columns:
            ic = spearman_ic(frame, feature, target=target, min_obs=min_obs)
            if pd.notna(ic):
                daily_ics[feature].append(float(ic))

    rows: list[dict[str, float | int | str]] = []
    for feature in feature_columns:
        values = daily_ics.get(feature, [])
        if not values:
            rows.append(
                {
                    "feature": feature,
                    "obs": 0,
                    "mean_ic": np.nan,
                    "std_ic": np.nan,
                    "ic_ir": np.nan,
                    "positive_ratio": np.nan,
                    "abs_mean_ic": np.nan,
                }
            )
            continue

        arr = np.asarray(values, dtype=float)
        mean_ic = float(np.nanmean(arr))
        std_ic = float(np.nanstd(arr, ddof=1)) if len(arr) > 1 else np.nan
        rows.append(
            {
                "feature": feature,
                "obs": int(len(arr)),
                "mean_ic": mean_ic,
                "std_ic": std_ic,
                "ic_ir": mean_ic / std_ic if pd.notna(std_ic) and std_ic > 0 else np.nan,
                "positive_ratio": float(np.mean(arr > 0)),
                "abs_mean_ic": abs(mean_ic),
            }
        )

    return pd.DataFrame(rows).sort_values("abs_mean_ic", ascending=False).reset_index(drop=True)


def select_candidate_features(
    ic_summary: pd.DataFrame,
    min_abs_ic: float = 0.005,
    min_keep: int = 30,
) -> list[str]:
    ranked = ic_summary.sort_values("abs_mean_ic", ascending=False)
    filtered = ranked[ranked["abs_mean_ic"].fillna(0) >= min_abs_ic]["feature"].tolist()
    if len(filtered) >= min_keep:
        return filtered
    return ranked.head(min_keep)["feature"].tolist()


def prune_correlated_features(
    panel: pd.DataFrame,
    feature_columns: list[str],
    ic_summary: pd.DataFrame,
    corr_threshold: float = 0.90,
    min_keep: int = 30,
) -> list[str]:
    ranked = ic_summary.set_index("feature")
    ordered = [feature for feature in ranked.index.tolist() if feature in feature_columns]
    if not ordered:
        return []

    corr_np = panel[ordered].corr().abs().fillna(0.0).values
    feature_to_idx = {f: i for i, f in enumerate(ordered)}
    selected: list[str] = []
    for feature in ordered:
        if len(selected) < min_keep:
            selected.append(feature)
            continue
        fi = feature_to_idx[feature]
        selected_indices = [feature_to_idx[k] for k in selected]
        if any(corr_np[fi, si] > corr_threshold for si in selected_indices):
            continue
        selected.append(feature)
    return selected


def build_rebalance_dates(
    trading_dates: pd.DatetimeIndex,
    start_date: str | pd.Timestamp | None = None,
    end_date: str | pd.Timestamp | None = None,
    step: int = HOLD_DAYS,
) -> pd.DatetimeIndex:
    dates = trading_dates
    if start_date is not None:
        dates = dates[dates >= pd.Timestamp(start_date)]
    if end_date is not None:
        dates = dates[dates <= pd.Timestamp(end_date)]
    return dates[::step]


def resolve_signal_date(trading_dates: pd.DatetimeIndex, trade_date: str | pd.Timestamp) -> pd.Timestamp:
    target = pd.Timestamp(trade_date)
    valid = trading_dates[trading_dates <= target]
    if len(valid) == 0:
        raise ValueError(f"No trading date found on or before {target.date()}.")
    return valid.max()
