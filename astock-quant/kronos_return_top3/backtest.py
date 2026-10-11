from __future__ import annotations

import argparse
import math
import os
import random
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from tqdm.auto import tqdm

from TechnicalPatternScanner.utils import load_one, save_result_csv

from .regime_gate import (
    REGIME_ACTION_FAST_RISK,
    REGIME_ACTION_MINI_ONLY,
    REGIME_ACTION_NO_TRADE,
    RegimeGateConfig,
    RegimeGateResult,
    build_regime_markdown,
    evaluate_regime_gate,
)
from .vendor_model import Kronos, KronosPredictor, KronosTokenizer

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent
OUTPUT_DIR = PACKAGE_DIR / "output"
STOCK_LIST_CSV = REPO_ROOT / "filtered_stocks.csv"

MODEL_SOURCE_URL = "https://github.com/shiyu-coder/Kronos"
DEFAULT_MODEL_NAME = "NeoQuasar/Kronos-base"
DEFAULT_TOKENIZER_NAME = "NeoQuasar/Kronos-Tokenizer-base"
SMALL_MODEL_NAME = "NeoQuasar/Kronos-small"
FAST_MODEL_NAME = "NeoQuasar/Kronos-mini"
FAST_TOKENIZER_NAME = "NeoQuasar/Kronos-Tokenizer-2k"
RANKING_MINI_ONLY = "mini_only"
RANKING_FAST_RISK = "fast_risk"
FAST_RISK_WEIGHTS = {
    "pred": 0.65,
    "win": 0.10,
    "recent_return": 0.10,
    "drawdown": 0.10,
    "volatility": 0.05,
}


@dataclass(frozen=True)
class BacktestConfig:
    start_date: str = "2025-01-01"
    hold_days: int = 10
    lookback: int = 512
    max_context: int = 512
    sample_size: int | None = 20
    full_universe: bool = False
    max_periods: int | None = None
    sample_seed: int = 42
    top_k: int = 3
    batch_size: int = 16
    device: str = "cpu"
    model_name: str = DEFAULT_MODEL_NAME
    tokenizer_name: str = DEFAULT_TOKENIZER_NAME
    temperature: float = 1.0
    forecast_top_k: int = 1
    forecast_top_p: float = 1.0
    sample_count: int = 1
    price_limit_rate: float = 0.10
    ranking_mode: str = RANKING_MINI_ONLY
    fast_risk_lookback: int = 60
    fast_risk_candidate_pool: int = 50
    regime_gate: bool = False
    regime_skip_threshold: float = 45.0
    regime_mini_threshold: float = 80.0
    regime_hard_veto: bool = True
    regime_lookback: int = 120
    output_prefix: str = "backtest"
    predict_only: bool = False
    verbose: bool = True
    stock_list_path: str | None = None
    signal_date_override: str | None = None


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.backends.cudnn.is_available():
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False


def _fmt_pct(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):.2%}"


def _fmt_float(value: float | int | None, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):.{digits}f}"


def _safe_prefix(prefix: str) -> str:
    text = prefix.strip().replace(" ", "_")
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in text) or "backtest"


def _progress(
    iterable,
    *,
    total: int | None = None,
    desc: str,
    unit: str,
    enabled: bool,
    leave: bool = True,
):
    return tqdm(
        iterable,
        total=total,
        desc=desc,
        unit=unit,
        disable=not enabled,
        dynamic_ncols=True,
        leave=leave,
    )


def select_universe(config: BacktestConfig) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    csv_path = Path(config.stock_list_path) if config.stock_list_path else STOCK_LIST_CSV
    meta = pd.read_csv(csv_path, encoding="utf-8-sig")
    available_codes = {path.stem for path in (REPO_ROOT / "daily_data").glob("*.csv")}
    meta = meta[meta["ts_code"].isin(available_codes)].copy()
    if meta.empty:
        raise RuntimeError("No overlap between filtered_stocks.csv and daily_data/.")

    if config.full_universe or config.sample_size is None:
        meta = meta.sort_values("ts_code").reset_index(drop=True)
    else:
        meta = meta.sample(frac=1.0, random_state=config.sample_seed).reset_index(drop=True)

    min_rows = config.lookback + config.hold_days + 5
    selected_rows: list[dict] = []
    stock_data: dict[str, pd.DataFrame] = {}

    rows_iter = _progress(
        meta.itertuples(index=False),
        total=len(meta),
        desc="[kronos] load universe",
        unit="stock",
        enabled=config.verbose,
    )
    for row in rows_iter:
        ts_code = str(row.ts_code)
        df = load_one(ts_code, end_date=config.signal_date_override)
        if df is None or len(df) < min_rows:
            continue
        if df.index.max() < pd.Timestamp(config.start_date):
            continue

        stock_data[ts_code] = df.sort_index()
        selected_rows.append({"ts_code": ts_code, "name": getattr(row, "name", "")})

        if not config.full_universe and config.sample_size is not None and len(selected_rows) >= config.sample_size:
            break

    if not stock_data:
        raise RuntimeError("No stocks satisfy the lookback / hold-days requirement.")

    selected = pd.DataFrame(selected_rows).sort_values("ts_code").reset_index(drop=True)
    stock_data = {code: stock_data[code] for code in selected["ts_code"]}
    return selected, stock_data


def build_trading_dates(stock_data: dict[str, pd.DataFrame]) -> pd.DatetimeIndex:
    all_dates: set[pd.Timestamp] = set()
    for df in stock_data.values():
        all_dates.update(pd.to_datetime(df.index).tolist())
    if not all_dates:
        raise RuntimeError("No trading dates found in the selected universe.")
    return pd.DatetimeIndex(sorted(all_dates))


def load_predictor(config: BacktestConfig) -> KronosPredictor:
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    if config.verbose:
        print(f"[kronos] load tokenizer={config.tokenizer_name}")
    tokenizer = KronosTokenizer.from_pretrained(config.tokenizer_name)
    if config.verbose:
        print(f"[kronos] load model={config.model_name}")
    model = Kronos.from_pretrained(config.model_name)
    tokenizer.eval()
    model.eval()
    return KronosPredictor(
        model=model,
        tokenizer=tokenizer,
        device=config.device,
        max_context=config.max_context,
    )


def to_kronos_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = (
        df.reset_index()
        .rename(
            columns={
                "Date": "timestamp",
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
            }
        )
        .copy()
    )
    out["amount"] = out["close"] * out["volume"]
    return out[["timestamp", "open", "high", "low", "close", "volume", "amount"]]


def apply_a_share_price_limits(
    pred_df: pd.DataFrame,
    last_close: float,
    limit_rate: float,
) -> pd.DataFrame:
    capped = pred_df.copy()
    ohlc_cols = ["open", "high", "low", "close"]

    for idx in capped.index:
        limit_up = last_close * (1.0 + limit_rate)
        limit_down = last_close * (1.0 - limit_rate)

        for col in ohlc_cols:
            value = float(capped.at[idx, col])
            capped.at[idx, col] = float(np.clip(value, limit_down, limit_up))

        day_open = float(capped.at[idx, "open"])
        day_close = float(capped.at[idx, "close"])
        clipped_high = float(capped.at[idx, "high"])
        clipped_low = float(capped.at[idx, "low"])
        capped.at[idx, "high"] = max(day_open, day_close, clipped_high, clipped_low)
        capped.at[idx, "low"] = min(day_open, day_close, clipped_high, clipped_low)
        last_close = day_close

    return capped


def _compute_path_stats(path_dfs: list[pd.DataFrame], last_close: float, price_limit_rate: float) -> dict:
    """Aggregate per-path OHLCV DataFrames into distribution statistics."""
    path_rets: list[float] = []
    path_max_rets: list[float] = []
    path_drawdowns: list[float] = []
    pred_entries: list[float] = []
    pred_exits: list[float] = []
    pred_min_closes: list[float] = []
    pred_max_closes: list[float] = []

    for path_df in path_dfs:
        df = apply_a_share_price_limits(path_df.copy(), last_close, price_limit_rate)
        entry = float(df["open"].iloc[0])
        if entry <= 0:
            continue
        exit_p = float(df["close"].iloc[-1])
        max_c = float(df["close"].max())
        min_c = float(df["close"].min())
        path_rets.append(exit_p / entry - 1.0)
        path_max_rets.append(max_c / entry - 1.0)
        path_drawdowns.append(min_c / entry - 1.0)
        pred_entries.append(entry)
        pred_exits.append(exit_p)
        pred_min_closes.append(min_c)
        pred_max_closes.append(max_c)

    if not path_rets:
        return {
            "predicted_entry_open": np.nan,
            "predicted_exit_close": np.nan,
            "predicted_return": np.nan,
            "predicted_min_close": np.nan,
            "predicted_max_close": np.nan,
            "path_win_prob": np.nan,
            "path_p10_return": np.nan,
            "path_p90_return": np.nan,
            "path_median_drawdown": np.nan,
            "path_reward_risk": np.nan,
            "path_count": 0,
        }

    reward_risks = [
        mr / max(abs(dd), 0.001)
        for mr, dd in zip(path_max_rets, path_drawdowns)
    ]
    return {
        "predicted_entry_open": round(float(np.median(pred_entries)), 4),
        "predicted_exit_close": round(float(np.median(pred_exits)), 4),
        "predicted_return": round(float(np.median(path_rets)), 6),
        "predicted_min_close": round(float(np.median(pred_min_closes)), 4),
        "predicted_max_close": round(float(np.median(pred_max_closes)), 4),
        "path_win_prob": round(float(np.mean([r > 0 for r in path_rets])), 4),
        "path_p10_return": round(float(np.percentile(path_rets, 10)), 6),
        "path_p90_return": round(float(np.percentile(path_rets, 90)), 6),
        "path_median_drawdown": round(float(np.median(path_drawdowns)), 6),
        "path_reward_risk": round(float(np.median(reward_risks)), 4),
        "path_count": len(path_rets),
    }


def _run_prediction_chunk(
    predictor: KronosPredictor,
    chunk_codes: list[str],
    chunk_frames: list[pd.DataFrame],
    chunk_x_timestamps: list[pd.Series],
    y_timestamps: list[pd.Series],
    last_closes: list[float],
    signal_date: pd.Timestamp,
    config: BacktestConfig,
) -> list[dict]:
    use_paths = config.sample_count > 1

    if use_paths:
        pred_paths = predictor.predict_batch(
            df_list=chunk_frames,
            x_timestamp_list=chunk_x_timestamps,
            y_timestamp_list=y_timestamps,
            pred_len=config.hold_days,
            T=config.temperature,
            top_k=config.forecast_top_k,
            top_p=config.forecast_top_p,
            sample_count=config.sample_count,
            verbose=False,
            return_paths=True,
        )
        rows: list[dict] = []
        for ts_code, path_dfs, last_close in zip(chunk_codes, pred_paths, last_closes, strict=False):
            stats = _compute_path_stats(path_dfs, last_close, config.price_limit_rate)
            rows.append({"signal_date": signal_date.date(), "ts_code": ts_code, "signal_close": round(last_close, 4), **stats})
        return rows

    pred_dfs = predictor.predict_batch(
        df_list=chunk_frames,
        x_timestamp_list=chunk_x_timestamps,
        y_timestamp_list=y_timestamps,
        pred_len=config.hold_days,
        T=config.temperature,
        top_k=config.forecast_top_k,
        top_p=config.forecast_top_p,
        sample_count=config.sample_count,
        verbose=False,
    )
    rows = []
    for ts_code, pred_df, last_close in zip(chunk_codes, pred_dfs, last_closes, strict=False):
        pred_df = apply_a_share_price_limits(pred_df.copy(), last_close, config.price_limit_rate)
        pred_entry = float(pred_df["open"].iloc[0])
        pred_exit = float(pred_df["close"].iloc[-1])
        pred_ret = pred_exit / pred_entry - 1.0 if pred_entry > 0 else np.nan
        rows.append(
            {
                "signal_date": signal_date.date(),
                "ts_code": ts_code,
                "signal_close": round(last_close, 4),
                "predicted_entry_open": round(pred_entry, 4),
                "predicted_exit_close": round(pred_exit, 4),
                "predicted_return": round(pred_ret, 6) if pd.notna(pred_ret) else np.nan,
                "predicted_min_close": round(float(pred_df["close"].min()), 4),
                "predicted_max_close": round(float(pred_df["close"].max()), 4),
            }
        )
    return rows


def forecast_cross_section(
    predictor: KronosPredictor,
    stock_data: dict[str, pd.DataFrame],
    signal_date: pd.Timestamp,
    future_dates: pd.DatetimeIndex,
    config: BacktestConfig,
) -> pd.DataFrame:
    batch_codes: list[str] = []
    batch_frames: list[pd.DataFrame] = []
    batch_x_timestamps: list[pd.Series] = []
    batch_y_timestamps: list[pd.Series] = []
    last_closes: list[float] = []
    rows: list[dict] = []

    with tqdm(
        total=len(stock_data),
        desc=f"[kronos] predict {signal_date.date()}",
        unit="stock",
        disable=not config.verbose,
        dynamic_ncols=True,
        leave=False,
    ) as pbar:
        for ts_code, df in stock_data.items():
            hist = df[df.index <= signal_date]
            if hist.empty or hist.index[-1] != signal_date or len(hist) < config.lookback:
                pbar.update(1)
                continue

            hist = hist.tail(config.lookback).copy()
            kronos_df = to_kronos_frame(hist)
            batch_codes.append(ts_code)
            batch_frames.append(kronos_df[["open", "high", "low", "close", "volume", "amount"]].reset_index(drop=True))
            batch_x_timestamps.append(kronos_df["timestamp"].reset_index(drop=True))
            batch_y_timestamps.append(pd.Series(future_dates))
            last_closes.append(float(hist["Close"].iloc[-1]))

            if len(batch_codes) >= config.batch_size:
                batch_n = len(batch_codes)
                rows.extend(
                    _run_prediction_chunk(
                        predictor=predictor,
                        chunk_codes=batch_codes,
                        chunk_frames=batch_frames,
                        chunk_x_timestamps=batch_x_timestamps,
                        y_timestamps=batch_y_timestamps,
                        last_closes=last_closes,
                        signal_date=signal_date,
                        config=config,
                    )
                )
                pbar.update(batch_n)
                pbar.set_postfix(predicted=len(rows), refresh=False)
                batch_codes, batch_frames, batch_x_timestamps, batch_y_timestamps, last_closes = [], [], [], [], []

        if batch_codes:
            batch_n = len(batch_codes)
            rows.extend(
                _run_prediction_chunk(
                    predictor=predictor,
                    chunk_codes=batch_codes,
                    chunk_frames=batch_frames,
                    chunk_x_timestamps=batch_x_timestamps,
                    y_timestamps=batch_y_timestamps,
                    last_closes=last_closes,
                    signal_date=signal_date,
                    config=config,
                )
            )
            pbar.update(batch_n)
            pbar.set_postfix(predicted=len(rows), refresh=False)

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("predicted_return", ascending=False).reset_index(drop=True)


def _percentile_score(values: pd.Series, higher_is_better: bool = True) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan)
    if numeric.notna().sum() == 0:
        return pd.Series(0.5, index=values.index, dtype=float)
    numeric = numeric.fillna(float(numeric.median()))
    return numeric.rank(method="average", pct=True, ascending=higher_is_better).astype(float)


def _fast_risk_metrics_for_stock(
    df: pd.DataFrame,
    signal_date: pd.Timestamp,
    lookback: int,
) -> dict[str, float]:
    hist = df[df.index <= signal_date].tail(max(int(lookback), 2) + 1).copy()
    if len(hist) < 2:
        return {
            "recent_return": np.nan,
            "recent_win_rate": np.nan,
            "recent_volatility": np.nan,
            "recent_max_drawdown": np.nan,
            "recent_atr_pct": np.nan,
        }

    close = pd.to_numeric(hist["Close"], errors="coerce")
    returns = close.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    if returns.empty:
        win_rate = np.nan
        volatility = np.nan
    else:
        win_rate = float((returns > 0).mean())
        volatility = float(returns.std(ddof=0))

    valid_close = close.dropna()
    if len(valid_close) >= 2 and float(valid_close.iloc[0]) > 0:
        recent_return = float(valid_close.iloc[-1] / valid_close.iloc[0] - 1.0)
        drawdown = valid_close / valid_close.cummax() - 1.0
        max_drawdown_value = float(drawdown.min())
    else:
        recent_return = np.nan
        max_drawdown_value = np.nan

    range_pct = (
        (pd.to_numeric(hist["High"], errors="coerce") - pd.to_numeric(hist["Low"], errors="coerce"))
        / close.replace(0, np.nan)
    )
    atr_pct = float(range_pct.replace([np.inf, -np.inf], np.nan).mean())

    return {
        "recent_return": recent_return,
        "recent_win_rate": win_rate,
        "recent_volatility": volatility,
        "recent_max_drawdown": max_drawdown_value,
        "recent_atr_pct": atr_pct,
    }


def apply_selection_ranking(
    forecasts: pd.DataFrame,
    stock_data: dict[str, pd.DataFrame],
    signal_date: pd.Timestamp,
    config: BacktestConfig,
) -> pd.DataFrame:
    if forecasts.empty:
        return forecasts

    ranked = forecasts.sort_values("predicted_return", ascending=False).reset_index(drop=True).copy()
    ranked["mini_rank"] = np.arange(1, len(ranked) + 1)

    if config.ranking_mode != RANKING_FAST_RISK:
        ranked["ranking_mode"] = RANKING_MINI_ONLY
        ranked["ranking_score"] = ranked["predicted_return"]
        ranked["selection_rank"] = ranked["mini_rank"]
        ranked["prediction_rank"] = ranked["selection_rank"]
        return ranked

    metrics = []
    for ts_code in ranked["ts_code"].astype(str):
        df = stock_data.get(ts_code)
        if df is None:
            metrics.append(
                {
                    "recent_return": np.nan,
                    "recent_win_rate": np.nan,
                    "recent_volatility": np.nan,
                    "recent_max_drawdown": np.nan,
                    "recent_atr_pct": np.nan,
                }
            )
        else:
            metrics.append(_fast_risk_metrics_for_stock(df, signal_date, config.fast_risk_lookback))

    metrics_df = pd.DataFrame(metrics, index=ranked.index)
    ranked = pd.concat([ranked, metrics_df], axis=1)
    ranked["fast_pred_score"] = _percentile_score(ranked["predicted_return"], higher_is_better=True)
    ranked["fast_win_score"] = _percentile_score(ranked["recent_win_rate"], higher_is_better=True)
    ranked["fast_return_score"] = _percentile_score(ranked["recent_return"], higher_is_better=True)
    ranked["fast_drawdown_score"] = _percentile_score(ranked["recent_max_drawdown"], higher_is_better=True)
    ranked["fast_volatility_score"] = _percentile_score(ranked["recent_volatility"], higher_is_better=False)
    ranked["fast_risk_score"] = (
        FAST_RISK_WEIGHTS["pred"] * ranked["fast_pred_score"]
        + FAST_RISK_WEIGHTS["win"] * ranked["fast_win_score"]
        + FAST_RISK_WEIGHTS["recent_return"] * ranked["fast_return_score"]
        + FAST_RISK_WEIGHTS["drawdown"] * ranked["fast_drawdown_score"]
        + FAST_RISK_WEIGHTS["volatility"] * ranked["fast_volatility_score"]
    ).round(6)
    ranked["ranking_mode"] = RANKING_FAST_RISK
    ranked["ranking_score"] = ranked["fast_risk_score"]

    pool_size = int(config.fast_risk_candidate_pool)
    if pool_size <= 0:
        pool_size = len(ranked)
    pool_size = min(max(pool_size, config.top_k), len(ranked))
    pool = ranked.head(pool_size).sort_values(
        ["fast_risk_score", "predicted_return"],
        ascending=[False, False],
    )
    rest = ranked.iloc[pool_size:]
    ranked = pd.concat([pool, rest], ignore_index=True)
    ranked["selection_rank"] = np.arange(1, len(ranked) + 1)
    ranked["prediction_rank"] = ranked["selection_rank"]
    return ranked


def calc_trade_window(
    df: pd.DataFrame,
    entry_date: pd.Timestamp,
    exit_date: pd.Timestamp,
) -> dict | None:
    if entry_date not in df.index or exit_date not in df.index:
        return None

    entry_price = float(df.loc[entry_date, "Open"])
    exit_price = float(df.loc[exit_date, "Close"])
    if entry_price <= 0 or pd.isna(entry_price) or pd.isna(exit_price):
        return None

    path = df.loc[(df.index >= entry_date) & (df.index <= exit_date)]
    trade_return = exit_price / entry_price - 1.0
    max_runup = float(path["High"].max() / entry_price - 1.0)
    max_drawdown = float(path["Low"].min() / entry_price - 1.0)

    return {
        "entry_price": entry_price,
        "exit_price": exit_price,
        "trade_return": float(trade_return),
        "max_runup": max_runup,
        "max_drawdown": max_drawdown,
    }


def compute_benchmark(
    stock_data: dict[str, pd.DataFrame],
    universe_codes: list[str],
    entry_date: pd.Timestamp,
    exit_date: pd.Timestamp,
) -> float:
    rets: list[float] = []
    for ts_code in universe_codes:
        res = calc_trade_window(stock_data[ts_code], entry_date, exit_date)
        if res is not None:
            rets.append(res["trade_return"])
    return float(np.mean(rets)) if rets else np.nan


def max_drawdown(equity_curve: pd.Series) -> float:
    if equity_curve.empty:
        return np.nan
    curve = pd.concat([pd.Series([1.0]), equity_curve.reset_index(drop=True)], ignore_index=True)
    running_max = curve.cummax()
    drawdown = curve / running_max - 1.0
    return float(drawdown.min())


def build_summary(
    periods_df: pd.DataFrame,
    config: BacktestConfig,
    universe_size: int,
) -> pd.DataFrame:
    if periods_df.empty:
        return pd.DataFrame()

    period_returns = periods_df["portfolio_return"].fillna(0.0)
    benchmark_returns = periods_df["benchmark_return"].fillna(0.0)
    equity_curve = (1.0 + period_returns).cumprod()
    benchmark_curve = (1.0 + benchmark_returns).cumprod()

    compound_return = float(equity_curve.iloc[-1] - 1.0)
    benchmark_compound = float(benchmark_curve.iloc[-1] - 1.0)
    avg_return = float(period_returns.mean())
    avg_alpha = float(periods_df["alpha"].mean())
    volatility = float(period_returns.std(ddof=0))
    annual_factor = math.sqrt(252.0 / config.hold_days)
    sharpe = float(avg_return / volatility * annual_factor) if volatility > 0 else np.nan

    total_trade_days = max(len(periods_df) * config.hold_days, 1)
    annualized_return = (
        float((1.0 + compound_return) ** (252.0 / total_trade_days) - 1.0)
        if compound_return > -1.0
        else np.nan
    )

    summary_row = {
        "model_name": config.model_name,
        "tokenizer_name": config.tokenizer_name,
        "source_url": MODEL_SOURCE_URL,
        "backtest_start": str(periods_df["signal_date"].min()),
        "backtest_end": str(periods_df["exit_date"].max()),
        "hold_days": config.hold_days,
        "lookback": config.lookback,
        "top_k": config.top_k,
        "ranking_mode": config.ranking_mode,
        "fast_risk_lookback": config.fast_risk_lookback,
        "fast_risk_candidate_pool": config.fast_risk_candidate_pool,
        "sample_size": universe_size,
        "full_universe": config.full_universe,
        "n_periods": len(periods_df),
        "avg_candidates": round(float(periods_df["n_candidates"].mean()), 2),
        "avg_execution_rate": round(float(periods_df["execution_rate"].mean()), 6),
        "win_rate": round(float((period_returns > 0).mean()), 6),
        "avg_portfolio_return": round(avg_return, 6),
        "avg_alpha": round(avg_alpha, 6),
        "compound_return": round(compound_return, 6),
        "benchmark_compound_return": round(benchmark_compound, 6),
        "annualized_return": round(float(annualized_return), 6) if pd.notna(annualized_return) else np.nan,
        "max_drawdown": round(max_drawdown(equity_curve), 6),
        "sharpe": round(float(sharpe), 6) if pd.notna(sharpe) else np.nan,
        "output_prefix": config.output_prefix,
    }
    return pd.DataFrame([summary_row])


def build_markdown_report(
    summary_df: pd.DataFrame,
    latest_forecasts: pd.DataFrame,
    periods_df: pd.DataFrame,
    config: BacktestConfig,
) -> str:
    if summary_df.empty or latest_forecasts.empty or periods_df.empty:
        return "# Kronos Top3 Backtest Report\n\nNo results were generated.\n"

    summary = summary_df.iloc[0]
    latest_signal = pd.to_datetime(latest_forecasts["signal_date"].iloc[0])
    latest_period = periods_df[periods_df["signal_date"] == latest_signal.date()].head(1)
    universe_label = "full universe" if bool(summary["full_universe"]) else f"sample universe ({int(summary['sample_size'])} stocks)"

    lines: list[str] = []
    lines.append(f"# Kronos Top3 Backtest Report - {summary['backtest_end']}")
    lines.append("")
    lines.append(f"- Upstream source: {MODEL_SOURCE_URL}")
    lines.append(f"- Model: `{summary['model_name']}`")
    lines.append(f"- Tokenizer: `{summary['tokenizer_name']}`")
    lines.append(f"- Universe: {universe_label}")
    lines.append(f"- Lookback: {int(summary['lookback'])}")
    lines.append(f"- Hold days: {int(summary['hold_days'])}")
    lines.append(f"- Ranking mode: `{summary['ranking_mode']}`")
    if summary["ranking_mode"] == RANKING_FAST_RISK:
        lines.append(
            f"- Fast risk: rerank Mini top {int(summary['fast_risk_candidate_pool'])} "
            f"with {int(summary['fast_risk_lookback'])}-day recent risk metrics"
        )
    lines.append(f"- Rebalance rule: every {int(summary['hold_days'])} trading days, Top{int(summary['top_k'])} equal weight, missing execution stays in cash")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Backtest range: {summary['backtest_start']} ~ {summary['backtest_end']}")
    lines.append(f"- Rebalance periods: {int(summary['n_periods'])}")
    lines.append(f"- Average candidates: {_fmt_float(summary['avg_candidates'])}")
    lines.append(f"- Average execution rate: {_fmt_pct(summary['avg_execution_rate'])}")
    lines.append(f"- Win rate: {_fmt_pct(summary['win_rate'])}")
    lines.append(f"- Average period return: {_fmt_pct(summary['avg_portfolio_return'])}")
    lines.append(f"- Average alpha: {_fmt_pct(summary['avg_alpha'])}")
    lines.append(f"- Compound return: {_fmt_pct(summary['compound_return'])}")
    lines.append(f"- Benchmark compound return: {_fmt_pct(summary['benchmark_compound_return'])}")
    lines.append(f"- Annualized return: {_fmt_pct(summary['annualized_return'])}")
    lines.append(f"- Max drawdown: {_fmt_pct(summary['max_drawdown'])}")
    lines.append(f"- Sharpe: {_fmt_float(summary['sharpe'])}")
    lines.append("")
    lines.append("## Latest Top3")
    lines.append("")
    lines.append(f"- Signal date: {latest_signal.strftime('%Y-%m-%d')}")
    if not latest_period.empty:
        row = latest_period.iloc[0]
        lines.append(f"- Entry date: {row['entry_date']}")
        lines.append(f"- Exit date: {row['exit_date']}")
        lines.append(f"- Portfolio return: {_fmt_pct(row['portfolio_return'])}")
        lines.append(f"- Alpha: {_fmt_pct(row['alpha'])}")
    lines.append("")
    if "fast_risk_score" in latest_forecasts.columns:
        lines.append("| Rank | Mini rank | Code | Fast risk score | Predicted return | Volatility | Max drawdown | Signal close | Predicted exit close |")
        lines.append("| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |")
        for idx, (_, row) in enumerate(latest_forecasts.head(config.top_k).iterrows(), start=1):
            lines.append(
                f"| {idx} | {_fmt_float(row.get('mini_rank'), 0)} | {row['ts_code']} | "
                f"{_fmt_float(row.get('fast_risk_score'), 4)} | {_fmt_pct(row['predicted_return'])} | "
                f"{_fmt_pct(row.get('recent_volatility'))} | {_fmt_pct(row.get('recent_max_drawdown'))} | "
                f"{_fmt_float(row['signal_close'])} | {_fmt_float(row['predicted_exit_close'])} |"
            )
    else:
        lines.append("| Rank | Code | Predicted return | Signal close | Predicted entry open | Predicted exit close |")
        lines.append("| --- | --- | ---: | ---: | ---: | ---: |")
        for idx, (_, row) in enumerate(latest_forecasts.head(config.top_k).iterrows(), start=1):
            lines.append(
                f"| {idx} | {row['ts_code']} | {_fmt_pct(row['predicted_return'])} | "
                f"{_fmt_float(row['signal_close'])} | {_fmt_float(row['predicted_entry_open'])} | {_fmt_float(row['predicted_exit_close'])} |"
            )
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append("- Input data comes from `daily_data/*.csv`, with `amount = close * volume` filled locally.")
    lines.append("- Predicted OHLC is clipped with an A-share +/-10% daily price limit path.")
    lines.append("- `mini_only` ranks by Mini predicted return; `fast_risk` keeps one Mini pass and reranks the Mini candidate pool by recent win rate, return, volatility, and drawdown.")
    lines.append("- This backtest uses zero-shot inference unless you point `--model-name` / `--tokenizer-name` to a local fine-tuned checkpoint.")
    lines.append("")
    return "\n".join(lines) + "\n"


def _regime_gate_config(config: BacktestConfig) -> RegimeGateConfig:
    return RegimeGateConfig(
        skip_threshold=config.regime_skip_threshold,
        mini_only_threshold=config.regime_mini_threshold,
        hard_veto=config.regime_hard_veto,
        lookback=config.regime_lookback,
    )


def _save_regime_gate_result(result: RegimeGateResult, prefix: str) -> Path:
    path = OUTPUT_DIR / f"{prefix}_regime_gate_latest.csv"
    save_result_csv(pd.DataFrame([result.to_record()]), str(path))
    return path


def _empty_prediction_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "signal_date",
            "ts_code",
            "name",
            "predicted_return",
            "ranking_mode",
            "ranking_score",
            "selection_rank",
        ]
    )


def _append_regime_gate_section(lines: list[str], result: RegimeGateResult, config: BacktestConfig) -> None:
    veto_reasons = "; ".join(result.veto_reasons) if result.veto_reasons else "-"
    lines += [
        "",
        "## Regime Gate",
        "",
        f"- Action: `{result.action}`",
        f"- Adapt score: {_fmt_float(result.adapt_score)}",
        f"- Thresholds: skip < {_fmt_float(config.regime_skip_threshold)}, mini_only >= {_fmt_float(config.regime_mini_threshold)}",
        f"- Hard veto: `{result.hard_veto}`",
        f"- Veto reasons: {veto_reasons}",
        "",
        "| Component | Score |",
        "| --- | ---: |",
        f"| Trend | {_fmt_float(result.trend_score)} |",
        f"| Breadth | {_fmt_float(result.breadth_score)} |",
        f"| Risk | {_fmt_float(result.risk_score)} |",
        f"| Dispersion | {_fmt_float(result.dispersion_score)} |",
        f"| Liquidity | {_fmt_float(result.liquidity_score)} |",
    ]


def _warn_runtime(config: BacktestConfig, universe_size: int, trading_dates: pd.DatetimeIndex) -> None:
    if config.device.startswith("cpu") and config.full_universe and config.model_name == DEFAULT_MODEL_NAME:
        n_periods = max(0, (len(trading_dates) - int(trading_dates.searchsorted(pd.Timestamp(config.start_date))) - config.hold_days) // config.hold_days)
        print(
            "[kronos] warning: CPU-only + full universe + Kronos-base is extremely slow. "
            f"Universe={universe_size}, estimated rebalance periods~{n_periods}. "
            "Use --max-periods for a smoke test or switch to --use-mini / GPU for practical iteration."
        )


def run_backtest(config: BacktestConfig) -> dict[str, object]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    set_seed(config.sample_seed)

    universe_df, stock_data = select_universe(config)
    trading_dates = build_trading_dates(stock_data)
    _warn_runtime(config, len(universe_df), trading_dates)
    predictor = load_predictor(config)

    start_idx = int(trading_dates.searchsorted(pd.Timestamp(config.start_date)))
    if start_idx >= len(trading_dates) - config.hold_days:
        raise RuntimeError("start_date is too late to form a complete backtest window.")

    if config.verbose:
        print(f"[kronos] universe size: {len(universe_df)}")
        print(f"[kronos] trading range: {trading_dates.min().date()} -> {trading_dates.max().date()}")

    periods: list[dict] = []
    trades: list[dict] = []
    latest_forecasts = pd.DataFrame()
    code_to_name = dict(zip(universe_df["ts_code"], universe_df["name"]))
    period_indices = list(range(start_idx, len(trading_dates) - config.hold_days, config.hold_days))
    if config.max_periods is not None:
        period_indices = period_indices[: config.max_periods]

    for idx in _progress(
        period_indices,
        total=len(period_indices),
        desc="[kronos] backtest",
        unit="period",
        enabled=config.verbose,
    ):
        signal_date = trading_dates[idx]
        future_dates = trading_dates[idx + 1 : idx + 1 + config.hold_days]
        if len(future_dates) < config.hold_days:
            break

        entry_date = future_dates[0]
        exit_date = future_dates[-1]

        if config.verbose:
            tqdm.write(
                f"[kronos] signal={signal_date.date()} entry={entry_date.date()} exit={exit_date.date()}",
            )

        forecasts = forecast_cross_section(
            predictor=predictor,
            stock_data=stock_data,
            signal_date=signal_date,
            future_dates=future_dates,
            config=config,
        )
        if forecasts.empty:
            continue

        forecasts["name"] = forecasts["ts_code"].map(code_to_name).fillna("")
        forecasts = apply_selection_ranking(forecasts, stock_data, signal_date, config)
        latest_forecasts = forecasts.copy()

        requested = forecasts.head(config.top_k).copy()
        executed_codes: list[str] = []
        skipped_codes: list[str] = []
        portfolio_return = 0.0

        benchmark_return = compute_benchmark(
            stock_data=stock_data,
            universe_codes=list(stock_data.keys()),
            entry_date=entry_date,
            exit_date=exit_date,
        )

        for _, row in requested.iterrows():
            ts_code = str(row["ts_code"])
            res = calc_trade_window(stock_data[ts_code], entry_date, exit_date)
            if res is None:
                skipped_codes.append(ts_code)
                continue

            weight = 1.0 / config.top_k
            contribution = res["trade_return"] * weight
            portfolio_return += contribution
            executed_codes.append(ts_code)
            trades.append(
                {
                    "signal_date": signal_date.date(),
                    "entry_date": entry_date.date(),
                    "exit_date": exit_date.date(),
                    "ts_code": ts_code,
                    "name": row["name"],
                    "prediction_rank": int(row["prediction_rank"]),
                    "predicted_return": row["predicted_return"],
                    "entry_price": round(res["entry_price"], 4),
                    "exit_price": round(res["exit_price"], 4),
                    "trade_return": round(res["trade_return"], 6),
                    "portfolio_weight": round(weight, 6),
                    "return_contribution": round(contribution, 6),
                    "max_runup": round(res["max_runup"], 6),
                    "max_drawdown": round(res["max_drawdown"], 6),
                }
            )

        execution_rate = len(executed_codes) / config.top_k if config.top_k > 0 else 0.0
        alpha = portfolio_return - benchmark_return if pd.notna(benchmark_return) else np.nan
        periods.append(
            {
                "signal_date": signal_date.date(),
                "entry_date": entry_date.date(),
                "exit_date": exit_date.date(),
                "n_candidates": len(forecasts),
                "requested_holdings": config.top_k,
                "holdings": len(executed_codes),
                "skipped_holdings": config.top_k - len(executed_codes),
                "execution_rate": round(execution_rate, 6),
                "invested_weight": round(execution_rate, 6),
                "portfolio_return": round(portfolio_return, 6),
                "benchmark_return": round(float(benchmark_return), 6) if pd.notna(benchmark_return) else np.nan,
                "alpha": round(float(alpha), 6) if pd.notna(alpha) else np.nan,
                "avg_requested_predicted_return": round(float(requested["predicted_return"].mean()), 6),
                "executed_codes": ",".join(executed_codes),
                "skipped_codes": ",".join(skipped_codes),
            }
        )

    periods_df = pd.DataFrame(periods)
    trades_df = pd.DataFrame(trades)
    summary_df = build_summary(periods_df, config, len(universe_df))

    prefix = _safe_prefix(config.output_prefix)
    report_path = OUTPUT_DIR / f"kronos_top3_{pd.Timestamp.today().strftime('%Y-%m-%d')}_{prefix}.md"
    report_text = build_markdown_report(summary_df, latest_forecasts, periods_df, config)
    report_path.write_text(report_text, encoding="utf-8")

    save_result_csv(summary_df, str(OUTPUT_DIR / f"{prefix}_summary.csv"))
    save_result_csv(periods_df, str(OUTPUT_DIR / f"{prefix}_periods.csv"))
    save_result_csv(trades_df, str(OUTPUT_DIR / f"{prefix}_trades.csv"))
    save_result_csv(latest_forecasts, str(OUTPUT_DIR / f"{prefix}_predictions_latest.csv"))

    return {
        "summary": summary_df,
        "periods": periods_df,
        "trades": trades_df,
        "predictions_latest": latest_forecasts,
        "report_path": report_path,
        "universe": universe_df,
    }


def predict_latest(config: BacktestConfig) -> dict[str, object]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    set_seed(config.sample_seed)

    universe_df, stock_data = select_universe(config)
    trading_dates = build_trading_dates(stock_data)

    signal_date = trading_dates[-1]
    future_dates = pd.bdate_range(
        start=signal_date + pd.Timedelta(days=1),
        periods=config.hold_days,
    )
    prefix = _safe_prefix(config.output_prefix)
    today_str = pd.Timestamp.today().strftime("%Y-%m-%d")
    report_path = OUTPUT_DIR / f"kronos_top3_{today_str}_{prefix}.md"
    regime_result: RegimeGateResult | None = None
    regime_path: Path | None = None
    effective_config = config

    if config.regime_gate:
        regime_result = evaluate_regime_gate(stock_data, signal_date, _regime_gate_config(config))
        regime_path = _save_regime_gate_result(regime_result, prefix)
        if config.verbose:
            print(
                "[kronos] regime gate: "
                f"action={regime_result.action}, score={regime_result.adapt_score:.2f}, "
                f"hard_veto={regime_result.hard_veto}"
            )
        if regime_result.action == REGIME_ACTION_NO_TRADE:
            report_path.write_text(
                build_regime_markdown(
                    regime_result,
                    f"Kronos Top{config.top_k} No-Trade - {today_str}",
                    _regime_gate_config(config),
                ),
                encoding="utf-8",
            )
            forecasts = _empty_prediction_frame()
            save_result_csv(forecasts, str(OUTPUT_DIR / f"{prefix}_predictions_latest.csv"))
            return {
                "predictions": forecasts,
                "report_path": report_path,
                "regime_path": regime_path,
                "universe": universe_df,
                "signal_date": signal_date,
                "regime_gate": regime_result,
                "skipped": True,
            }
        if regime_result.action in {REGIME_ACTION_FAST_RISK, REGIME_ACTION_MINI_ONLY}:
            effective_config = replace(config, ranking_mode=regime_result.action)

    predictor = load_predictor(effective_config)

    if config.verbose:
        print(
            f"[kronos] predict-only: signal={signal_date.date()}, "
            f"entry={future_dates[0].date()}, exit={future_dates[-1].date()}"
        )

    forecasts = forecast_cross_section(
        predictor=predictor,
        stock_data=stock_data,
        signal_date=signal_date,
        future_dates=future_dates,
        config=effective_config,
    )
    if forecasts.empty:
        raise RuntimeError("No forecasts generated.")

    code_to_name = dict(zip(universe_df["ts_code"], universe_df["name"]))
    forecasts["name"] = forecasts["ts_code"].map(code_to_name).fillna("")
    forecasts = apply_selection_ranking(forecasts, stock_data, signal_date, effective_config)

    top_rows = forecasts.head(effective_config.top_k)
    lines: list[str] = [
        f"# Kronos Top{effective_config.top_k} 推荐 - {today_str}",
        "",
        f"- 模型: `{effective_config.model_name}`",
        f"- 分词器: `{effective_config.tokenizer_name}`",
        f"- 信号日: {signal_date.strftime('%Y-%m-%d')}",
        f"- 预测入场: {future_dates[0].strftime('%Y-%m-%d')}",
        f"- 预测离场: {future_dates[-1].strftime('%Y-%m-%d')}（{effective_config.hold_days} 个工作日）",
        f"- 候选股数: {len(forecasts)}",
        f"- 排名模式: `{effective_config.ranking_mode}`",
        "",
    ]
    if regime_result is not None:
        _append_regime_gate_section(lines, regime_result, config)
        lines.append("")
    lines += [
        f"## Top{effective_config.top_k} 推荐",
        "",
    ]

    if "fast_risk_score" in forecasts.columns:
        lines += [
            "| 排名 | Mini排名 | 代码 | 名称 | 快速风险分 | 预测收益 | 近端波动 | 近端回撤 | 预测末日收盘 |",
            "| --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
        for rank, (_, row) in enumerate(top_rows.iterrows(), start=1):
            lines.append(
                f"| {rank} | {_fmt_float(row.get('mini_rank'), 0)} | {row['ts_code']} | {row.get('name', '')} | "
                f"{_fmt_float(row.get('fast_risk_score'), 4)} | {_fmt_pct(row['predicted_return'])} | "
                f"{_fmt_pct(row.get('recent_volatility'))} | {_fmt_pct(row.get('recent_max_drawdown'))} | "
                f"{_fmt_float(row['predicted_exit_close'])} |"
            )
    else:
        lines += [
            "| 排名 | 代码 | 名称 | 预测收益 | 信号日收盘 | 预测首日开盘 | 预测末日收盘 |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: |",
        ]
        for rank, (_, row) in enumerate(top_rows.iterrows(), start=1):
            lines.append(
                f"| {rank} | {row['ts_code']} | {row.get('name', '')} | {_fmt_pct(row['predicted_return'])} | "
                f"{_fmt_float(row['signal_close'])} | {_fmt_float(row['predicted_entry_open'])} | {_fmt_float(row['predicted_exit_close'])} |"
            )
    lines += [
        "",
        "## 全候选排序",
        "",
    ]
    if "fast_risk_score" in forecasts.columns:
        lines += [
            "| 排名 | Mini排名 | 代码 | 名称 | 快速风险分 | 预测收益 |",
            "| --- | ---: | --- | --- | ---: | ---: |",
        ]
        for rank, (_, row) in enumerate(forecasts.iterrows(), start=1):
            lines.append(
                f"| {rank} | {_fmt_float(row.get('mini_rank'), 0)} | {row['ts_code']} | {row.get('name', '')} | "
                f"{_fmt_float(row.get('fast_risk_score'), 4)} | {_fmt_pct(row['predicted_return'])} |"
            )
    else:
        lines += [
            "| 排名 | 代码 | 名称 | 预测收益 |",
            "| --- | --- | --- | ---: |",
        ]
        for rank, (_, row) in enumerate(forecasts.iterrows(), start=1):
            lines.append(f"| {rank} | {row['ts_code']} | {row.get('name', '')} | {_fmt_pct(row['predicted_return'])} |")
    lines += [
        "",
        "## 说明",
        "",
        "- 推断模式（`--predict-only`），无历史回测指标。",
        "- 未来时间戳基于自然工作日生成，不依赖真实未来价格。",
        "- `mini_only` 直接按 Mini 预测收益排序；`fast_risk` 只跑一次 Mini，并在 Mini 候选池内叠加近期胜率、收益、波动和回撤做快速重排。",
        "- 零样本推理，未针对 A 股日线数据微调。",
        "",
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    save_result_csv(forecasts, str(OUTPUT_DIR / f"{prefix}_predictions_latest.csv"))

    return {
        "predictions": forecasts,
        "report_path": report_path,
        "regime_path": regime_path,
        "universe": universe_df,
        "signal_date": signal_date,
        "regime_gate": regime_result,
        "skipped": False,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Kronos Top3 backtest on repo daily_data samples")
    parser.add_argument("--start-date", type=str, default="2025-01-01", help="Signal start date, format YYYY-MM-DD")
    parser.add_argument("--hold-days", type=int, default=10, help="Holding days and forecast horizon")
    parser.add_argument("--lookback", type=int, default=512, help="Model lookback window")
    parser.add_argument("--max-context", type=int, default=None, help="Kronos max context, default equals lookback")
    parser.add_argument("--sample-size", type=int, default=20, help="Sample stock count from filtered_stocks.csv; <=0 means all")
    parser.add_argument("--full-universe", action="store_true", help="Use the full available universe instead of sampling")
    parser.add_argument("--max-periods", type=int, default=None, help="Only run the first N rebalance periods")
    parser.add_argument("--sample-seed", type=int, default=42, help="Sample seed")
    parser.add_argument("--top-k", type=int, default=3, help="Portfolio holdings per rebalance")
    parser.add_argument("--batch-size", type=int, default=16, help="Batch size for cross-sectional inference")
    parser.add_argument("--device", type=str, default="cpu", help="Inference device, e.g. cpu / cuda:0")
    parser.add_argument("--model-name", type=str, default=DEFAULT_MODEL_NAME, help="Hugging Face model name or local path")
    parser.add_argument("--tokenizer-name", type=str, default=DEFAULT_TOKENIZER_NAME, help="Hugging Face tokenizer name or local path")
    parser.add_argument("--use-small", action="store_true", help="Switch to Kronos-small + Kronos-Tokenizer-base (24.7M)")
    parser.add_argument("--use-mini", action="store_true", help="Switch to Kronos-mini + Tokenizer-2k for faster smoke tests")
    parser.add_argument("--predict-only", action="store_true", help="Inference-only: predict from latest available trading date without requiring a complete hold-period window")
    parser.add_argument("--temperature", type=float, default=1.0, help="Sampling temperature")
    parser.add_argument("--forecast-top-k", type=int, default=1, help="Token-level top-k during generation")
    parser.add_argument("--forecast-top-p", type=float, default=1.0, help="Token-level top-p during generation")
    parser.add_argument("--sample-count", type=int, default=1, help="Forecast path sample count")
    parser.add_argument("--price-limit-rate", type=float, default=0.10, help="A-share daily price limit rate")
    parser.add_argument(
        "--ranking-mode",
        choices=[RANKING_MINI_ONLY, RANKING_FAST_RISK],
        default=RANKING_MINI_ONLY,
        help="Selection ranking after prediction: mini_only or fast_risk",
    )
    parser.add_argument("--fast-risk-lookback", type=int, default=60, help="Recent trading days used by fast_risk ranking")
    parser.add_argument("--fast-risk-candidate-pool", type=int, default=50, help="Mini top-N pool reranked by fast_risk; <=0 means all")
    parser.add_argument("--regime-gate", action="store_true", help="Run market regime gate before predict-only inference")
    parser.add_argument("--regime-skip-threshold", type=float, default=45.0, help="Regime score below this skips prediction")
    parser.add_argument("--regime-mini-threshold", type=float, default=80.0, help="Regime score at/above this uses mini_only; otherwise fast_risk")
    parser.add_argument("--regime-lookback", type=int, default=120, help="Trading days used by the regime gate")
    parser.add_argument("--disable-regime-hard-veto", action="store_true", help="Disable hard no-trade veto rules in the regime gate")
    parser.add_argument("--output-prefix", type=str, default="backtest", help="Prefix for output artifact filenames")
    parser.add_argument("--quiet", action="store_true", help="Reduce console logs")
    parser.add_argument("--stock-list-path", type=str, default=None, help="覆盖 filtered_stocks.csv 的自定义股票列表 CSV（须含 ts_code, name 列）")
    parser.add_argument(
        "--signal-date",
        type=str,
        default=None,
        help=(
            "仅在 --predict-only 模式下生效：把日线数据截断到该日期（YYYY-MM-DD），"
            "用于历史 IC 评估。非交易日时自动使用前一个交易日。"
        ),
    )
    return parser


def parse_args() -> BacktestConfig:
    args = build_arg_parser().parse_args()
    full_universe = args.full_universe or args.sample_size <= 0
    sample_size = None if full_universe else args.sample_size
    if args.use_mini:
        model_name, tokenizer_name = FAST_MODEL_NAME, FAST_TOKENIZER_NAME
    elif args.use_small:
        model_name, tokenizer_name = SMALL_MODEL_NAME, DEFAULT_TOKENIZER_NAME
    else:
        model_name, tokenizer_name = args.model_name, args.tokenizer_name
    lookback = args.lookback
    max_context = args.max_context if args.max_context is not None else lookback
    return BacktestConfig(
        start_date=args.start_date,
        hold_days=args.hold_days,
        lookback=lookback,
        max_context=max_context,
        sample_size=sample_size,
        full_universe=full_universe,
        max_periods=args.max_periods,
        sample_seed=args.sample_seed,
        top_k=args.top_k,
        batch_size=args.batch_size,
        device=args.device,
        model_name=model_name,
        tokenizer_name=tokenizer_name,
        temperature=args.temperature,
        forecast_top_k=args.forecast_top_k,
        forecast_top_p=args.forecast_top_p,
        sample_count=args.sample_count,
        price_limit_rate=args.price_limit_rate,
        ranking_mode=args.ranking_mode,
        fast_risk_lookback=args.fast_risk_lookback,
        fast_risk_candidate_pool=args.fast_risk_candidate_pool,
        regime_gate=args.regime_gate,
        regime_skip_threshold=args.regime_skip_threshold,
        regime_mini_threshold=args.regime_mini_threshold,
        regime_hard_veto=not args.disable_regime_hard_veto,
        regime_lookback=args.regime_lookback,
        output_prefix=args.output_prefix,
        predict_only=args.predict_only,
        verbose=not args.quiet,
        stock_list_path=args.stock_list_path,
        signal_date_override=args.signal_date,
    )


def main() -> None:
    config = parse_args()
    if config.predict_only:
        result = predict_latest(config)
        if result.get("skipped"):
            regime = result.get("regime_gate")
            if regime is not None:
                print(pd.DataFrame([regime.to_record()]).to_string(index=False))
            print("[kronos] prediction skipped by regime gate")
            print(f"[kronos] report saved: {result['report_path']}")
            if result.get("regime_path") is not None:
                print(f"[kronos] regime gate saved: {result['regime_path']}")
            return
        print(result["predictions"].head(config.top_k).to_string(index=False))
        print(f"[kronos] report saved: {result['report_path']}")
        if result.get("regime_path") is not None:
            print(f"[kronos] regime gate saved: {result['regime_path']}")
        return
    result = run_backtest(config)
    summary_df = result["summary"]
    if summary_df.empty:
        raise RuntimeError("No usable backtest result was generated.")
    print(summary_df.to_string(index=False))
    print(f"[kronos] report saved: {result['report_path']}")


if __name__ == "__main__":
    main()
