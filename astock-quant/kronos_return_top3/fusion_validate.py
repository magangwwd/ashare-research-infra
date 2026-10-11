from __future__ import annotations

import argparse
import gc
import math
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from TechnicalPatternScanner.utils import save_result_csv

from .backtest import (
    DEFAULT_MODEL_NAME,
    DEFAULT_TOKENIZER_NAME,
    FAST_MODEL_NAME,
    FAST_TOKENIZER_NAME,
    MODEL_SOURCE_URL,
    OUTPUT_DIR,
    SMALL_MODEL_NAME,
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
from .cascade_predict import StageSpec, _stage_view, _subset_stock_data


@dataclass(frozen=True)
class FusionValidationConfig:
    horizon: int = 10
    max_windows: int = 10
    start_date: str | None = None
    end_signal_date: str | None = None
    signal_dates: str | None = None
    window_step: int | None = None
    random_windows: bool = False
    mini_top_n: int = 50
    mini_min_return: float = 0.05
    final_top_k: int = 3
    veto_return: float = 0.0
    rank3_veto_return: float = -0.05
    weight_mini: float = 0.5
    weight_small: float = 0.25
    weight_base: float = 0.25
    include_dual_veto: bool = False
    include_weighted_fusion: bool = False
    mini_lookback: int = 512
    mini_max_context: int = 2048
    small_lookback: int = 512
    small_max_context: int = 512
    base_lookback: int = 512
    base_max_context: int = 512
    batch_size: int = 32
    device: str = "cpu"
    sample_seed: int = 42
    temperature: float = 1.0
    forecast_top_k: int = 1
    forecast_top_p: float = 1.0
    sample_count: int = 1
    price_limit_rate: float = 0.10
    output_prefix: str = "fusion_validate"
    cache_dir: str | None = None
    seed_mini_predictions_path: str | None = None
    seed_small_predictions_path: str | None = None
    seed_base_predictions_path: str | None = None
    quiet: bool = False


def _make_specs(config: FusionValidationConfig) -> dict[str, StageSpec]:
    return {
        "mini": StageSpec("mini", FAST_MODEL_NAME, FAST_TOKENIZER_NAME, config.mini_lookback, config.mini_max_context),
        "small": StageSpec("small", SMALL_MODEL_NAME, DEFAULT_TOKENIZER_NAME, config.small_lookback, config.small_max_context),
        "base": StageSpec("base", DEFAULT_MODEL_NAME, DEFAULT_TOKENIZER_NAME, config.base_lookback, config.base_max_context),
    }


def _base_config(config: FusionValidationConfig) -> BacktestConfig:
    return BacktestConfig(
        start_date=config.start_date or "2025-01-01",
        hold_days=config.horizon,
        lookback=config.mini_lookback,
        max_context=config.mini_max_context,
        sample_size=None,
        full_universe=True,
        top_k=config.final_top_k,
        batch_size=config.batch_size,
        device=config.device,
        temperature=config.temperature,
        forecast_top_k=config.forecast_top_k,
        forecast_top_p=config.forecast_top_p,
        sample_count=config.sample_count,
        price_limit_rate=config.price_limit_rate,
        output_prefix=config.output_prefix,
        predict_only=True,
        verbose=not config.quiet,
    )


def _stage_config(base: BacktestConfig, spec: StageSpec) -> BacktestConfig:
    return replace(
        base,
        model_name=spec.model_name,
        tokenizer_name=spec.tokenizer_name,
        lookback=spec.lookback,
        max_context=spec.max_context,
    )


def _cache_path(cache_dir: Path, stage: str, signal_date: pd.Timestamp) -> Path:
    return cache_dir / f"{stage}_{signal_date.strftime('%Y%m%d')}.csv"


def _read_stage_csv(path: str | Path, stage: str, code_to_name: dict[str, str]) -> pd.DataFrame:
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
    rank_col = f"{stage}_rank"
    df[rank_col] = np.arange(1, len(df) + 1)
    return df


def _seed_path_for_stage(config: FusionValidationConfig, stage: str) -> str | None:
    if stage == "mini":
        return config.seed_mini_predictions_path
    if stage == "small":
        return config.seed_small_predictions_path
    if stage == "base":
        return config.seed_base_predictions_path
    return None


def _stage_predict_cached(
    stage: str,
    spec: StageSpec,
    base_config: BacktestConfig,
    stock_data: dict[str, pd.DataFrame],
    code_to_name: dict[str, str],
    signal_date: pd.Timestamp,
    future_dates: pd.DatetimeIndex,
    cache_dir: Path,
    predictors: dict[str, object],
    config: FusionValidationConfig,
) -> pd.DataFrame:
    cache_path = _cache_path(cache_dir, stage, signal_date)
    if cache_path.exists():
        return _read_stage_csv(cache_path, stage, code_to_name)

    seed_path = _seed_path_for_stage(config, stage)
    if seed_path:
        seeded = _read_stage_csv(seed_path, stage, code_to_name)
        if "signal_date" not in seeded.columns or str(seeded["signal_date"].iloc[0]) == signal_date.strftime("%Y-%m-%d"):
            save_result_csv(seeded, str(cache_path))
            return seeded

    if stage not in predictors:
        if not config.quiet:
            print(
                f"[fusion] load {stage}: model={spec.model_name}, tokenizer={spec.tokenizer_name}",
                flush=True,
            )
        predictors[stage] = load_predictor(_stage_config(base_config, spec))

    if not config.quiet:
        print(f"[fusion] predict {stage}: signal={signal_date.date()}, input={len(stock_data)}", flush=True)

    stage_cfg = _stage_config(base_config, spec)
    forecasts = forecast_cross_section(
        predictor=predictors[stage],
        stock_data=stock_data,
        signal_date=signal_date,
        future_dates=future_dates,
        config=stage_cfg,
    )
    if not forecasts.empty:
        forecasts["name"] = forecasts["ts_code"].map(code_to_name).fillna("")
        forecasts[f"{stage}_rank"] = np.arange(1, len(forecasts) + 1)
    save_result_csv(forecasts, str(cache_path))
    return forecasts


def _release_predictors(predictors: dict[str, object]) -> None:
    predictors.clear()
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def _select_signal_dates(
    trading_dates: pd.DatetimeIndex,
    config: FusionValidationConfig,
) -> list[pd.Timestamp]:
    if config.signal_dates:
        return [pd.Timestamp(item.strip()) for item in config.signal_dates.split(",") if item.strip()]

    max_signal_idx = len(trading_dates) - config.horizon - 1
    if max_signal_idx < 0:
        raise RuntimeError("Not enough trading dates for the requested horizon.")

    end_date = pd.Timestamp(config.end_signal_date) if config.end_signal_date else trading_dates[max_signal_idx]
    end_idx = int(trading_dates.searchsorted(end_date, side="right") - 1)
    end_idx = min(end_idx, max_signal_idx)

    min_date = pd.Timestamp(config.start_date) if config.start_date else trading_dates[0]
    step = config.window_step if config.window_step is not None else config.horizon

    if config.random_windows:
        candidate_indices = [
            idx
            for idx in range(0, end_idx + 1)
            if trading_dates[idx] >= min_date and idx + config.horizon < len(trading_dates)
        ]
        rng = np.random.default_rng(config.sample_seed)
        rng.shuffle(candidate_indices)
        picked_indices: list[int] = []
        for idx in candidate_indices:
            if all(abs(idx - chosen) >= max(step, 1) for chosen in picked_indices):
                picked_indices.append(idx)
            if len(picked_indices) >= config.max_windows:
                break
        if len(picked_indices) < config.max_windows:
            raise RuntimeError(
                f"Only {len(picked_indices)} random windows satisfy the minimum spacing; "
                f"requested {config.max_windows}. Reduce --window-step or widen the date range."
            )
        return [trading_dates[idx] for idx in sorted(picked_indices)]

    picked: list[pd.Timestamp] = []
    idx = end_idx
    while idx >= 0 and len(picked) < config.max_windows:
        signal_date = trading_dates[idx]
        if signal_date < min_date:
            break
        picked.append(signal_date)
        idx -= max(step, 1)
    return list(reversed(picked))


def _merge_stage_predictions(mini: pd.DataFrame, small: pd.DataFrame, base: pd.DataFrame) -> pd.DataFrame:
    merged = (
        mini[["ts_code", "name", "signal_date", "signal_close"]]
        .merge(_stage_view(mini, "mini"), on="ts_code", how="left")
        .merge(_stage_view(small, "small"), on="ts_code", how="left")
        .merge(_stage_view(base, "base"), on="ts_code", how="left")
    )
    return merged


def _dual_negative(df: pd.DataFrame, veto_return: float) -> pd.Series:
    small_bad = df["small_predicted_return"].fillna(1.0) < veto_return
    base_bad = df["base_predicted_return"].fillna(1.0) < veto_return
    return small_bad & base_bad


def _make_strategy_picks(
    merged: pd.DataFrame,
    mini_full: pd.DataFrame,
    config: FusionValidationConfig,
) -> dict[str, pd.DataFrame]:
    picks: dict[str, pd.DataFrame] = {}

    mini_only = mini_full.head(config.final_top_k).copy()
    mini_only = _merge_stage_predictions(mini_only, pd.DataFrame(), pd.DataFrame())
    mini_only.insert(0, "final_rank", np.arange(1, len(mini_only) + 1))
    picks["mini_only"] = mini_only

    if merged.empty:
        picks["rank3_only_veto"] = mini_only.copy()
        if config.include_dual_veto:
            for name in ["keep_top3_dual_veto", "dual_veto_mini_rank"]:
                picks[name] = pd.DataFrame()
        if config.include_weighted_fusion:
            picks["weighted_fusion"] = pd.DataFrame()
        return picks

    ranked = merged.sort_values("mini_rank", ascending=True).copy()
    ranked["rank3_dual_negative"] = _dual_negative(ranked, config.rank3_veto_return)

    rank3_rows: list[pd.Series] = []
    chosen: set[str] = set()
    for mini_rank in [1, 2]:
        row_df = ranked[ranked["mini_rank"] == mini_rank].head(1)
        if row_df.empty:
            continue
        row = row_df.iloc[0]
        rank3_rows.append(row)
        chosen.add(str(row["ts_code"]))

    third_df = ranked[ranked["mini_rank"] == 3].head(1)
    if not third_df.empty:
        third = third_df.iloc[0]
        if not bool(third["rank3_dual_negative"]):
            rank3_rows.append(third)
            chosen.add(str(third["ts_code"]))

    for _, row in ranked[ranked["mini_rank"] > 3].iterrows():
        if len(rank3_rows) >= config.final_top_k:
            break
        code = str(row["ts_code"])
        if code in chosen or bool(row["rank3_dual_negative"]):
            continue
        rank3_rows.append(row)
        chosen.add(code)

    rank3_df = pd.DataFrame(rank3_rows)
    if not rank3_df.empty:
        rank3_df.insert(0, "final_rank", np.arange(1, len(rank3_df) + 1))
    picks["rank3_only_veto"] = rank3_df

    if config.include_dual_veto:
        ranked["dual_negative"] = _dual_negative(ranked, config.veto_return)

        dual_filtered = ranked[~ranked["dual_negative"]].head(config.final_top_k).copy()
        dual_filtered.insert(0, "final_rank", np.arange(1, len(dual_filtered) + 1))
        picks["dual_veto_mini_rank"] = dual_filtered

        keep_rows: list[pd.Series] = []
        chosen = set()
        for _, row in ranked.head(config.final_top_k).iterrows():
            if not bool(row["dual_negative"]):
                keep_rows.append(row)
                chosen.add(str(row["ts_code"]))
        for _, row in ranked.iterrows():
            if len(keep_rows) >= config.final_top_k:
                break
            code = str(row["ts_code"])
            if code in chosen or bool(row["dual_negative"]):
                continue
            keep_rows.append(row)
            chosen.add(code)
        keep_df = pd.DataFrame(keep_rows)
        if not keep_df.empty:
            keep_df.insert(0, "final_rank", np.arange(1, len(keep_df) + 1))
        picks["keep_top3_dual_veto"] = keep_df

    if config.include_weighted_fusion:
        weighted = ranked.copy()
        weighted["fusion_score"] = (
            config.weight_mini * weighted["mini_predicted_return"].fillna(0.0)
            + config.weight_small * weighted["small_predicted_return"].fillna(0.0)
            + config.weight_base * weighted["base_predicted_return"].fillna(0.0)
        )
        weighted = weighted.sort_values(["fusion_score", "mini_predicted_return"], ascending=False).head(config.final_top_k)
        weighted.insert(0, "final_rank", np.arange(1, len(weighted) + 1))
        picks["weighted_fusion"] = weighted

    return picks


def _evaluate_picks(
    picks: dict[str, pd.DataFrame],
    stock_data: dict[str, pd.DataFrame],
    universe_codes: list[str],
    signal_date: pd.Timestamp,
    entry_date: pd.Timestamp,
    exit_date: pd.Timestamp,
    config: FusionValidationConfig,
    mini_candidate_count: int,
) -> tuple[list[dict], list[dict]]:
    benchmark = compute_benchmark(stock_data, universe_codes, entry_date, exit_date)
    periods: list[dict] = []
    trades: list[dict] = []

    for strategy, final_df in picks.items():
        executed_codes: list[str] = []
        skipped_codes: list[str] = []
        portfolio_return = 0.0

        for _, row in final_df.iterrows():
            ts_code = str(row["ts_code"])
            res = calc_trade_window(stock_data[ts_code], entry_date, exit_date)
            if res is None:
                skipped_codes.append(ts_code)
                continue

            weight = 1.0 / config.final_top_k
            contribution = res["trade_return"] * weight
            portfolio_return += contribution
            executed_codes.append(ts_code)
            trades.append(
                {
                    "strategy": strategy,
                    "signal_date": signal_date.date(),
                    "entry_date": entry_date.date(),
                    "exit_date": exit_date.date(),
                    "ts_code": ts_code,
                    "name": row.get("name", ""),
                    "final_rank": int(row["final_rank"]),
                    "mini_rank": row.get("mini_rank", np.nan),
                    "mini_predicted_return": row.get("mini_predicted_return", np.nan),
                    "small_rank": row.get("small_rank", np.nan),
                    "small_predicted_return": row.get("small_predicted_return", np.nan),
                    "base_rank": row.get("base_rank", np.nan),
                    "base_predicted_return": row.get("base_predicted_return", np.nan),
                    "fusion_score": row.get("fusion_score", np.nan),
                    "entry_price": round(res["entry_price"], 4),
                    "exit_price": round(res["exit_price"], 4),
                    "trade_return": round(res["trade_return"], 6),
                    "return_contribution": round(contribution, 6),
                }
            )

        alpha = portfolio_return - benchmark if pd.notna(benchmark) else np.nan
        periods.append(
            {
                "strategy": strategy,
                "signal_date": signal_date.date(),
                "entry_date": entry_date.date(),
                "exit_date": exit_date.date(),
                "mini_candidates": mini_candidate_count,
                "requested_holdings": config.final_top_k,
                "holdings": len(executed_codes),
                "skipped_holdings": config.final_top_k - len(executed_codes),
                "portfolio_return": round(portfolio_return, 6),
                "benchmark_return": round(float(benchmark), 6) if pd.notna(benchmark) else np.nan,
                "alpha": round(float(alpha), 6) if pd.notna(alpha) else np.nan,
                "executed_codes": ",".join(executed_codes),
                "skipped_codes": ",".join(skipped_codes),
            }
        )

    return periods, trades


def _summarize_periods(periods_df: pd.DataFrame, config: FusionValidationConfig) -> pd.DataFrame:
    rows: list[dict] = []
    for strategy, group in periods_df.groupby("strategy", sort=False):
        returns = group["portfolio_return"].fillna(0.0)
        benchmark = group["benchmark_return"].fillna(0.0)
        equity = (1.0 + returns).cumprod()
        benchmark_equity = (1.0 + benchmark).cumprod()
        compound = float(equity.iloc[-1] - 1.0) if not equity.empty else np.nan
        benchmark_compound = float(benchmark_equity.iloc[-1] - 1.0) if not benchmark_equity.empty else np.nan
        volatility = float(returns.std(ddof=0))
        annual_factor = math.sqrt(252.0 / config.horizon)
        sharpe = float(returns.mean() / volatility * annual_factor) if volatility > 0 else np.nan
        drawdown = equity / equity.cummax() - 1.0 if not equity.empty else pd.Series(dtype=float)
        rows.append(
            {
                "strategy": strategy,
                "n_periods": len(group),
                "win_rate": round(float((returns > 0).mean()), 6),
                "avg_period_return": round(float(returns.mean()), 6),
                "avg_alpha": round(float(group["alpha"].mean()), 6),
                "compound_return": round(compound, 6),
                "benchmark_compound_return": round(benchmark_compound, 6),
                "max_drawdown": round(float(drawdown.min()), 6) if not drawdown.empty else np.nan,
                "sharpe": round(sharpe, 6) if pd.notna(sharpe) else np.nan,
            }
        )
    return pd.DataFrame(rows).sort_values("compound_return", ascending=False).reset_index(drop=True)


def _build_report(
    config: FusionValidationConfig,
    summary: pd.DataFrame,
    periods: pd.DataFrame,
    paths: dict[str, Path],
) -> str:
    today = pd.Timestamp.today().strftime("%Y-%m-%d")
    lines = [
        f"# Kronos Conservative Fusion Validation - {today}",
        "",
        f"- Source: {MODEL_SOURCE_URL}",
        f"- Horizon: {config.horizon} trading days",
        f"- Windows: {periods['signal_date'].nunique() if not periods.empty else 0}",
        f"- Mini filter: rank <= {config.mini_top_n}, predicted_return >= {_fmt_pct(config.mini_min_return)}, strict no refill",
        f"- Rank3-only veto: Mini Top1/Top2 protected; Mini Top3 is replaced only when Small < {_fmt_pct(config.rank3_veto_return)} and Base < {_fmt_pct(config.rank3_veto_return)}",
    ]
    if config.include_dual_veto:
        lines.append(f"- Dual-negative veto: Small < {_fmt_pct(config.veto_return)} and Base < {_fmt_pct(config.veto_return)}")
    if config.include_weighted_fusion:
        lines.append(f"- Weighted fusion: mini={config.weight_mini:g}, small={config.weight_small:g}, base={config.weight_base:g}")
    lines += [
        "",
        "## Summary",
        "",
        "| Strategy | Periods | Win rate | Avg return | Avg alpha | Compound | Benchmark | Max DD | Sharpe |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for _, row in summary.iterrows():
        lines.append(
            f"| {row['strategy']} | {int(row['n_periods'])} | {_fmt_pct(row['win_rate'])} | "
            f"{_fmt_pct(row['avg_period_return'])} | {_fmt_pct(row['avg_alpha'])} | "
            f"{_fmt_pct(row['compound_return'])} | {_fmt_pct(row['benchmark_compound_return'])} | "
            f"{_fmt_pct(row['max_drawdown'])} | {_fmt_float(row['sharpe'])} |"
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
            "| Strategy | Return | Benchmark | Alpha | Holdings | Codes |",
            "| --- | ---: | ---: | ---: | ---: | --- |",
        ]
        for _, row in latest.iterrows():
            lines.append(
                f"| {row['strategy']} | {_fmt_pct(row['portfolio_return'])} | "
                f"{_fmt_pct(row['benchmark_return'])} | {_fmt_pct(row['alpha'])} | "
                f"{int(row['holdings'])}/{int(row['requested_holdings'])} | {row['executed_codes']} |"
            )

    lines += [
        "",
        "## Rules",
        "",
        "- `mini_only`: original Mini full-universe Top3 baseline.",
        "- `rank3_only_veto`: Mini Top1/Top2 stay; Mini Top3 is replaced only under the extreme dual-negative threshold, then replacement follows Mini rank.",
    ]
    if config.include_dual_veto:
        lines += [
            "- `keep_top3_dual_veto`: Mini Top3 stays unless both Small and Base are below veto threshold; replacements come by Mini rank.",
            "- `dual_veto_mini_rank`: remove only dual-negative names from Mini strict candidates, then keep Mini ranking.",
        ]
    if config.include_weighted_fusion:
        lines.append("- `weighted_fusion`: rank Mini strict candidates by weighted average of Mini/Small/Base predicted returns.")
    lines += [
        "",
        "## Artifacts",
        "",
    ]
    for name, path in paths.items():
        lines.append(f"- {name}: `{path.as_posix()}`")
    lines.append("")
    return "\n".join(lines)


def run_validation(config: FusionValidationConfig) -> dict[str, object]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(config.cache_dir) if config.cache_dir else OUTPUT_DIR / f"{_safe_prefix(config.output_prefix)}_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    set_seed(config.sample_seed)

    specs = _make_specs(config)
    base_config = _base_config(config)
    universe_df, stock_data = select_universe(base_config)
    trading_dates = build_trading_dates(stock_data)
    signal_dates = _select_signal_dates(trading_dates, config)
    code_to_name = dict(zip(universe_df["ts_code"], universe_df["name"]))
    universe_codes = universe_df["ts_code"].astype(str).tolist()
    predictors: dict[str, object] = {}

    all_periods: list[dict] = []
    all_trades: list[dict] = []
    all_candidates: list[pd.DataFrame] = []

    try:
        for signal_date in signal_dates:
            if signal_date not in trading_dates:
                raise RuntimeError(f"signal_date {signal_date.date()} is not available.")
            idx = int(trading_dates.get_loc(signal_date))
            future_dates = trading_dates[idx + 1 : idx + 1 + config.horizon]
            if len(future_dates) < config.horizon:
                continue
            entry_date, exit_date = future_dates[0], future_dates[-1]
            if not config.quiet:
                print(
                    f"[fusion] window signal={signal_date.date()} entry={entry_date.date()} exit={exit_date.date()}",
                    flush=True,
                )

            mini = _stage_predict_cached(
                "mini", specs["mini"], base_config, stock_data, code_to_name, signal_date, future_dates, cache_dir, predictors, config
            )
            mini_candidates = mini[
                (mini["mini_rank"] <= config.mini_top_n)
                & (mini["predicted_return"] >= config.mini_min_return)
            ].copy() if not mini.empty else pd.DataFrame()

            if mini_candidates.empty:
                small = pd.DataFrame()
                base = pd.DataFrame()
                merged = pd.DataFrame()
            else:
                candidate_data = _subset_stock_data(stock_data, mini_candidates["ts_code"].tolist())
                small = _stage_predict_cached(
                    "small", specs["small"], base_config, candidate_data, code_to_name, signal_date, future_dates, cache_dir, predictors, config
                )
                base = _stage_predict_cached(
                    "base", specs["base"], base_config, candidate_data, code_to_name, signal_date, future_dates, cache_dir, predictors, config
                )
                merged = _merge_stage_predictions(mini_candidates, small, base)
                merged.insert(0, "signal_date_window", signal_date.date())
                all_candidates.append(merged)

            picks = _make_strategy_picks(merged, mini, config)
            periods, trades = _evaluate_picks(
                picks=picks,
                stock_data=stock_data,
                universe_codes=universe_codes,
                signal_date=signal_date,
                entry_date=entry_date,
                exit_date=exit_date,
                config=config,
                mini_candidate_count=len(mini_candidates),
            )
            all_periods.extend(periods)
            all_trades.extend(trades)
    finally:
        _release_predictors(predictors)

    periods_df = pd.DataFrame(all_periods)
    trades_df = pd.DataFrame(all_trades)
    candidates_df = pd.concat(all_candidates, ignore_index=True) if all_candidates else pd.DataFrame()
    summary_df = _summarize_periods(periods_df, config) if not periods_df.empty else pd.DataFrame()

    prefix = _safe_prefix(config.output_prefix)
    paths = {
        "summary": OUTPUT_DIR / f"{prefix}_summary.csv",
        "periods": OUTPUT_DIR / f"{prefix}_periods.csv",
        "trades": OUTPUT_DIR / f"{prefix}_trades.csv",
        "candidates": OUTPUT_DIR / f"{prefix}_candidates.csv",
    }
    save_result_csv(summary_df, str(paths["summary"]))
    save_result_csv(periods_df, str(paths["periods"]))
    save_result_csv(trades_df, str(paths["trades"]))
    save_result_csv(candidates_df, str(paths["candidates"]))

    report_path = OUTPUT_DIR / f"kronos_fusion_validation_{pd.Timestamp.today().strftime('%Y-%m-%d')}_{prefix}.md"
    report_path.write_text(_build_report(config, summary_df, periods_df, paths), encoding="utf-8")

    return {
        "summary": summary_df,
        "periods": periods_df,
        "trades": trades_df,
        "candidates": candidates_df,
        "report_path": report_path,
        "cache_dir": cache_dir,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate conservative Kronos Mini/Small/Base fusion rules")
    parser.add_argument("--horizon", type=int, default=10, help="Holding horizon in trading days")
    parser.add_argument("--max-windows", type=int, default=10, help="Number of historical windows to validate")
    parser.add_argument("--start-date", type=str, default=None, help="Earliest signal date")
    parser.add_argument("--end-signal-date", type=str, default=None, help="Latest signal date")
    parser.add_argument("--signal-dates", type=str, default=None, help="Comma-separated explicit signal dates")
    parser.add_argument("--window-step", type=int, default=None, help="Trading-day gap between windows; default equals horizon")
    parser.add_argument("--random-windows", action="store_true", help="Randomly sample windows with --window-step as minimum spacing")
    parser.add_argument("--mini-top-n", type=int, default=50, help="Mini strict candidate rank cap")
    parser.add_argument("--mini-min-return", type=float, default=0.05, help="Mini strict minimum predicted return")
    parser.add_argument("--final-top-k", type=int, default=3, help="Final holdings")
    parser.add_argument("--veto-return", type=float, default=0.0, help="Dual-negative veto threshold")
    parser.add_argument("--rank3-veto-return", type=float, default=-0.05, help="Rank3-only veto threshold for both Small and Base")
    parser.add_argument("--weight-mini", type=float, default=0.5, help="Weighted-fusion Mini weight")
    parser.add_argument("--weight-small", type=float, default=0.25, help="Weighted-fusion Small weight")
    parser.add_argument("--weight-base", type=float, default=0.25, help="Weighted-fusion Base weight")
    parser.add_argument("--include-dual-veto", action="store_true", help="Also test broader dual-veto research rules")
    parser.add_argument("--include-weighted-fusion", action="store_true", help="Also test weighted average reranking")
    parser.add_argument("--mini-lookback", type=int, default=512, help="Mini lookback")
    parser.add_argument("--mini-max-context", type=int, default=2048, help="Mini max context")
    parser.add_argument("--small-lookback", type=int, default=512, help="Small lookback")
    parser.add_argument("--small-max-context", type=int, default=512, help="Small max context")
    parser.add_argument("--base-lookback", type=int, default=512, help="Base lookback")
    parser.add_argument("--base-max-context", type=int, default=512, help="Base max context")
    parser.add_argument("--batch-size", type=int, default=32, help="Inference batch size")
    parser.add_argument("--device", type=str, default="cpu", help="Inference device")
    parser.add_argument("--sample-seed", type=int, default=42, help="Random seed")
    parser.add_argument("--temperature", type=float, default=1.0, help="Sampling temperature")
    parser.add_argument("--forecast-top-k", type=int, default=1, help="Token-level top-k")
    parser.add_argument("--forecast-top-p", type=float, default=1.0, help="Token-level top-p")
    parser.add_argument("--sample-count", type=int, default=1, help="Forecast sample count")
    parser.add_argument("--price-limit-rate", type=float, default=0.10, help="A-share daily price limit")
    parser.add_argument("--output-prefix", type=str, default="fusion_validate", help="Output prefix")
    parser.add_argument("--cache-dir", type=str, default=None, help="Prediction cache directory")
    parser.add_argument("--seed-mini-predictions-path", type=str, default=None, help="Seed one Mini cache from an existing CSV")
    parser.add_argument("--seed-small-predictions-path", type=str, default=None, help="Seed one Small cache from an existing CSV")
    parser.add_argument("--seed-base-predictions-path", type=str, default=None, help="Seed one Base cache from an existing CSV")
    parser.add_argument("--quiet", action="store_true", help="Reduce logs")
    return parser


def parse_args() -> FusionValidationConfig:
    args = build_arg_parser().parse_args()
    return FusionValidationConfig(
        horizon=args.horizon,
        max_windows=args.max_windows,
        start_date=args.start_date,
        end_signal_date=args.end_signal_date,
        signal_dates=args.signal_dates,
        window_step=args.window_step,
        random_windows=args.random_windows,
        mini_top_n=args.mini_top_n,
        mini_min_return=args.mini_min_return,
        final_top_k=args.final_top_k,
        veto_return=args.veto_return,
        rank3_veto_return=args.rank3_veto_return,
        weight_mini=args.weight_mini,
        weight_small=args.weight_small,
        weight_base=args.weight_base,
        include_dual_veto=args.include_dual_veto,
        include_weighted_fusion=args.include_weighted_fusion,
        mini_lookback=args.mini_lookback,
        mini_max_context=args.mini_max_context,
        small_lookback=args.small_lookback,
        small_max_context=args.small_max_context,
        base_lookback=args.base_lookback,
        base_max_context=args.base_max_context,
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
        seed_mini_predictions_path=args.seed_mini_predictions_path,
        seed_small_predictions_path=args.seed_small_predictions_path,
        seed_base_predictions_path=args.seed_base_predictions_path,
        quiet=args.quiet,
    )


def main() -> None:
    result = run_validation(parse_args())
    summary = result["summary"]
    if summary.empty:
        print("[fusion] no results")
    else:
        print(summary.to_string(index=False))
    print(f"[fusion] cache dir: {result['cache_dir']}")
    print(f"[fusion] report saved: {result['report_path']}")


if __name__ == "__main__":
    main()
