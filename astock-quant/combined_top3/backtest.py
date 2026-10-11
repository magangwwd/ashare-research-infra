"""Backtest LightGBM -> Kronos-mini combined Top3 strategy.

Stage 1 filters the LightGBM universe to stocks with predicted return >= min_return,
then keeps the top N by predicted return (mirroring the daily report logic).
Stage 2 runs Kronos-mini only on those candidates and buys the top K predicted
returns for the configured hold window.
"""
from __future__ import annotations

import argparse
import gc
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from TechnicalPatternScanner.utils import save_result_csv
from lightgbm_return_top3.feature_engine import HOLD_DAYS, TARGET_COLUMN, build_rebalance_dates
from lightgbm_return_top3.model_backtest import (
    OOS_START,
    OUTPUT_DIR as LGBM_OUTPUT_DIR,
    ResearchConfig,
    compute_rank_ic_by_date,
    max_drawdown as lgbm_max_drawdown,
    prepare_ranked_panel,
    prepare_research_context,
    read_saved_validation_selection,
    run_aux_live_predictions,
    run_monthly_model_predictions,
)
from kronos_return_top3.backtest import (
    FAST_MODEL_NAME,
    FAST_TOKENIZER_NAME,
    BacktestConfig,
    apply_selection_ranking,
    build_trading_dates,
    calc_trade_window,
    compute_benchmark,
    forecast_cross_section,
    load_predictor,
    select_universe,
    set_seed,
)

PACKAGE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = PACKAGE_DIR / "output"


@dataclass(frozen=True)
class CombinedBacktestConfig:
    start_date: str = OOS_START
    end_date: str | None = None
    hold_days: int = HOLD_DAYS
    lgbm_top_n: int = 10
    top_k: int = 3
    min_return: float = 0.07
    fee_bps: float = 30.0
    workers: int = 8
    min_avg_amount: float = 20_000_000.0
    max_periods: int | None = None
    output_prefix: str = "combined_backtest"
    lookback: int = 512
    batch_size: int = 16
    device: str = "cpu"
    sample_count: int = 1
    quiet: bool = False


def _filter_stage1_candidates(predictions: pd.DataFrame, config: CombinedBacktestConfig) -> pd.DataFrame:
    scored = predictions.dropna(subset=["prediction"]).copy()
    scored = scored[scored["prediction"] >= config.min_return].copy()
    if scored.empty:
        return scored
    scored = scored.sort_values("prediction", ascending=False).reset_index(drop=True)
    scored["stage1_rank"] = np.arange(1, len(scored) + 1)
    return scored.head(config.lgbm_top_n).copy()


def _summarize(periods: pd.DataFrame) -> pd.DataFrame:
    if periods.empty:
        return pd.DataFrame()

    returns = periods["net_return"].fillna(0.0)
    gross = periods["gross_return"].fillna(0.0)
    benchmark = periods["benchmark_return"].fillna(0.0)
    stage1 = periods["stage1_top10_return"].fillna(0.0)
    equity = (1.0 + returns).cumprod()
    gross_equity = (1.0 + gross).cumprod()
    benchmark_equity = (1.0 + benchmark).cumprod()
    stage1_equity = (1.0 + stage1).cumprod()
    ret_std = returns.std(ddof=1)
    periods_per_year = 250.0 / max(int(periods["hold_days"].iloc[0]), 1)
    sharpe = float(returns.mean() / ret_std * np.sqrt(periods_per_year)) if pd.notna(ret_std) and ret_std > 0 else np.nan
    return pd.DataFrame(
        [
            {
                "strategy": "lgbm_composite_top10_to_kronos_mini_top3",
                "n_periods": int(len(periods)),
                "backtest_start": str(periods["signal_date"].min()),
                "backtest_end": str(periods["exit_date"].max()),
                "win_rate_net": round(float((returns > 0).mean()), 6),
                "positive_alpha_rate": round(float((periods["net_alpha"] > 0).mean()), 6),
                "avg_gross_return": round(float(gross.mean()), 6),
                "avg_net_return": round(float(returns.mean()), 6),
                "avg_stage1_top10_return": round(float(stage1.mean()), 6),
                "avg_benchmark_return": round(float(benchmark.mean()), 6),
                "avg_net_alpha": round(float(periods["net_alpha"].mean()), 6),
                "compound_gross": round(float(gross_equity.iloc[-1] - 1.0), 6),
                "compound_net": round(float(equity.iloc[-1] - 1.0), 6),
                "compound_stage1_top10": round(float(stage1_equity.iloc[-1] - 1.0), 6),
                "compound_benchmark": round(float(benchmark_equity.iloc[-1] - 1.0), 6),
                "max_drawdown_net": round(float(lgbm_max_drawdown(equity)), 6),
                "sharpe_net": round(sharpe, 6) if pd.notna(sharpe) else np.nan,
                "best_period_net": round(float(returns.max()), 6),
                "worst_period_net": round(float(returns.min()), 6),
            }
        ]
    )


def _build_report(config: CombinedBacktestConfig, summary: pd.DataFrame, periods: pd.DataFrame, paths: dict[str, Path]) -> str:
    lines = [
        f"# Combined Top3 Backtest - {pd.Timestamp.today().strftime('%Y-%m-%d')}",
        "",
        "## Config",
        "",
        f"- Stage 1: LightGBM predicted return ≥ {config.min_return:.0%}, Top {config.lgbm_top_n}",
        f"- Stage 2: Kronos-mini predicted-return Top {config.top_k}",
        f"- Window: {config.start_date} ~ {config.end_date or 'latest complete'}, hold {config.hold_days} trading days",
        "",
        "## Summary",
        "",
    ]
    if summary.empty:
        lines.append("No completed periods.")
    else:
        row = summary.iloc[0]
        lines += [
            "| Periods | Win | Avg Net | Compound Net | Stage1 Top10 | Benchmark | Max DD | Sharpe |",
            "|---:|---:|---:|---:|---:|---:|---:|---:|",
            (
                f"| {int(row['n_periods'])} | {row['win_rate_net']:.2%} | {row['avg_net_return']:.2%} | "
                f"{row['compound_net']:.2%} | {row['compound_stage1_top10']:.2%} | "
                f"{row['compound_benchmark']:.2%} | {row['max_drawdown_net']:.2%} | {row['sharpe_net']:.2f} |"
            ),
        ]

    lines += [
        "",
        "## Latest Periods",
        "",
        "| Signal | Entry | Exit | Net | Stage1 Top10 | Benchmark | Picks |",
        "|---|---|---|---:|---:|---:|---|",
    ]
    for _, row in periods.tail(8).iterrows():
        lines.append(
            f"| {row['signal_date']} | {row['entry_date']} | {row['exit_date']} | "
            f"{row['net_return']:.2%} | {row['stage1_top10_return']:.2%} | "
            f"{row['benchmark_return']:.2%} | {row['executed_codes']} |"
        )
    lines += [
        "",
        "## Artifacts",
        "",
        f"- Summary: `{paths['summary'].as_posix()}`",
        f"- Periods: `{paths['periods'].as_posix()}`",
        f"- Trades: `{paths['trades'].as_posix()}`",
        f"- Stage 1 candidates: `{paths['candidates'].as_posix()}`",
        "",
    ]
    return "\n".join(lines)


def run_backtest(config: CombinedBacktestConfig) -> dict[str, object]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    set_seed(42)

    if not config.quiet:
        print("[combined-backtest] preparing LightGBM panel", flush=True)
    research_config = ResearchConfig(
        fee_bps=config.fee_bps,
        workers=config.workers,
        min_avg_amount=config.min_avg_amount,
    )
    raw_panel, _metadata, lgbm_dates = prepare_research_context(research_config, verbose=not config.quiet)
    selection = read_saved_validation_selection(LGBM_OUTPUT_DIR)
    if selection is None:
        raise RuntimeError("Missing LightGBM validation_metrics.csv. Run: python -m lightgbm_return_top3.model_backtest")
    selected_template, selected_features = selection
    ranked_panel = prepare_ranked_panel(raw_panel, selected_features)

    if not config.quiet:
        print("[combined-backtest] loading Kronos universe and model", flush=True)
    kronos_config = BacktestConfig(
        start_date=config.start_date,
        hold_days=config.hold_days,
        lookback=config.lookback,
        max_context=config.lookback,
        sample_size=None,
        full_universe=True,
        top_k=config.top_k,
        batch_size=config.batch_size,
        device=config.device,
        model_name=FAST_MODEL_NAME,
        tokenizer_name=FAST_TOKENIZER_NAME,
        sample_count=config.sample_count,
        output_prefix=config.output_prefix,
        verbose=not config.quiet,
    )
    universe_df, stock_data = select_universe(kronos_config)
    kronos_dates = build_trading_dates(stock_data)
    common_dates = pd.DatetimeIndex(sorted(set(lgbm_dates).intersection(set(kronos_dates))))
    signal_dates = build_rebalance_dates(
        trading_dates=common_dates,
        start_date=config.start_date,
        end_date=config.end_date or common_dates.max(),
        step=config.hold_days,
    )
    signal_dates = pd.DatetimeIndex([d for d in signal_dates if int(common_dates.get_loc(d)) + config.hold_days < len(common_dates)])
    if config.max_periods is not None:
        signal_dates = signal_dates[: config.max_periods]
    if len(signal_dates) == 0:
        raise RuntimeError("No complete backtest windows for the requested date range.")

    lgbm_predictions, _importance = run_monthly_model_predictions(
        ranked_panel=ranked_panel,
        signal_dates=signal_dates,
        feature_columns=selected_features,
        template_name=selected_template,
        config=research_config,
        period_label="combined_oos",
        verbose=not config.quiet,
    )
    if lgbm_predictions.empty:
        raise RuntimeError("LightGBM produced no predictions for the selected signal dates.")

    predictor = load_predictor(kronos_config)
    code_to_name = dict(zip(universe_df["ts_code"].astype(str), universe_df["name"]))
    universe_codes = universe_df["ts_code"].astype(str).tolist()
    periods: list[dict] = []
    trades: list[dict] = []
    all_candidates: list[pd.DataFrame] = []

    try:
        for signal_date in signal_dates:
            idx = int(common_dates.get_loc(signal_date))
            future_dates = common_dates[idx + 1 : idx + 1 + config.hold_days]
            entry_date, exit_date = future_dates[0], future_dates[-1]
            if not config.quiet:
                print(f"[combined-backtest] signal={signal_date.date()} entry={entry_date.date()} exit={exit_date.date()}", flush=True)

            frame = lgbm_predictions[lgbm_predictions["signal_date"] == signal_date].copy()
            aux = run_aux_live_predictions(
                ranked_panel=ranked_panel,
                signal_date=signal_date,
                feature_columns=selected_features,
                template_name=selected_template,
                config=research_config,
            )
            if not aux.empty:
                frame = frame.merge(aux, on="ts_code", how="left")
            candidates = _filter_stage1_candidates(frame, config)
            if candidates.empty:
                continue
            candidates["signal_date"] = signal_date.date()
            all_candidates.append(candidates.copy())

            candidate_codes = [code for code in candidates["ts_code"].astype(str).tolist() if code in stock_data]
            subset_stock_data = {code: stock_data[code] for code in candidate_codes}
            forecasts = forecast_cross_section(predictor, subset_stock_data, signal_date, future_dates, kronos_config)
            if forecasts.empty:
                continue
            forecasts["name"] = forecasts["ts_code"].map(code_to_name).fillna("")
            forecasts = apply_selection_ranking(forecasts, subset_stock_data, signal_date, kronos_config)
            requested = forecasts.head(config.top_k).copy()

            stage1_returns: list[float] = []
            for ts_code in candidates["ts_code"].astype(str).tolist():
                if ts_code not in stock_data:
                    continue
                res = calc_trade_window(stock_data[ts_code], entry_date, exit_date)
                if res is not None:
                    stage1_returns.append(float(res["trade_return"]))
            stage1_top10_return = float(np.mean(stage1_returns)) if stage1_returns else np.nan
            benchmark_return = compute_benchmark(stock_data, universe_codes, entry_date, exit_date)

            gross_return = 0.0
            executed_codes: list[str] = []
            skipped_codes: list[str] = []
            for rank, (_, row) in enumerate(requested.iterrows(), start=1):
                ts_code = str(row["ts_code"])
                res = calc_trade_window(stock_data[ts_code], entry_date, exit_date)
                if res is None:
                    skipped_codes.append(ts_code)
                    trade_return = np.nan
                    contribution = 0.0
                else:
                    trade_return = float(res["trade_return"])
                    contribution = trade_return / config.top_k
                    gross_return += contribution
                    executed_codes.append(ts_code)
                trades.append(
                    {
                        "signal_date": signal_date.date(),
                        "entry_date": entry_date.date(),
                        "exit_date": exit_date.date(),
                        "rank": rank,
                        "ts_code": ts_code,
                        "name": row.get("name", ""),
                        "lgbm_prediction_rank": int(candidates.set_index("ts_code").loc[ts_code, "stage1_rank"]) if ts_code in set(candidates["ts_code"]) else np.nan,
                        "lgbm_prediction": float(candidates.set_index("ts_code").loc[ts_code, "prediction"]) if ts_code in set(candidates["ts_code"]) else np.nan,
                        "prob_up": float(candidates.set_index("ts_code").loc[ts_code, "prob_up"]) if "prob_up" in candidates and ts_code in set(candidates["ts_code"]) else np.nan,
                        "pred_drawdown": float(candidates.set_index("ts_code").loc[ts_code, "pred_drawdown"]) if "pred_drawdown" in candidates and ts_code in set(candidates["ts_code"]) else np.nan,
                        "kronos_predicted_return": float(row["predicted_return"]),
                        "trade_return": round(float(trade_return), 6) if pd.notna(trade_return) else np.nan,
                        "return_contribution": round(float(contribution), 6),
                    }
                )

            execution_rate = len(executed_codes) / config.top_k if config.top_k else 0.0
            turnover_fee = 2.0 * config.fee_bps / 10_000.0
            net_return = gross_return - turnover_fee
            periods.append(
                {
                    "signal_date": signal_date.date(),
                    "entry_date": entry_date.date(),
                    "exit_date": exit_date.date(),
                    "hold_days": config.hold_days,
                    "n_stage1_candidates": len(candidates),
                    "requested_holdings": config.top_k,
                    "holdings": len(executed_codes),
                    "skipped_holdings": len(skipped_codes),
                    "execution_rate": round(float(execution_rate), 6),
                    "gross_return": round(float(gross_return), 6),
                    "net_return": round(float(net_return), 6),
                    "stage1_top10_return": round(float(stage1_top10_return), 6) if pd.notna(stage1_top10_return) else np.nan,
                    "benchmark_return": round(float(benchmark_return), 6) if pd.notna(benchmark_return) else np.nan,
                    "net_alpha": round(float(net_return - benchmark_return), 6) if pd.notna(benchmark_return) else np.nan,
                    "avg_lgbm_prediction": round(float(candidates["prediction"].mean()), 6),
                    "avg_kronos_predicted_return": round(float(requested["predicted_return"].mean()), 6),
                    "executed_codes": ",".join(executed_codes),
                    "skipped_codes": ",".join(skipped_codes),
                }
            )
    finally:
        del predictor
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    periods_df = pd.DataFrame(periods)
    trades_df = pd.DataFrame(trades)
    candidates_df = pd.concat(all_candidates, ignore_index=True) if all_candidates else pd.DataFrame()
    summary_df = _summarize(periods_df)
    rank_ic_df = compute_rank_ic_by_date(lgbm_predictions)

    prefix = config.output_prefix
    paths = {
        "summary": OUTPUT_DIR / f"{prefix}_summary.csv",
        "periods": OUTPUT_DIR / f"{prefix}_periods.csv",
        "trades": OUTPUT_DIR / f"{prefix}_trades.csv",
        "candidates": OUTPUT_DIR / f"{prefix}_stage1_candidates.csv",
        "rank_ic": OUTPUT_DIR / f"{prefix}_lgbm_rank_ic.csv",
        "report": OUTPUT_DIR / f"{prefix}_report.md",
    }
    save_result_csv(summary_df, str(paths["summary"]))
    save_result_csv(periods_df, str(paths["periods"]))
    save_result_csv(trades_df, str(paths["trades"]))
    save_result_csv(candidates_df, str(paths["candidates"]))
    save_result_csv(rank_ic_df, str(paths["rank_ic"]))
    paths["report"].write_text(_build_report(config, summary_df, periods_df, paths), encoding="utf-8")

    return {
        "summary": summary_df,
        "periods": periods_df,
        "trades": trades_df,
        "candidates": candidates_df,
        "rank_ic": rank_ic_df,
        "paths": paths,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backtest LightGBM composite Top10 -> Kronos-mini Top3")
    parser.add_argument("--start-date", type=str, default=OOS_START)
    parser.add_argument("--end-date", type=str, default=None)
    parser.add_argument("--hold-days", type=int, default=HOLD_DAYS)
    parser.add_argument("--lgbm-top-n", type=int, default=10)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--min-return", type=float, default=0.07)
    parser.add_argument("--fee-bps", type=float, default=30.0)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--min-avg-amount", type=float, default=20_000_000.0)
    parser.add_argument("--max-periods", type=int, default=None)
    parser.add_argument("--output-prefix", type=str, default="combined_backtest")
    parser.add_argument("--lookback", type=int, default=512)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--device", type=str, default="cpu")
    parser.add_argument("--sample-count", type=int, default=1)
    parser.add_argument("--quiet", action="store_true")
    return parser


def parse_args() -> CombinedBacktestConfig:
    args = build_arg_parser().parse_args()
    return CombinedBacktestConfig(
        start_date=args.start_date,
        end_date=args.end_date,
        hold_days=args.hold_days,
        lgbm_top_n=args.lgbm_top_n,
        top_k=args.top_k,
        min_return=args.min_return,
        fee_bps=args.fee_bps,
        workers=args.workers,
        min_avg_amount=args.min_avg_amount,
        max_periods=args.max_periods,
        output_prefix=args.output_prefix,
        lookback=args.lookback,
        batch_size=args.batch_size,
        device=args.device,
        sample_count=args.sample_count,
        quiet=args.quiet,
    )


def main() -> None:
    result = run_backtest(parse_args())
    summary = result["summary"]
    if isinstance(summary, pd.DataFrame) and not summary.empty:
        print(summary.to_string(index=False))
    print(f"[combined-backtest] report: {result['paths']['report']}")


if __name__ == "__main__":
    main()
