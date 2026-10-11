from __future__ import annotations

import argparse
import gc
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from TechnicalPatternScanner.utils import save_result_csv

from .backtest import (
    FAST_MODEL_NAME,
    FAST_TOKENIZER_NAME,
    MODEL_SOURCE_URL,
    OUTPUT_DIR,
    BacktestConfig,
    calc_trade_window,
    compute_benchmark,
    _fmt_float,
    _fmt_pct,
    _safe_prefix,
    build_trading_dates,
    forecast_cross_section,
    load_predictor,
    select_universe,
    set_seed,
)


@dataclass(frozen=True)
class MiniScoreConfig:
    horizon: int = 10
    max_windows: int = 20
    start_date: str | None = "2025-01-01"
    end_signal_date: str | None = None
    signal_dates: str | None = None
    random_windows: bool = True
    window_step: int = 20
    mini_top_n: int = 50
    mini_min_return: float = 0.05
    final_top_k: int = 3
    # Fallback weights — used when sample_count == 1 / no path stats in cache
    pred_weight: float = 0.50
    win_weight: float = 0.30
    return_weight: float = 0.20
    # Path-mode weights — used when sample_count > 1 and path stats are present
    path_pred_weight: float = 0.30
    path_win_prob_weight: float = 0.25
    path_rr_weight: float = 0.20
    path_hist_win_weight: float = 0.15
    path_hist_return_weight: float = 0.10
    # Hard veto: exclude candidates whose median predicted drawdown is below this
    path_drawdown_veto: float = -0.15
    return_scale: float = 0.10
    lookback: int = 512
    max_context: int = 2048
    batch_size: int = 32
    device: str = "cpu"
    sample_seed: int = 42
    temperature: float = 1.0
    forecast_top_k: int = 0    # 0 = no top-k cap; enables stochastic sampling for path diversity
    forecast_top_p: float = 0.9
    sample_count: int = 5      # >1 activates multi-path mode
    price_limit_rate: float = 0.10
    output_prefix: str = "mini_score_validate"
    cache_dir: str | None = None
    reuse_cache_dirs: tuple[str, ...] = ()
    quiet: bool = False


def _base_config(config: MiniScoreConfig) -> BacktestConfig:
    return BacktestConfig(
        start_date=config.start_date or "2025-01-01",
        hold_days=config.horizon,
        lookback=config.lookback,
        max_context=config.max_context,
        sample_size=None,
        full_universe=True,
        top_k=config.final_top_k,
        batch_size=config.batch_size,
        device=config.device,
        model_name=FAST_MODEL_NAME,
        tokenizer_name=FAST_TOKENIZER_NAME,
        temperature=config.temperature,
        forecast_top_k=config.forecast_top_k,
        forecast_top_p=config.forecast_top_p,
        sample_count=config.sample_count,
        price_limit_rate=config.price_limit_rate,
        output_prefix=config.output_prefix,
        predict_only=True,
        verbose=not config.quiet,
    )


def _cache_path(cache_dir: Path, signal_date: pd.Timestamp) -> Path:
    return cache_dir / f"mini_{signal_date.strftime('%Y%m%d')}.csv"


def _read_predictions(path: Path, code_to_name: dict[str, str]) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="utf-8-sig")
    required = {"ts_code", "predicted_return", "predicted_entry_open", "predicted_exit_close"}
    missing = required - set(df.columns)
    if missing:
        raise RuntimeError(f"{path} is missing columns: {sorted(missing)}")

    df = df.sort_values("predicted_return", ascending=False).reset_index(drop=True)
    if "name" not in df.columns:
        df["name"] = df["ts_code"].map(code_to_name).fillna("")
    else:
        df["name"] = df["name"].fillna(df["ts_code"].map(code_to_name)).fillna("")
    df["mini_rank"] = np.arange(1, len(df) + 1)
    return df


def _select_signal_dates(trading_dates: pd.DatetimeIndex, config: MiniScoreConfig) -> list[pd.Timestamp]:
    if config.signal_dates:
        return [pd.Timestamp(item.strip()) for item in config.signal_dates.split(",") if item.strip()]

    max_signal_idx = len(trading_dates) - config.horizon - 1
    if max_signal_idx < 0:
        raise RuntimeError("Not enough trading dates for the requested horizon.")

    end_date = pd.Timestamp(config.end_signal_date) if config.end_signal_date else trading_dates[max_signal_idx]
    end_idx = int(trading_dates.searchsorted(end_date, side="right") - 1)
    end_idx = min(end_idx, max_signal_idx)
    min_date = pd.Timestamp(config.start_date) if config.start_date else trading_dates[0]
    step = max(config.window_step, 1)

    if config.random_windows:
        candidate_indices = [
            idx
            for idx in range(0, end_idx + 1)
            if trading_dates[idx] >= min_date and idx + config.horizon < len(trading_dates)
        ]
        rng = np.random.default_rng(config.sample_seed)
        rng.shuffle(candidate_indices)
        picked: list[int] = []
        for idx in candidate_indices:
            if all(abs(idx - chosen) >= step for chosen in picked):
                picked.append(idx)
            if len(picked) >= config.max_windows:
                break
        if len(picked) < config.max_windows:
            raise RuntimeError(
                f"Only {len(picked)} windows satisfy min spacing {step}; requested {config.max_windows}."
            )
        return [trading_dates[idx] for idx in sorted(picked)]

    picked = []
    idx = end_idx
    while idx >= 0 and len(picked) < config.max_windows:
        signal_date = trading_dates[idx]
        if signal_date < min_date:
            break
        picked.append(signal_date)
        idx -= step
    return list(reversed(picked))


def _find_reuse_cache(signal_date: pd.Timestamp, config: MiniScoreConfig) -> Path | None:
    name = f"mini_{signal_date.strftime('%Y%m%d')}.csv"
    for cache_dir in config.reuse_cache_dirs:
        path = Path(cache_dir) / name
        if path.exists():
            return path
    return None


def _predict_or_load_mini(
    signal_date: pd.Timestamp,
    future_dates: pd.DatetimeIndex,
    stock_data: dict[str, pd.DataFrame],
    code_to_name: dict[str, str],
    predictor: object | None,
    base_config: BacktestConfig,
    cache_dir: Path,
    config: MiniScoreConfig,
) -> tuple[pd.DataFrame, object]:
    path = _cache_path(cache_dir, signal_date)
    if path.exists():
        return _read_predictions(path, code_to_name), predictor

    reuse_path = _find_reuse_cache(signal_date, config)
    if reuse_path is not None:
        df = _read_predictions(reuse_path, code_to_name)
        save_result_csv(df, str(path))
        return df, predictor

    if predictor is None:
        predictor = load_predictor(base_config)

    if not config.quiet:
        print(f"[mini-score] predict mini: signal={signal_date.date()}, input={len(stock_data)}", flush=True)
    df = forecast_cross_section(
        predictor=predictor,
        stock_data=stock_data,
        signal_date=signal_date,
        future_dates=future_dates,
        config=base_config,
    )
    if not df.empty:
        df["name"] = df["ts_code"].map(code_to_name).fillna("")
        df["mini_rank"] = np.arange(1, len(df) + 1)
    save_result_csv(df, str(path))
    return df, predictor


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-value))


_PATH_COLS = ("path_win_prob", "path_p10_return", "path_p90_return", "path_median_drawdown", "path_reward_risk", "path_count")


def _strict_candidates(
    predictions: pd.DataFrame,
    signal_date: pd.Timestamp,
    entry_date: pd.Timestamp,
    exit_date: pd.Timestamp,
    stock_data: dict[str, pd.DataFrame],
    config: MiniScoreConfig,
) -> pd.DataFrame:
    candidates = predictions[
        (predictions["mini_rank"] <= config.mini_top_n)
        & (predictions["predicted_return"] >= config.mini_min_return)
    ].copy()

    # Hard veto: drop candidates whose predicted median drawdown is too severe
    if "path_median_drawdown" in candidates.columns:
        candidates = candidates[
            candidates["path_median_drawdown"].isna()
            | (candidates["path_median_drawdown"] >= config.path_drawdown_veto)
        ].copy()

    rows: list[dict] = []
    for _, row in candidates.iterrows():
        code = str(row["ts_code"])
        res = calc_trade_window(stock_data[code], entry_date, exit_date)
        r: dict = {
            "signal_date": signal_date.date(),
            "entry_date": entry_date.date(),
            "exit_date": exit_date.date(),
            "ts_code": code,
            "name": row.get("name", ""),
            "mini_rank": int(row["mini_rank"]),
            "predicted_return": float(row["predicted_return"]),
            "trade_return": np.nan if res is None else float(res["trade_return"]),
        }
        for col in _PATH_COLS:
            if col in row.index:
                r[col] = row[col]
        rows.append(r)
    return pd.DataFrame(rows)


def _score_candidates(
    candidates: pd.DataFrame,
    history: dict[str, list[float]],
    config: MiniScoreConfig,
) -> pd.DataFrame:
    scored = candidates.copy()

    # pred_score: sigmoid of predicted return value (preserves magnitude, not just rank)
    scored["pred_score"] = scored["predicted_return"].apply(
        lambda r: _sigmoid(float(r) / config.return_scale) if pd.notna(r) else 0.5
    )

    hist_count: list[int] = []
    win_score: list[float] = []
    hist_avg_return: list[float] = []
    hist_return_score: list[float] = []
    for _, row in scored.iterrows():
        prev = history.get(str(row["ts_code"]), [])
        count = len(prev)
        wins = sum(1 for value in prev if value > 0)
        avg_return = float(np.mean(prev)) if prev else 0.0
        hist_count.append(count)
        win_score.append((wins + 1.0) / (count + 2.0))
        hist_avg_return.append(avg_return)
        hist_return_score.append(_sigmoid(avg_return / config.return_scale))

    scored["hist_count"] = hist_count
    scored["win_score"] = win_score
    scored["hist_avg_return"] = hist_avg_return
    scored["hist_return_score"] = hist_return_score

    has_paths = (
        "path_win_prob" in scored.columns
        and "path_reward_risk" in scored.columns
        and scored["path_win_prob"].notna().any()
    )

    if has_paths:
        scored["path_win_prob_score"] = scored["path_win_prob"].fillna(0.5)
        scored["path_rr_score"] = scored["path_reward_risk"].apply(
            lambda rr: _sigmoid(float(rr) / 2.0) if pd.notna(rr) else 0.5
        )
        scored["composite_score"] = (
            config.path_pred_weight * scored["pred_score"]
            + config.path_win_prob_weight * scored["path_win_prob_score"]
            + config.path_rr_weight * scored["path_rr_score"]
            + config.path_hist_win_weight * scored["win_score"]
            + config.path_hist_return_weight * scored["hist_return_score"]
        )
    else:
        scored["composite_score"] = (
            config.pred_weight * scored["pred_score"]
            + config.win_weight * scored["win_score"]
            + config.return_weight * scored["hist_return_score"]
        )

    return scored


def _evaluate_picks(
    strategy: str,
    picks: pd.DataFrame,
    benchmark_return: float,
    config: MiniScoreConfig,
) -> tuple[dict, list[dict]]:
    portfolio_return = float(picks["trade_return"].fillna(0.0).sum() / config.final_top_k)
    row0 = picks.iloc[0]
    period = {
        "strategy": strategy,
        "signal_date": row0["signal_date"],
        "entry_date": row0["entry_date"],
        "exit_date": row0["exit_date"],
        "requested_holdings": config.final_top_k,
        "holdings": int(picks["trade_return"].notna().sum()),
        "portfolio_return": round(portfolio_return, 6),
        "benchmark_return": round(float(benchmark_return), 6) if pd.notna(benchmark_return) else np.nan,
        "alpha": round(float(portfolio_return - benchmark_return), 6) if pd.notna(benchmark_return) else np.nan,
        "executed_codes": ",".join(picks["ts_code"].astype(str).tolist()),
        "avg_hist_count": round(float(picks.get("hist_count", pd.Series(dtype=float)).mean()), 4)
        if "hist_count" in picks
        else np.nan,
    }
    trades: list[dict] = []
    for rank, (_, pick) in enumerate(picks.iterrows(), start=1):
        trades.append(
            {
                "strategy": strategy,
                "signal_date": pick["signal_date"],
                "final_rank": rank,
                "ts_code": pick["ts_code"],
                "name": pick.get("name", ""),
                "mini_rank": int(pick["mini_rank"]),
                "predicted_return": pick["predicted_return"],
                "hist_count": pick.get("hist_count", np.nan),
                "win_score": pick.get("win_score", np.nan),
                "hist_avg_return": pick.get("hist_avg_return", np.nan),
                "composite_score": pick.get("composite_score", np.nan),
                "path_win_prob": pick.get("path_win_prob", np.nan),
                "path_p10_return": pick.get("path_p10_return", np.nan),
                "path_p90_return": pick.get("path_p90_return", np.nan),
                "path_median_drawdown": pick.get("path_median_drawdown", np.nan),
                "path_reward_risk": pick.get("path_reward_risk", np.nan),
                "trade_return": pick["trade_return"],
            }
        )
    return period, trades


def _summarize(periods: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    for strategy, group in periods.groupby("strategy", sort=False):
        returns = group["portfolio_return"].fillna(0.0)
        benchmark = group["benchmark_return"].fillna(0.0)
        equity = (1.0 + returns).cumprod()
        benchmark_equity = (1.0 + benchmark).cumprod()
        drawdown = equity / equity.cummax() - 1.0
        rows.append(
            {
                "strategy": strategy,
                "n_periods": len(group),
                "win_rate": round(float((returns > 0).mean()), 6),
                "positive_alpha_rate": round(float((group["alpha"] > 0).mean()), 6),
                "avg_period_return": round(float(returns.mean()), 6),
                "median_period_return": round(float(returns.median()), 6),
                "compound_return": round(float(equity.iloc[-1] - 1.0), 6),
                "benchmark_compound": round(float(benchmark_equity.iloc[-1] - 1.0), 6),
                "avg_alpha": round(float(group["alpha"].mean()), 6),
                "max_drawdown": round(float(drawdown.min()), 6),
                "best_period": round(float(returns.max()), 6),
                "worst_period": round(float(returns.min()), 6),
            }
        )
    return pd.DataFrame(rows).sort_values("compound_return", ascending=False).reset_index(drop=True)


def _build_report(config: MiniScoreConfig, summary: pd.DataFrame, periods: pd.DataFrame, paths: dict[str, Path]) -> str:
    today = pd.Timestamp.today().strftime("%Y-%m-%d")
    lines = [
        f"# Kronos-mini Composite Score Validation - {today}",
        "",
        f"- Source: {MODEL_SOURCE_URL}",
        f"- Model: `{FAST_MODEL_NAME}`",
        f"- Tokenizer: `{FAST_TOKENIZER_NAME}`",
        f"- Horizon: {config.horizon} trading days",
        f"- Windows: {periods['signal_date'].nunique() if not periods.empty else 0}",
        f"- Candidate filter: Mini rank <= {config.mini_top_n}, predicted_return >= {_fmt_pct(config.mini_min_return)}",
        f"- Sample paths: {config.sample_count} (top_k={config.forecast_top_k}, top_p={config.forecast_top_p})",
        f"- Path-mode score weights: pred={config.path_pred_weight:g}, win_prob={config.path_win_prob_weight:g}, "
        f"rr={config.path_rr_weight:g}, hist_win={config.path_hist_win_weight:g}, hist_ret={config.path_hist_return_weight:g}",
        f"- Path drawdown veto: {_fmt_pct(config.path_drawdown_veto)}",
        f"- Fallback score weights: pred={config.pred_weight:g}, win={config.win_weight:g}, return={config.return_weight:g}",
        "",
        "## Summary",
        "",
        "| Strategy | Periods | Win rate | Alpha rate | Avg return | Median | Compound | Benchmark | Max DD |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for _, row in summary.iterrows():
        lines.append(
            f"| {row['strategy']} | {int(row['n_periods'])} | {_fmt_pct(row['win_rate'])} | "
            f"{_fmt_pct(row['positive_alpha_rate'])} | {_fmt_pct(row['avg_period_return'])} | "
            f"{_fmt_pct(row['median_period_return'])} | {_fmt_pct(row['compound_return'])} | "
            f"{_fmt_pct(row['benchmark_compound'])} | {_fmt_pct(row['max_drawdown'])} |"
        )

    lines += [
        "",
        "## Latest Window",
        "",
    ]
    if not periods.empty:
        latest_date = periods["signal_date"].max()
        latest = periods[periods["signal_date"] == latest_date]
        lines += [
            f"- Signal date: {latest_date}",
            "",
            "| Strategy | Return | Benchmark | Alpha | Codes |",
            "| --- | ---: | ---: | ---: | --- |",
        ]
        for _, row in latest.iterrows():
            lines.append(
                f"| {row['strategy']} | {_fmt_pct(row['portfolio_return'])} | "
                f"{_fmt_pct(row['benchmark_return'])} | {_fmt_pct(row['alpha'])} | {row['executed_codes']} |"
            )

    lines += ["", "## Artifacts", ""]
    for name, path in paths.items():
        lines.append(f"- {name}: `{path.as_posix()}`")
    lines.append("")
    return "\n".join(lines)


def run_validation(config: MiniScoreConfig) -> dict[str, object]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(config.cache_dir) if config.cache_dir else OUTPUT_DIR / f"{_safe_prefix(config.output_prefix)}_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    set_seed(config.sample_seed)

    base_config = _base_config(config)
    universe_df, stock_data = select_universe(base_config)
    trading_dates = build_trading_dates(stock_data)
    signal_dates = _select_signal_dates(trading_dates, config)
    code_to_name = dict(zip(universe_df["ts_code"].astype(str), universe_df["name"]))
    universe_codes = universe_df["ts_code"].astype(str).tolist()
    predictor = None
    history: dict[str, list[float]] = {}
    all_periods: list[dict] = []
    all_trades: list[dict] = []
    all_candidates: list[pd.DataFrame] = []

    try:
        for signal_date in signal_dates:
            idx = int(trading_dates.get_loc(signal_date))
            future_dates = trading_dates[idx + 1 : idx + 1 + config.horizon]
            if len(future_dates) < config.horizon:
                continue
            entry_date, exit_date = future_dates[0], future_dates[-1]
            if not config.quiet:
                print(
                    f"[mini-score] window signal={signal_date.date()} entry={entry_date.date()} exit={exit_date.date()}",
                    flush=True,
                )

            predictions, predictor = _predict_or_load_mini(
                signal_date=signal_date,
                future_dates=future_dates,
                stock_data=stock_data,
                code_to_name=code_to_name,
                predictor=predictor,
                base_config=base_config,
                cache_dir=cache_dir,
                config=config,
            )
            candidates = _strict_candidates(predictions, signal_date, entry_date, exit_date, stock_data, config)
            if candidates.empty:
                continue
            scored = _score_candidates(candidates, history, config)
            all_candidates.append(scored.copy())

            mini_picks = scored.sort_values("mini_rank", ascending=True).head(config.final_top_k).copy()
            composite_picks = scored.sort_values(
                ["composite_score", "predicted_return"],
                ascending=False,
            ).head(config.final_top_k).copy()
            benchmark_return = compute_benchmark(stock_data, universe_codes, entry_date, exit_date)

            for strategy, picks in [("mini_only", mini_picks), ("composite_score", composite_picks)]:
                period, trades = _evaluate_picks(strategy, picks, benchmark_return, config)
                all_periods.append(period)
                all_trades.extend(trades)

            for _, row in candidates.dropna(subset=["trade_return"]).iterrows():
                history.setdefault(str(row["ts_code"]), []).append(float(row["trade_return"]))
    finally:
        if predictor is not None:
            del predictor
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    periods = pd.DataFrame(all_periods)
    trades = pd.DataFrame(all_trades)
    candidates = pd.concat(all_candidates, ignore_index=True) if all_candidates else pd.DataFrame()
    summary = _summarize(periods) if not periods.empty else pd.DataFrame()

    prefix = _safe_prefix(config.output_prefix)
    paths = {
        "summary": OUTPUT_DIR / f"{prefix}_summary.csv",
        "periods": OUTPUT_DIR / f"{prefix}_periods.csv",
        "trades": OUTPUT_DIR / f"{prefix}_trades.csv",
        "candidates": OUTPUT_DIR / f"{prefix}_candidates.csv",
    }
    save_result_csv(summary, str(paths["summary"]))
    save_result_csv(periods, str(paths["periods"]))
    save_result_csv(trades, str(paths["trades"]))
    save_result_csv(candidates, str(paths["candidates"]))

    report_path = OUTPUT_DIR / f"kronos_mini_score_validation_{pd.Timestamp.today().strftime('%Y-%m-%d')}_{prefix}.md"
    report_path.write_text(_build_report(config, summary, periods, paths), encoding="utf-8")

    return {
        "summary": summary,
        "periods": periods,
        "trades": trades,
        "candidates": candidates,
        "cache_dir": cache_dir,
        "report_path": report_path,
        "signal_dates": signal_dates,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate Kronos-mini Top3 vs composite score Top3")
    parser.add_argument("--horizon", type=int, default=10, help="Holding horizon in trading days")
    parser.add_argument("--max-windows", type=int, default=20, help="Number of windows")
    parser.add_argument("--start-date", type=str, default="2025-01-01", help="Earliest signal date")
    parser.add_argument("--end-signal-date", type=str, default=None, help="Latest signal date")
    parser.add_argument("--signal-dates", type=str, default=None, help="Comma-separated explicit signal dates")
    parser.add_argument("--random-windows", action="store_true", default=True, help="Randomly sample windows")
    parser.add_argument("--sequential-windows", action="store_false", dest="random_windows", help="Use sequential windows from the end")
    parser.add_argument("--window-step", type=int, default=20, help="Minimum spacing for random windows or step for sequential")
    parser.add_argument("--mini-top-n", type=int, default=50, help="Mini rank cap")
    parser.add_argument("--mini-min-return", type=float, default=0.05, help="Mini minimum predicted return")
    parser.add_argument("--final-top-k", type=int, default=3, help="Final holdings")
    parser.add_argument("--pred-weight", type=float, default=0.50, help="Fallback: prediction score weight")
    parser.add_argument("--win-weight", type=float, default=0.30, help="Fallback: historical win score weight")
    parser.add_argument("--return-weight", type=float, default=0.20, help="Fallback: historical return score weight")
    parser.add_argument("--path-pred-weight", type=float, default=0.30, help="Path mode: median return score weight")
    parser.add_argument("--path-win-prob-weight", type=float, default=0.25, help="Path mode: win probability weight")
    parser.add_argument("--path-rr-weight", type=float, default=0.20, help="Path mode: reward/risk score weight")
    parser.add_argument("--path-hist-win-weight", type=float, default=0.15, help="Path mode: historical win rate weight")
    parser.add_argument("--path-hist-return-weight", type=float, default=0.10, help="Path mode: historical avg return weight")
    parser.add_argument("--path-drawdown-veto", type=float, default=-0.15, help="Exclude if median predicted drawdown below this")
    parser.add_argument("--return-scale", type=float, default=0.10, help="Return sigmoid scale")
    parser.add_argument("--lookback", type=int, default=512, help="Historical lookback")
    parser.add_argument("--max-context", type=int, default=2048, help="Model max context")
    parser.add_argument("--batch-size", type=int, default=32, help="Inference batch size")
    parser.add_argument("--device", type=str, default="cpu", help="Inference device")
    parser.add_argument("--sample-seed", type=int, default=42, help="Random seed")
    parser.add_argument("--temperature", type=float, default=1.0, help="Sampling temperature")
    parser.add_argument("--forecast-top-k", type=int, default=0, help="Token-level top-k (0 = no cap, enables stochastic sampling)")
    parser.add_argument("--forecast-top-p", type=float, default=0.9, help="Token-level top-p nucleus sampling")
    parser.add_argument("--sample-count", type=int, default=5, help="Forecast path count (>1 activates path-stats mode)")
    parser.add_argument("--price-limit-rate", type=float, default=0.10, help="A-share daily price limit")
    parser.add_argument("--output-prefix", type=str, default="mini_score_validate", help="Output prefix")
    parser.add_argument("--cache-dir", type=str, default=None, help="Prediction cache directory")
    parser.add_argument("--reuse-cache-dir", action="append", default=[], help="Existing cache dir to reuse mini_YYYYMMDD.csv from")
    parser.add_argument("--quiet", action="store_true", help="Reduce logs")
    return parser


def parse_args() -> MiniScoreConfig:
    args = build_arg_parser().parse_args()
    return MiniScoreConfig(
        horizon=args.horizon,
        max_windows=args.max_windows,
        start_date=args.start_date,
        end_signal_date=args.end_signal_date,
        signal_dates=args.signal_dates,
        random_windows=args.random_windows,
        window_step=args.window_step,
        mini_top_n=args.mini_top_n,
        mini_min_return=args.mini_min_return,
        final_top_k=args.final_top_k,
        pred_weight=args.pred_weight,
        win_weight=args.win_weight,
        return_weight=args.return_weight,
        path_pred_weight=args.path_pred_weight,
        path_win_prob_weight=args.path_win_prob_weight,
        path_rr_weight=args.path_rr_weight,
        path_hist_win_weight=args.path_hist_win_weight,
        path_hist_return_weight=args.path_hist_return_weight,
        path_drawdown_veto=args.path_drawdown_veto,
        return_scale=args.return_scale,
        lookback=args.lookback,
        max_context=args.max_context,
        batch_size=args.batch_size,
        device=args.device,
        sample_seed=args.sample_seed,
        temperature=args.temperature,
        forecast_top_k=args.forecast_top_k,
        forecast_top_p=args.forecast_top_p,
        sample_count=args.sample_count,
        price_limit_rate=args.price_limit_rate,
        output_prefix=args.output_prefix,
        cache_dir=args.cache_dir,
        reuse_cache_dirs=tuple(args.reuse_cache_dir),
        quiet=args.quiet,
    )


def main() -> None:
    result = run_validation(parse_args())
    summary = result["summary"]
    if summary.empty:
        print("[mini-score] no results")
    else:
        print(summary.to_string(index=False))
    print("[mini-score] signal dates:", ", ".join(date.strftime("%Y-%m-%d") for date in result["signal_dates"]))
    print(f"[mini-score] cache dir: {result['cache_dir']}")
    print(f"[mini-score] report saved: {result['report_path']}")


if __name__ == "__main__":
    main()
