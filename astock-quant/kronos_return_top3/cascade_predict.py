from __future__ import annotations

import argparse
import gc
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


@dataclass(frozen=True)
class CascadeConfig:
    horizon: int = 1
    mini_top_n: int = 50
    mini_min_return: float = 0.05
    small_top_n: int = 10
    final_top_k: int = 3
    mini_lookback: int = 512
    mini_max_context: int = 2048
    small_lookback: int = 512
    small_max_context: int = 512
    base_lookback: int = 512
    base_max_context: int = 512
    batch_size: int = 32
    device: str = "cpu"
    sample_seed: int = 42
    signal_date: str | None = None
    temperature: float = 1.0
    forecast_top_k: int = 1
    forecast_top_p: float = 1.0
    sample_count: int = 1
    price_limit_rate: float = 0.10
    output_prefix: str = "cascade_latest"
    mini_predictions_path: str | None = None
    backtest: bool = False
    quiet: bool = False


@dataclass(frozen=True)
class StageSpec:
    name: str
    model_name: str
    tokenizer_name: str
    lookback: int
    max_context: int


def _stage_config(base: BacktestConfig, spec: StageSpec) -> BacktestConfig:
    return replace(
        base,
        model_name=spec.model_name,
        tokenizer_name=spec.tokenizer_name,
        lookback=spec.lookback,
        max_context=spec.max_context,
    )


def _stage_view(df: pd.DataFrame, stage: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["ts_code"])

    cols = [
        "ts_code",
        f"{stage}_rank",
        f"{stage}_predicted_return",
        f"{stage}_predicted_entry_open",
        f"{stage}_predicted_exit_close",
    ]
    out = df[
        [
            "ts_code",
            f"{stage}_rank",
            "predicted_return",
            "predicted_entry_open",
            "predicted_exit_close",
        ]
    ].copy()
    out.columns = cols
    return out


def _subset_stock_data(stock_data: dict[str, pd.DataFrame], codes: pd.Series | list[str]) -> dict[str, pd.DataFrame]:
    return {str(code): stock_data[str(code)] for code in codes if str(code) in stock_data}


def _release_predictor(predictor: object) -> None:
    del predictor
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def _run_stage(
    spec: StageSpec,
    base_config: BacktestConfig,
    stock_data: dict[str, pd.DataFrame],
    code_to_name: dict[str, str],
    signal_date: pd.Timestamp,
    future_dates: pd.DatetimeIndex,
) -> pd.DataFrame:
    config = _stage_config(base_config, spec)
    if config.verbose:
        print(
            f"[cascade] {spec.name}: model={spec.model_name}, tokenizer={spec.tokenizer_name}, "
            f"lookback={spec.lookback}, max_context={spec.max_context}, input={len(stock_data)}",
            flush=True,
        )

    predictor = load_predictor(config)
    try:
        forecasts = forecast_cross_section(
            predictor=predictor,
            stock_data=stock_data,
            signal_date=signal_date,
            future_dates=future_dates,
            config=config,
        )
    finally:
        _release_predictor(predictor)

    if forecasts.empty:
        return forecasts

    forecasts["name"] = forecasts["ts_code"].map(code_to_name).fillna("")
    forecasts[f"{spec.name}_rank"] = np.arange(1, len(forecasts) + 1)
    return forecasts


def _load_mini_predictions(path: str | Path, code_to_name: dict[str, str]) -> pd.DataFrame:
    forecasts = pd.read_csv(path, encoding="utf-8-sig")
    required = {"ts_code", "predicted_return", "predicted_entry_open", "predicted_exit_close"}
    missing = required - set(forecasts.columns)
    if missing:
        raise RuntimeError(f"Mini predictions file is missing columns: {sorted(missing)}")

    forecasts = forecasts.sort_values("predicted_return", ascending=False).reset_index(drop=True)
    if "name" not in forecasts.columns:
        forecasts["name"] = forecasts["ts_code"].map(code_to_name).fillna("")
    else:
        forecasts["name"] = forecasts["name"].fillna(forecasts["ts_code"].map(code_to_name)).fillna("")
    forecasts["mini_rank"] = np.arange(1, len(forecasts) + 1)
    return forecasts


def _build_backtest_outputs(
    final_df: pd.DataFrame,
    stock_data: dict[str, pd.DataFrame],
    universe_codes: list[str],
    entry_date: pd.Timestamp,
    exit_date: pd.Timestamp,
    config: CascadeConfig,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if final_df.empty:
        period = pd.DataFrame(
            [
                {
                    "signal_date": np.nan,
                    "entry_date": entry_date.date(),
                    "exit_date": exit_date.date(),
                    "requested_holdings": config.final_top_k,
                    "holdings": 0,
                    "skipped_holdings": config.final_top_k,
                    "execution_rate": 0.0,
                    "invested_weight": 0.0,
                    "portfolio_return": 0.0,
                    "benchmark_return": compute_benchmark(stock_data, universe_codes, entry_date, exit_date),
                    "alpha": np.nan,
                    "executed_codes": "",
                    "skipped_codes": "",
                }
            ]
        )
        return period, pd.DataFrame()

    trades: list[dict] = []
    executed_codes: list[str] = []
    skipped_codes: list[str] = []
    portfolio_return = 0.0
    signal_date = final_df["signal_date"].iloc[0] if "signal_date" in final_df.columns else np.nan

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
                "signal_date": signal_date,
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
                "entry_price": round(res["entry_price"], 4),
                "exit_price": round(res["exit_price"], 4),
                "trade_return": round(res["trade_return"], 6),
                "portfolio_weight": round(weight, 6),
                "return_contribution": round(contribution, 6),
                "max_runup": round(res["max_runup"], 6),
                "max_drawdown": round(res["max_drawdown"], 6),
            }
        )

    benchmark_return = compute_benchmark(stock_data, universe_codes, entry_date, exit_date)
    alpha = portfolio_return - benchmark_return if pd.notna(benchmark_return) else np.nan
    period = pd.DataFrame(
        [
            {
                "signal_date": signal_date,
                "entry_date": entry_date.date(),
                "exit_date": exit_date.date(),
                "requested_holdings": config.final_top_k,
                "holdings": len(executed_codes),
                "skipped_holdings": config.final_top_k - len(executed_codes),
                "execution_rate": round(len(executed_codes) / config.final_top_k, 6) if config.final_top_k else 0.0,
                "invested_weight": round(len(executed_codes) / config.final_top_k, 6) if config.final_top_k else 0.0,
                "portfolio_return": round(portfolio_return, 6),
                "benchmark_return": round(float(benchmark_return), 6) if pd.notna(benchmark_return) else np.nan,
                "alpha": round(float(alpha), 6) if pd.notna(alpha) else np.nan,
                "executed_codes": ",".join(executed_codes),
                "skipped_codes": ",".join(skipped_codes),
            }
        ]
    )
    return period, pd.DataFrame(trades)


def _build_summary_rows(
    config: CascadeConfig,
    specs: dict[str, StageSpec],
    signal_date: pd.Timestamp,
    future_dates: pd.DatetimeIndex,
    universe_size: int,
    mini_count: int,
    mini_selected_count: int,
    small_count: int,
    small_selected_count: int,
    base_count: int,
    final_count: int,
) -> pd.DataFrame:
    rows = [
        {
            "stage": "mini",
            "model_name": specs["mini"].model_name,
            "tokenizer_name": specs["mini"].tokenizer_name,
            "lookback": specs["mini"].lookback,
            "max_context": specs["mini"].max_context,
            "input_count": universe_size,
            "output_count": mini_count,
            "selected_count": mini_selected_count,
            "selection_rule": f"rank <= {config.mini_top_n} and predicted_return >= {config.mini_min_return:.2%}; strict no refill",
        },
        {
            "stage": "small",
            "model_name": specs["small"].model_name,
            "tokenizer_name": specs["small"].tokenizer_name,
            "lookback": specs["small"].lookback,
            "max_context": specs["small"].max_context,
            "input_count": mini_selected_count,
            "output_count": small_count,
            "selected_count": small_selected_count,
            "selection_rule": f"top {config.small_top_n}; if fewer, pass fewer",
        },
        {
            "stage": "base",
            "model_name": specs["base"].model_name,
            "tokenizer_name": specs["base"].tokenizer_name,
            "lookback": specs["base"].lookback,
            "max_context": specs["base"].max_context,
            "input_count": small_selected_count,
            "output_count": base_count,
            "selected_count": final_count,
            "selection_rule": f"top {config.final_top_k}; if fewer, return fewer",
        },
    ]
    summary = pd.DataFrame(rows)
    summary.insert(0, "signal_date", signal_date.strftime("%Y-%m-%d"))
    summary.insert(1, "entry_date", future_dates[0].strftime("%Y-%m-%d"))
    summary.insert(2, "exit_date", future_dates[-1].strftime("%Y-%m-%d"))
    summary.insert(3, "horizon", config.horizon)
    return summary


def _build_report(
    config: CascadeConfig,
    specs: dict[str, StageSpec],
    signal_date: pd.Timestamp,
    future_dates: pd.DatetimeIndex,
    summary_df: pd.DataFrame,
    mini_candidates: pd.DataFrame,
    small_shortlist: pd.DataFrame,
    final_df: pd.DataFrame,
    artifact_paths: dict[str, Path],
    periods_df: pd.DataFrame | None = None,
    trades_df: pd.DataFrame | None = None,
) -> str:
    today = pd.Timestamp.today().strftime("%Y-%m-%d")
    lines: list[str] = [
        f"# Kronos Cascade Top{config.final_top_k} 推荐 - {today}",
        "",
        f"- 上游模型来源: {MODEL_SOURCE_URL}",
        f"- 信号日: {signal_date.strftime('%Y-%m-%d')}",
        f"- 预测入场: {future_dates[0].strftime('%Y-%m-%d')}",
        f"- 预测离场: {future_dates[-1].strftime('%Y-%m-%d')}（{config.horizon} 个工作日）",
        f"- 严格阈值: Mini 只保留排名前 {config.mini_top_n} 且预测收益 >= {_fmt_pct(config.mini_min_return)} 的股票；不足不补位",
        "",
        "## 模型级联",
        "",
        "| 阶段 | 模型 | 分词器 | lookback | max_context | 输入 | 输出 | 入下一阶段 | 规则 |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for _, row in summary_df.iterrows():
        lines.append(
            f"| {row['stage']} | `{row['model_name']}` | `{row['tokenizer_name']}` | "
            f"{int(row['lookback'])} | {int(row['max_context'])} | {int(row['input_count'])} | "
            f"{int(row['output_count'])} | {int(row['selected_count'])} | {row['selection_rule']} |"
        )

    lines += [
        "",
        f"## 最终 Top{config.final_top_k}",
        "",
    ]
    if final_df.empty:
        lines.append("本次没有股票通过级联筛选。")
    else:
        lines += [
            "| 排名 | 代码 | 名称 | Mini排名 | Mini预测 | Small排名 | Small预测 | Base排名 | Base预测 | Base预测开盘 | Base预测收盘 |",
            "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for rank, (_, row) in enumerate(final_df.iterrows(), start=1):
            lines.append(
                f"| {rank} | {row['ts_code']} | {row.get('name', '')} | "
                f"{_fmt_float(row.get('mini_rank'), 0)} | {_fmt_pct(row.get('mini_predicted_return'))} | "
                f"{_fmt_float(row.get('small_rank'), 0)} | {_fmt_pct(row.get('small_predicted_return'))} | "
                f"{_fmt_float(row.get('base_rank'), 0)} | {_fmt_pct(row.get('base_predicted_return'))} | "
                f"{_fmt_float(row.get('base_predicted_entry_open'))} | {_fmt_float(row.get('base_predicted_exit_close'))} |"
            )

    if "periods" in artifact_paths:
        period = periods_df.iloc[0] if periods_df is not None and not periods_df.empty else None
        lines += [
            "",
            "## 一期回测",
            "",
            f"- 交易窗口: {future_dates[0].strftime('%Y-%m-%d')} 开盘买入，{future_dates[-1].strftime('%Y-%m-%d')} 收盘卖出",
        ]
        if period is not None:
            lines += [
                f"- 组合收益: {_fmt_pct(period['portfolio_return'])}",
                f"- 全量等权基准: {_fmt_pct(period['benchmark_return'])}",
                f"- Alpha: {_fmt_pct(period['alpha'])}",
                f"- 成交: {int(period['holdings'])}/{int(period['requested_holdings'])}",
                "",
            ]
        if trades_df is not None and not trades_df.empty:
            lines += [
                "| 排名 | 代码 | 名称 | 实际收益 | 权重贡献 | 入场价 | 离场价 | 最大浮盈 | 最大浮亏 |",
                "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
            for _, row in trades_df.iterrows():
                lines.append(
                    f"| {int(row['final_rank'])} | {row['ts_code']} | {row.get('name', '')} | "
                    f"{_fmt_pct(row['trade_return'])} | {_fmt_pct(row['return_contribution'])} | "
                    f"{_fmt_float(row['entry_price'])} | {_fmt_float(row['exit_price'])} | "
                    f"{_fmt_pct(row['max_runup'])} | {_fmt_pct(row['max_drawdown'])} |"
                )
        lines += [
            "",
            f"- 回测明细: `{artifact_paths['periods'].as_posix()}`",
            f"- 交易明细: `{artifact_paths['trades'].as_posix()}`",
        ]

    lines += [
        "",
        "## Mini 严格阈值候选",
        "",
    ]
    if mini_candidates.empty:
        lines.append("Mini 阶段没有股票同时满足排名和收益阈值。")
    else:
        lines += [
            "| Mini排名 | 代码 | 名称 | 预测收益 | 信号日收盘 | 预测开盘 | 预测收盘 |",
            "| ---: | --- | --- | ---: | ---: | ---: | ---: |",
        ]
        for _, row in mini_candidates.head(20).iterrows():
            lines.append(
                f"| {int(row['mini_rank'])} | {row['ts_code']} | {row.get('name', '')} | "
                f"{_fmt_pct(row['predicted_return'])} | {_fmt_float(row['signal_close'])} | "
                f"{_fmt_float(row['predicted_entry_open'])} | {_fmt_float(row['predicted_exit_close'])} |"
            )

    lines += [
        "",
        "## Small 入 Base 候选",
        "",
    ]
    if small_shortlist.empty:
        lines.append("Small 阶段没有可传入 Base 的候选。")
    else:
        lines += [
            "| Small排名 | 代码 | 名称 | 预测收益 | 信号日收盘 | 预测开盘 | 预测收盘 |",
            "| ---: | --- | --- | ---: | ---: | ---: | ---: |",
        ]
        for _, row in small_shortlist.iterrows():
            lines.append(
                f"| {int(row['small_rank'])} | {row['ts_code']} | {row.get('name', '')} | "
                f"{_fmt_pct(row['predicted_return'])} | {_fmt_float(row['signal_close'])} | "
                f"{_fmt_float(row['predicted_entry_open'])} | {_fmt_float(row['predicted_exit_close'])} |"
            )

    lines += [
        "",
        "## 输出文件",
        "",
    ]
    for label, path in artifact_paths.items():
        lines.append(f"- {label}: `{path.as_posix()}`")

    lines += ["", "## 说明", ""]
    if config.backtest:
        lines.append("- 历史一期回测使用真实交易日窗口，并用真实入场开盘价/离场收盘价计算收益。")
    else:
        lines.append("- 该脚本为推断模式，不使用未来真实价格，因此不产生回测收益指标。")
    lines += [
        "- 预测 OHLC 已按 A 股 +/-10% 日涨跌停路径裁剪。",
        "- Mini 阈值采用严格模式：若满足 `TopN + 最低收益` 的股票不足 N，只减少候选数量，不向后补位。",
        "",
    ]
    return "\n".join(lines)


def run_cascade(config: CascadeConfig) -> dict[str, object]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    set_seed(config.sample_seed)

    specs = {
        "mini": StageSpec("mini", FAST_MODEL_NAME, FAST_TOKENIZER_NAME, config.mini_lookback, config.mini_max_context),
        "small": StageSpec("small", SMALL_MODEL_NAME, DEFAULT_TOKENIZER_NAME, config.small_lookback, config.small_max_context),
        "base": StageSpec("base", DEFAULT_MODEL_NAME, DEFAULT_TOKENIZER_NAME, config.base_lookback, config.base_max_context),
    }

    base_config = BacktestConfig(
        start_date="2025-01-01",
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

    universe_df, stock_data = select_universe(base_config)
    trading_dates = build_trading_dates(stock_data)
    signal_date = pd.Timestamp(config.signal_date) if config.signal_date else trading_dates[-1]
    if signal_date not in trading_dates:
        raise RuntimeError(f"signal_date {signal_date.date()} is not available in the selected universe.")
    if config.backtest:
        signal_idx = int(trading_dates.get_loc(signal_date))
        future_dates = trading_dates[signal_idx + 1 : signal_idx + 1 + config.horizon]
        if len(future_dates) < config.horizon:
            raise RuntimeError("signal_date is too late to form a complete backtest window.")
    else:
        future_dates = pd.bdate_range(start=signal_date + pd.Timedelta(days=1), periods=config.horizon)
    code_to_name = dict(zip(universe_df["ts_code"], universe_df["name"]))

    if not config.quiet:
        print(
            f"[cascade] signal={signal_date.date()} entry={future_dates[0].date()} "
            f"exit={future_dates[-1].date()} universe={len(universe_df)}",
            flush=True,
        )

    if config.mini_predictions_path:
        if not config.quiet:
            print(f"[cascade] mini: reuse predictions={config.mini_predictions_path}", flush=True)
        mini = _load_mini_predictions(config.mini_predictions_path, code_to_name)
    else:
        mini = _run_stage(specs["mini"], base_config, stock_data, code_to_name, signal_date, future_dates)
    mini_candidates = mini[
        (mini["mini_rank"] <= config.mini_top_n)
        & (mini["predicted_return"] >= config.mini_min_return)
    ].copy() if not mini.empty else pd.DataFrame()

    small = pd.DataFrame()
    small_shortlist = pd.DataFrame()
    if not mini_candidates.empty:
        small_data = _subset_stock_data(stock_data, mini_candidates["ts_code"].tolist())
        small = _run_stage(specs["small"], base_config, small_data, code_to_name, signal_date, future_dates)
        small_shortlist = small.head(config.small_top_n).copy() if not small.empty else pd.DataFrame()

    base = pd.DataFrame()
    final = pd.DataFrame()
    if not small_shortlist.empty:
        base_data = _subset_stock_data(stock_data, small_shortlist["ts_code"].tolist())
        base = _run_stage(specs["base"], base_config, base_data, code_to_name, signal_date, future_dates)
        final = base.head(config.final_top_k).copy() if not base.empty else pd.DataFrame()

    mini_view = _stage_view(mini, "mini")
    small_view = _stage_view(small, "small")
    base_view = _stage_view(base, "base")
    if not final.empty:
        final = (
            final[["ts_code", "name", "signal_date", "signal_close"]]
            .merge(mini_view, on="ts_code", how="left")
            .merge(small_view, on="ts_code", how="left")
            .merge(base_view, on="ts_code", how="left")
        )
        final.insert(0, "final_rank", np.arange(1, len(final) + 1))

    summary = _build_summary_rows(
        config=config,
        specs=specs,
        signal_date=signal_date,
        future_dates=future_dates,
        universe_size=len(universe_df),
        mini_count=len(mini),
        mini_selected_count=len(mini_candidates),
        small_count=len(small),
        small_selected_count=len(small_shortlist),
        base_count=len(base),
        final_count=len(final),
    )

    prefix = _safe_prefix(config.output_prefix)
    artifact_paths = {
        "summary": OUTPUT_DIR / f"{prefix}_summary.csv",
        "mini_predictions": OUTPUT_DIR / f"{prefix}_mini_predictions.csv",
        "mini_candidates": OUTPUT_DIR / f"{prefix}_mini_candidates.csv",
        "small_predictions": OUTPUT_DIR / f"{prefix}_small_predictions.csv",
        "small_shortlist": OUTPUT_DIR / f"{prefix}_small_shortlist.csv",
        "base_predictions": OUTPUT_DIR / f"{prefix}_base_predictions.csv",
        "final_top": OUTPUT_DIR / f"{prefix}_final_top{config.final_top_k}.csv",
    }

    periods = pd.DataFrame()
    trades = pd.DataFrame()
    if config.backtest:
        periods, trades = _build_backtest_outputs(
            final_df=final,
            stock_data=stock_data,
            universe_codes=universe_df["ts_code"].astype(str).tolist(),
            entry_date=future_dates[0],
            exit_date=future_dates[-1],
            config=config,
        )
        artifact_paths["periods"] = OUTPUT_DIR / f"{prefix}_periods.csv"
        artifact_paths["trades"] = OUTPUT_DIR / f"{prefix}_trades.csv"

    save_result_csv(summary, str(artifact_paths["summary"]))
    save_result_csv(mini, str(artifact_paths["mini_predictions"]))
    save_result_csv(mini_candidates, str(artifact_paths["mini_candidates"]))
    save_result_csv(small, str(artifact_paths["small_predictions"]))
    save_result_csv(small_shortlist, str(artifact_paths["small_shortlist"]))
    save_result_csv(base, str(artifact_paths["base_predictions"]))
    save_result_csv(final, str(artifact_paths["final_top"]))
    if config.backtest:
        save_result_csv(periods, str(artifact_paths["periods"]))
        save_result_csv(trades, str(artifact_paths["trades"]))

    report_path = OUTPUT_DIR / f"kronos_cascade_{pd.Timestamp.today().strftime('%Y-%m-%d')}_{prefix}.md"
    report = _build_report(
        config=config,
        specs=specs,
        signal_date=signal_date,
        future_dates=future_dates,
        summary_df=summary,
        mini_candidates=mini_candidates,
        small_shortlist=small_shortlist,
        final_df=final,
        artifact_paths=artifact_paths,
        periods_df=periods,
        trades_df=trades,
    )
    report_path.write_text(report, encoding="utf-8")

    return {
        "summary": summary,
        "mini": mini,
        "mini_candidates": mini_candidates,
        "small": small,
        "small_shortlist": small_shortlist,
        "base": base,
        "final": final,
        "periods": periods,
        "trades": trades,
        "report_path": report_path,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Kronos mini -> small -> base cascade predictor")
    parser.add_argument("--horizon", type=int, default=1, help="Forecast horizon in business days")
    parser.add_argument("--mini-top-n", type=int, default=50, help="Mini stage maximum rank to pass")
    parser.add_argument("--mini-min-return", type=float, default=0.05, help="Strict Mini minimum predicted return")
    parser.add_argument("--small-top-n", type=int, default=10, help="Small stage maximum rank to pass to base")
    parser.add_argument("--final-top-k", type=int, default=3, help="Final base-ranked holdings")
    parser.add_argument("--mini-lookback", type=int, default=512, help="Mini historical lookback")
    parser.add_argument("--mini-max-context", type=int, default=2048, help="Mini model max context")
    parser.add_argument("--small-lookback", type=int, default=512, help="Small historical lookback")
    parser.add_argument("--small-max-context", type=int, default=512, help="Small model max context")
    parser.add_argument("--base-lookback", type=int, default=512, help="Base historical lookback")
    parser.add_argument("--base-max-context", type=int, default=512, help="Base model max context")
    parser.add_argument("--batch-size", type=int, default=32, help="Inference batch size")
    parser.add_argument("--device", type=str, default="cpu", help="Inference device, e.g. cpu / cuda:0")
    parser.add_argument("--sample-seed", type=int, default=42, help="Random seed")
    parser.add_argument("--signal-date", type=str, default=None, help="Optional signal date YYYY-MM-DD; default latest")
    parser.add_argument("--temperature", type=float, default=1.0, help="Sampling temperature")
    parser.add_argument("--forecast-top-k", type=int, default=1, help="Token-level top-k during generation")
    parser.add_argument("--forecast-top-p", type=float, default=1.0, help="Token-level top-p during generation")
    parser.add_argument("--sample-count", type=int, default=1, help="Forecast path sample count")
    parser.add_argument("--price-limit-rate", type=float, default=0.10, help="A-share daily price limit rate")
    parser.add_argument("--output-prefix", type=str, default="cascade_latest", help="Output artifact prefix")
    parser.add_argument("--mini-predictions-path", type=str, default=None, help="Reuse an existing Mini predictions CSV")
    parser.add_argument("--backtest", action="store_true", help="Use real trading dates and compute one-window realized returns")
    parser.add_argument("--quiet", action="store_true", help="Reduce console logs")
    return parser


def parse_args() -> CascadeConfig:
    args = build_arg_parser().parse_args()
    return CascadeConfig(
        horizon=args.horizon,
        mini_top_n=args.mini_top_n,
        mini_min_return=args.mini_min_return,
        small_top_n=args.small_top_n,
        final_top_k=args.final_top_k,
        mini_lookback=args.mini_lookback,
        mini_max_context=args.mini_max_context,
        small_lookback=args.small_lookback,
        small_max_context=args.small_max_context,
        base_lookback=args.base_lookback,
        base_max_context=args.base_max_context,
        batch_size=args.batch_size,
        device=args.device,
        sample_seed=args.sample_seed,
        signal_date=args.signal_date,
        temperature=args.temperature,
        forecast_top_k=args.forecast_top_k,
        forecast_top_p=args.forecast_top_p,
        sample_count=args.sample_count,
        price_limit_rate=args.price_limit_rate,
        output_prefix=args.output_prefix,
        mini_predictions_path=args.mini_predictions_path,
        backtest=args.backtest,
        quiet=args.quiet,
    )


def main() -> None:
    result = run_cascade(parse_args())
    final = result["final"]
    summary = result["summary"]
    print(summary.to_string(index=False))
    if not result["periods"].empty:
        print(result["periods"].to_string(index=False))
    if final.empty:
        print("[cascade] no final picks")
    else:
        print(final.to_string(index=False))
    print(f"[cascade] report saved: {result['report_path']}")


if __name__ == "__main__":
    main()
