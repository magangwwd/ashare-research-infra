from __future__ import annotations

import argparse
import math
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd

from TechnicalPatternScanner.utils import save_result_csv

from .backtest import (
    FAST_MODEL_NAME,
    FAST_TOKENIZER_NAME,
    MODEL_SOURCE_URL,
    OUTPUT_DIR,
    RANKING_FAST_RISK,
    RANKING_MINI_ONLY,
    BacktestConfig,
    _fmt_float,
    _fmt_pct,
    _progress,
    _safe_prefix,
    apply_selection_ranking,
    build_trading_dates,
    calc_trade_window,
    compute_benchmark,
    forecast_cross_section,
    load_predictor,
    select_universe,
    set_seed,
)
from .regime_gate import (
    REGIME_ACTION_NO_TRADE,
    RegimeGateConfig,
    evaluate_regime_gate,
)


STRATEGY_ADAPTIVE_GATE = "adaptive_gate"
DEFAULT_CALIBRATION_SKIP_VALUES = (35.0, 40.0, 45.0, 50.0, 55.0, 60.0)
DEFAULT_CALIBRATION_MINI_VALUES = (70.0, 75.0, 78.0, 80.0, 85.0)


@dataclass(frozen=True)
class CompareConfig:
    start_date: str = "2025-01-01"
    hold_days: int = 10
    lookback: int = 512
    max_context: int = 2048
    max_periods: int = 5
    top_k: int = 3
    batch_size: int = 32
    device: str = "cpu"
    sample_seed: int = 42
    fast_risk_lookback: int = 60
    fast_risk_candidate_pool: int = 50
    include_adaptive_gate: bool = True
    regime_skip_threshold: float = 45.0
    regime_mini_threshold: float = 80.0
    regime_hard_veto: bool = True
    regime_lookback: int = 120
    calibration_skip_values: tuple[float, ...] = DEFAULT_CALIBRATION_SKIP_VALUES
    calibration_mini_values: tuple[float, ...] = DEFAULT_CALIBRATION_MINI_VALUES
    resume: bool = False
    output_prefix: str = "ranking_compare_h10_p5"
    quiet: bool = False


def _base_backtest_config(config: CompareConfig) -> BacktestConfig:
    return BacktestConfig(
        start_date=config.start_date,
        hold_days=config.hold_days,
        lookback=config.lookback,
        max_context=config.max_context,
        sample_size=None,
        full_universe=True,
        max_periods=config.max_periods,
        sample_seed=config.sample_seed,
        top_k=config.top_k,
        batch_size=config.batch_size,
        device=config.device,
        model_name=FAST_MODEL_NAME,
        tokenizer_name=FAST_TOKENIZER_NAME,
        fast_risk_lookback=config.fast_risk_lookback,
        fast_risk_candidate_pool=config.fast_risk_candidate_pool,
        output_prefix=config.output_prefix,
        verbose=not config.quiet,
    )


def _evaluate_picks(
    strategy: str,
    ranked: pd.DataFrame,
    stock_data: dict[str, pd.DataFrame],
    universe_codes: list[str],
    signal_date: pd.Timestamp,
    entry_date: pd.Timestamp,
    exit_date: pd.Timestamp,
    top_k: int,
) -> tuple[dict, list[dict]]:
    requested = ranked.head(top_k).copy()
    benchmark_return = compute_benchmark(stock_data, universe_codes, entry_date, exit_date)
    executed_codes: list[str] = []
    skipped_codes: list[str] = []
    trades: list[dict] = []
    portfolio_return = 0.0

    for _, row in requested.iterrows():
        ts_code = str(row["ts_code"])
        res = calc_trade_window(stock_data[ts_code], entry_date, exit_date)
        if res is None:
            skipped_codes.append(ts_code)
            continue

        weight = 1.0 / top_k
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
                "selection_rank": int(row.get("selection_rank", len(trades) + 1)),
                "mini_rank": row.get("mini_rank", np.nan),
                "predicted_return": row.get("predicted_return", np.nan),
                "ranking_score": row.get("ranking_score", np.nan),
                "fast_risk_score": row.get("fast_risk_score", np.nan),
                "entry_price": round(res["entry_price"], 4),
                "exit_price": round(res["exit_price"], 4),
                "trade_return": round(res["trade_return"], 6),
                "portfolio_weight": round(weight, 6),
                "return_contribution": round(contribution, 6),
                "max_runup": round(res["max_runup"], 6),
                "max_drawdown": round(res["max_drawdown"], 6),
            }
        )

    alpha = portfolio_return - benchmark_return if pd.notna(benchmark_return) else np.nan
    period = {
        "strategy": strategy,
        "signal_date": signal_date.date(),
        "entry_date": entry_date.date(),
        "exit_date": exit_date.date(),
        "requested_holdings": top_k,
        "holdings": len(executed_codes),
        "skipped_holdings": top_k - len(executed_codes),
        "execution_rate": round(len(executed_codes) / top_k, 6) if top_k else 0.0,
        "portfolio_return": round(portfolio_return, 6),
        "benchmark_return": round(float(benchmark_return), 6) if pd.notna(benchmark_return) else np.nan,
        "alpha": round(float(alpha), 6) if pd.notna(alpha) else np.nan,
        "executed_codes": ",".join(executed_codes),
        "skipped_codes": ",".join(skipped_codes),
        "avg_requested_predicted_return": round(float(requested["predicted_return"].mean()), 6),
    }
    return period, trades


def _evaluate_no_trade(
    *,
    strategy: str,
    stock_data: dict[str, pd.DataFrame],
    universe_codes: list[str],
    signal_date: pd.Timestamp,
    entry_date: pd.Timestamp,
    exit_date: pd.Timestamp,
) -> dict:
    benchmark_return = compute_benchmark(stock_data, universe_codes, entry_date, exit_date)
    alpha = 0.0 - benchmark_return if pd.notna(benchmark_return) else np.nan
    return {
        "strategy": strategy,
        "signal_date": signal_date.date(),
        "entry_date": entry_date.date(),
        "exit_date": exit_date.date(),
        "requested_holdings": 0,
        "holdings": 0,
        "skipped_holdings": 0,
        "execution_rate": 1.0,
        "portfolio_return": 0.0,
        "benchmark_return": round(float(benchmark_return), 6) if pd.notna(benchmark_return) else np.nan,
        "alpha": round(float(alpha), 6) if pd.notna(alpha) else np.nan,
        "executed_codes": "",
        "skipped_codes": "",
        "avg_requested_predicted_return": np.nan,
    }


def _summarize(periods: pd.DataFrame, config: CompareConfig) -> pd.DataFrame:
    rows: list[dict] = []
    for strategy, group in periods.groupby("strategy", sort=False):
        returns = group["portfolio_return"].fillna(0.0)
        benchmark = group["benchmark_return"].fillna(0.0)
        equity = (1.0 + returns).cumprod()
        benchmark_equity = (1.0 + benchmark).cumprod()
        curve = pd.concat([pd.Series([1.0]), equity.reset_index(drop=True)], ignore_index=True)
        drawdown = curve / curve.cummax() - 1.0
        volatility = float(returns.std(ddof=0))
        annual_factor = math.sqrt(252.0 / config.hold_days)
        sharpe = float(returns.mean() / volatility * annual_factor) if volatility > 0 else np.nan
        rows.append(
            {
                "strategy": strategy,
                "n_periods": len(group),
                "win_rate": round(float((returns > 0).mean()), 6),
                "avg_period_return": round(float(returns.mean()), 6),
                "avg_alpha": round(float(group["alpha"].mean()), 6),
                "compound_return": round(float(equity.iloc[-1] - 1.0), 6),
                "benchmark_compound_return": round(float(benchmark_equity.iloc[-1] - 1.0), 6),
                "max_drawdown": round(float(drawdown.min()), 6),
                "sharpe": round(sharpe, 6) if pd.notna(sharpe) else np.nan,
            }
        )
    return pd.DataFrame(rows).sort_values("compound_return", ascending=False).reset_index(drop=True)


def _truthy_veto_reasons(value: object) -> bool:
    if value is None or pd.isna(value):
        return False
    text = str(value).strip()
    return bool(text and text.lower() != "nan")


def _summarize_return_series(
    returns: pd.Series,
    benchmark: pd.Series,
    *,
    hold_days: int,
) -> dict[str, float]:
    returns = pd.to_numeric(returns, errors="coerce").fillna(0.0)
    benchmark = pd.to_numeric(benchmark, errors="coerce").fillna(0.0)
    equity = (1.0 + returns).cumprod()
    benchmark_equity = (1.0 + benchmark).cumprod()
    curve = pd.concat([pd.Series([1.0]), equity.reset_index(drop=True)], ignore_index=True)
    drawdown = curve / curve.cummax() - 1.0
    volatility = float(returns.std(ddof=0))
    annual_factor = math.sqrt(252.0 / hold_days)
    sharpe = float(returns.mean() / volatility * annual_factor) if volatility > 0 else np.nan
    return {
        "n_periods": int(len(returns)),
        "win_rate": round(float((returns > 0).mean()), 6) if len(returns) else np.nan,
        "avg_period_return": round(float(returns.mean()), 6) if len(returns) else np.nan,
        "avg_alpha": round(float((returns - benchmark).mean()), 6) if len(returns) else np.nan,
        "compound_return": round(float(equity.iloc[-1] - 1.0), 6) if len(returns) else np.nan,
        "benchmark_compound_return": round(float(benchmark_equity.iloc[-1] - 1.0), 6) if len(returns) else np.nan,
        "max_drawdown": round(float(drawdown.min()), 6) if len(returns) else np.nan,
        "sharpe": round(sharpe, 6) if pd.notna(sharpe) else np.nan,
    }


def calibrate_thresholds(
    periods: pd.DataFrame,
    regimes: pd.DataFrame,
    config: CompareConfig,
) -> pd.DataFrame:
    if periods.empty or regimes.empty:
        return pd.DataFrame()

    periods = _normalize_date_columns(periods)
    regimes = _normalize_date_columns(regimes)
    wide = periods.pivot(index="signal_date", columns="strategy", values="portfolio_return").reset_index()
    bench = (
        periods[["signal_date", "benchmark_return"]]
        .drop_duplicates(subset=["signal_date"])
        .reset_index(drop=True)
    )
    wide = wide.merge(bench, on="signal_date", how="left")
    regime_cols = ["signal_date", "adapt_score", "veto_reasons"]
    wide = wide.merge(regimes[regime_cols], on="signal_date", how="inner")
    if wide.empty or RANKING_MINI_ONLY not in wide or RANKING_FAST_RISK not in wide:
        return pd.DataFrame()

    rows: list[dict] = []
    for skip_threshold in config.calibration_skip_values:
        for mini_threshold in config.calibration_mini_values:
            if mini_threshold <= skip_threshold:
                continue
            for hard_veto in [False, True]:
                selected_returns: list[float] = []
                actions: list[str] = []
                for _, row in wide.iterrows():
                    has_veto = _truthy_veto_reasons(row.get("veto_reasons"))
                    score = float(row["adapt_score"])
                    if (hard_veto and has_veto) or score < skip_threshold:
                        action = REGIME_ACTION_NO_TRADE
                        ret = 0.0
                    elif score >= mini_threshold:
                        action = RANKING_MINI_ONLY
                        ret = row[RANKING_MINI_ONLY]
                    else:
                        action = RANKING_FAST_RISK
                        ret = row[RANKING_FAST_RISK]
                    actions.append(action)
                    selected_returns.append(float(ret) if pd.notna(ret) else 0.0)

                stats = _summarize_return_series(
                    pd.Series(selected_returns),
                    pd.to_numeric(wide["benchmark_return"], errors="coerce"),
                    hold_days=config.hold_days,
                )
                rows.append(
                    {
                        "skip_threshold": skip_threshold,
                        "mini_threshold": mini_threshold,
                        "hard_veto": hard_veto,
                        "no_trade_count": actions.count(REGIME_ACTION_NO_TRADE),
                        "fast_risk_count": actions.count(RANKING_FAST_RISK),
                        "mini_only_count": actions.count(RANKING_MINI_ONLY),
                        "actions": ",".join(actions),
                        **stats,
                    }
                )

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(
        ["compound_return", "max_drawdown", "avg_alpha"],
        ascending=[False, False, False],
    ).reset_index(drop=True)


def _read_csv_if_exists(path: Path) -> pd.DataFrame:
    if path.exists() and path.stat().st_size > 0:
        return pd.read_csv(path, encoding="utf-8-sig")
    return pd.DataFrame()


def _normalize_date_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    normalized = df.copy()
    for column in ["signal_date", "entry_date", "exit_date"]:
        if column not in normalized.columns:
            continue
        parsed = pd.to_datetime(normalized[column], errors="coerce")
        valid = parsed.notna()
        normalized.loc[valid, column] = parsed.loc[valid].dt.strftime("%Y-%m-%d")
    return normalized


def _date_key_series(df: pd.DataFrame) -> pd.Series:
    return pd.to_datetime(df["signal_date"], errors="coerce").dt.strftime("%Y-%m-%d")


def _completed_signal_dates(periods: pd.DataFrame, *, include_adaptive_gate: bool) -> set[str]:
    if periods.empty or "signal_date" not in periods.columns or "strategy" not in periods.columns:
        return set()

    required = {RANKING_MINI_ONLY, RANKING_FAST_RISK}
    if include_adaptive_gate:
        required.add(STRATEGY_ADAPTIVE_GATE)

    completed: set[str] = set()
    keyed = periods[["signal_date", "strategy"]].copy()
    keyed["_signal_key"] = _date_key_series(keyed)
    keyed = keyed.dropna(subset=["_signal_key", "strategy"])
    for signal_key, group in keyed.groupby("_signal_key", sort=False):
        strategies = set(group["strategy"].astype(str))
        if required.issubset(strategies):
            completed.add(str(signal_key))
    return completed


def _filter_signal_dates(df: pd.DataFrame, signal_keys: set[str]) -> pd.DataFrame:
    if df.empty or "signal_date" not in df.columns:
        return df.copy()
    if not signal_keys:
        return df.iloc[0:0].copy()
    keys = _date_key_series(df)
    return df.loc[keys.isin(signal_keys)].copy()


def _paths_for_prefix(prefix: str) -> dict[str, Path]:
    return {
        "summary": OUTPUT_DIR / f"{prefix}_summary.csv",
        "periods": OUTPUT_DIR / f"{prefix}_periods.csv",
        "trades": OUTPUT_DIR / f"{prefix}_trades.csv",
        "candidates": OUTPUT_DIR / f"{prefix}_candidates.csv",
        "regimes": OUTPUT_DIR / f"{prefix}_regimes.csv",
        "thresholds": OUTPUT_DIR / f"{prefix}_thresholds.csv",
    }


def _save_outputs(
    paths: dict[str, Path],
    periods: pd.DataFrame,
    trades: pd.DataFrame,
    candidates: pd.DataFrame,
    regimes: pd.DataFrame,
    config: CompareConfig,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    periods = _normalize_date_columns(periods)
    trades = _normalize_date_columns(trades)
    candidates = _normalize_date_columns(candidates)
    regimes = _normalize_date_columns(regimes)
    summary = _summarize(periods, config) if not periods.empty else pd.DataFrame()
    calibration = calibrate_thresholds(periods, regimes, config)
    save_result_csv(summary, str(paths["summary"]))
    save_result_csv(periods, str(paths["periods"]))
    save_result_csv(trades, str(paths["trades"]))
    save_result_csv(candidates, str(paths["candidates"]))
    save_result_csv(regimes, str(paths["regimes"]))
    save_result_csv(calibration, str(paths["thresholds"]))
    return summary, calibration


def _build_report(
    config: CompareConfig,
    summary: pd.DataFrame,
    periods: pd.DataFrame,
    paths: dict[str, Path],
    calibration: pd.DataFrame | None = None,
) -> str:
    periods = _normalize_date_columns(periods)
    today = pd.Timestamp.today().strftime("%Y-%m-%d")
    lines = [
        f"# Kronos Ranking Comparison - {today}",
        "",
        f"- Source: {MODEL_SOURCE_URL}",
        f"- Model: `{FAST_MODEL_NAME}`",
        f"- Tokenizer: `{FAST_TOKENIZER_NAME}`",
        f"- Hold days: {config.hold_days}",
        f"- Windows: {periods['signal_date'].nunique() if not periods.empty else 0}",
        f"- Fast risk: Mini top {config.fast_risk_candidate_pool}, lookback {config.fast_risk_lookback}",
        f"- Adaptive gate: skip < {config.regime_skip_threshold:g}, mini_only >= {config.regime_mini_threshold:g}, hard veto={config.regime_hard_veto}",
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

    lines += ["", "## Latest Window", ""]
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

    if calibration is not None and not calibration.empty:
        lines += ["", "## Threshold Calibration", ""]
        lines += [
            "| Skip | Mini | Hard veto | No-trade | Fast-risk | Mini-only | Compound | Avg return | Max DD | Sharpe |",
            "| ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for _, row in calibration.head(10).iterrows():
            lines.append(
                f"| {_fmt_float(row['skip_threshold'], 0)} | {_fmt_float(row['mini_threshold'], 0)} | "
                f"{bool(row['hard_veto'])} | {int(row['no_trade_count'])} | "
                f"{int(row['fast_risk_count'])} | {int(row['mini_only_count'])} | "
                f"{_fmt_pct(row['compound_return'])} | {_fmt_pct(row['avg_period_return'])} | "
                f"{_fmt_pct(row['max_drawdown'])} | {_fmt_float(row['sharpe'])} |"
            )

    lines += ["", "## Artifacts", ""]
    for name, path in paths.items():
        lines.append(f"- {name}: `{path.as_posix()}`")
    lines.append("")
    return "\n".join(lines)


def run_comparison(config: CompareConfig) -> dict[str, object]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    set_seed(config.sample_seed)

    prefix = _safe_prefix(config.output_prefix)
    paths = _paths_for_prefix(prefix)

    existing_periods = _read_csv_if_exists(paths["periods"]) if config.resume else pd.DataFrame()
    existing_trades = _read_csv_if_exists(paths["trades"]) if config.resume else pd.DataFrame()
    existing_candidates = _read_csv_if_exists(paths["candidates"]) if config.resume else pd.DataFrame()
    existing_regimes = _read_csv_if_exists(paths["regimes"]) if config.resume else pd.DataFrame()
    completed_dates = _completed_signal_dates(existing_periods, include_adaptive_gate=config.include_adaptive_gate)
    if config.resume and completed_dates and not config.quiet:
        print(f"[compare] resume: found {len(completed_dates)} completed windows")

    existing_periods = _filter_signal_dates(existing_periods, completed_dates)
    existing_trades = _filter_signal_dates(existing_trades, completed_dates)
    existing_candidates = _filter_signal_dates(existing_candidates, completed_dates)
    existing_regimes = _filter_signal_dates(existing_regimes, completed_dates)

    base_config = _base_backtest_config(config)
    universe_df, stock_data = select_universe(base_config)
    trading_dates = build_trading_dates(stock_data)
    predictor = load_predictor(base_config)

    start_idx = int(trading_dates.searchsorted(pd.Timestamp(config.start_date)))
    period_indices = list(range(start_idx, len(trading_dates) - config.hold_days, config.hold_days))[: config.max_periods]
    code_to_name = dict(zip(universe_df["ts_code"], universe_df["name"]))
    universe_codes = universe_df["ts_code"].astype(str).tolist()

    all_periods: list[dict] = existing_periods.to_dict("records") if not existing_periods.empty else []
    all_trades: list[dict] = existing_trades.to_dict("records") if not existing_trades.empty else []
    all_candidates: list[pd.DataFrame] = [existing_candidates] if not existing_candidates.empty else []
    all_regimes: list[dict] = existing_regimes.to_dict("records") if not existing_regimes.empty else []
    gate_config = RegimeGateConfig(
        skip_threshold=config.regime_skip_threshold,
        mini_only_threshold=config.regime_mini_threshold,
        hard_veto=config.regime_hard_veto,
        lookback=config.regime_lookback,
    )

    for idx in _progress(
        period_indices,
        total=len(period_indices),
        desc="[kronos] compare",
        unit="period",
        enabled=not config.quiet,
    ):
        signal_date = trading_dates[idx]
        signal_key = signal_date.strftime("%Y-%m-%d")
        if signal_key in completed_dates:
            continue

        future_dates = trading_dates[idx + 1 : idx + 1 + config.hold_days]
        if len(future_dates) < config.hold_days:
            break
        entry_date, exit_date = future_dates[0], future_dates[-1]

        if not config.quiet:
            from tqdm.auto import tqdm

            tqdm.write(
                f"[kronos] compare signal={signal_date.date()} entry={entry_date.date()} exit={exit_date.date()}"
            )

        forecasts = forecast_cross_section(
            predictor=predictor,
            stock_data=stock_data,
            signal_date=signal_date,
            future_dates=future_dates,
            config=base_config,
        )
        if forecasts.empty:
            continue
        forecasts["name"] = forecasts["ts_code"].map(code_to_name).fillna("")

        regime = None
        if config.include_adaptive_gate:
            regime = evaluate_regime_gate(stock_data, signal_date, gate_config)
            regime_record = regime.to_record()
            regime_record.update(
                {
                    "entry_date": entry_date.date(),
                    "exit_date": exit_date.date(),
                }
            )
            all_regimes.append(regime_record)

        for strategy in [RANKING_MINI_ONLY, RANKING_FAST_RISK]:
            strategy_config = replace(base_config, ranking_mode=strategy)
            ranked = apply_selection_ranking(forecasts.copy(), stock_data, signal_date, strategy_config)
            period, trades = _evaluate_picks(
                strategy=strategy,
                ranked=ranked,
                stock_data=stock_data,
                universe_codes=universe_codes,
                signal_date=signal_date,
                entry_date=entry_date,
                exit_date=exit_date,
                top_k=config.top_k,
            )
            all_periods.append(period)
            all_trades.extend(trades)

            candidates = ranked.head(max(config.fast_risk_candidate_pool, config.top_k)).copy()
            candidates.insert(0, "strategy", strategy)
            all_candidates.append(candidates)

        if regime is not None:
            if regime.action == REGIME_ACTION_NO_TRADE:
                period = _evaluate_no_trade(
                    strategy=STRATEGY_ADAPTIVE_GATE,
                    stock_data=stock_data,
                    universe_codes=universe_codes,
                    signal_date=signal_date,
                    entry_date=entry_date,
                    exit_date=exit_date,
                )
                period.update(
                    {
                        "regime_action": regime.action,
                        "regime_score": regime.adapt_score,
                        "regime_hard_veto": regime.hard_veto,
                        "regime_veto_reasons": "; ".join(regime.veto_reasons),
                    }
                )
                all_periods.append(period)
            else:
                strategy_config = replace(base_config, ranking_mode=regime.action)
                ranked = apply_selection_ranking(forecasts.copy(), stock_data, signal_date, strategy_config)
                period, trades = _evaluate_picks(
                    strategy=STRATEGY_ADAPTIVE_GATE,
                    ranked=ranked,
                    stock_data=stock_data,
                    universe_codes=universe_codes,
                    signal_date=signal_date,
                    entry_date=entry_date,
                    exit_date=exit_date,
                    top_k=config.top_k,
                )
                period.update(
                    {
                        "regime_action": regime.action,
                        "regime_score": regime.adapt_score,
                        "regime_hard_veto": regime.hard_veto,
                        "regime_veto_reasons": "; ".join(regime.veto_reasons),
                    }
                )
                all_periods.append(period)
                for trade in trades:
                    trade.update(
                        {
                            "regime_action": regime.action,
                            "regime_score": regime.adapt_score,
                        }
                    )
                all_trades.extend(trades)

                candidates = ranked.head(max(config.fast_risk_candidate_pool, config.top_k)).copy()
                candidates.insert(0, "strategy", STRATEGY_ADAPTIVE_GATE)
                candidates.insert(1, "regime_action", regime.action)
                candidates.insert(2, "regime_score", regime.adapt_score)
                all_candidates.append(candidates)

        completed_dates.add(signal_key)
        periods = pd.DataFrame(all_periods)
        trades = pd.DataFrame(all_trades)
        candidates = pd.concat(all_candidates, ignore_index=True) if all_candidates else pd.DataFrame()
        regimes = pd.DataFrame(all_regimes)
        summary, calibration = _save_outputs(paths, periods, trades, candidates, regimes, config)
        report_path = OUTPUT_DIR / f"kronos_ranking_comparison_{pd.Timestamp.today().strftime('%Y-%m-%d')}_{prefix}.md"
        report_path.write_text(_build_report(config, summary, periods, paths, calibration), encoding="utf-8")

    periods = pd.DataFrame(all_periods)
    trades = pd.DataFrame(all_trades)
    candidates = pd.concat(all_candidates, ignore_index=True) if all_candidates else pd.DataFrame()
    regimes = pd.DataFrame(all_regimes)
    summary, calibration = _save_outputs(paths, periods, trades, candidates, regimes, config)

    report_path = OUTPUT_DIR / f"kronos_ranking_comparison_{pd.Timestamp.today().strftime('%Y-%m-%d')}_{prefix}.md"
    report_path.write_text(_build_report(config, summary, periods, paths, calibration), encoding="utf-8")

    return {
        "summary": summary,
        "periods": periods,
        "trades": trades,
        "candidates": candidates,
        "regimes": regimes,
        "thresholds": calibration,
        "report_path": report_path,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare mini_only, fast_risk, and adaptive_gate with one Mini inference pass per window"
    )
    parser.add_argument("--start-date", type=str, default="2025-01-01")
    parser.add_argument("--hold-days", type=int, default=10)
    parser.add_argument("--lookback", type=int, default=512)
    parser.add_argument("--max-context", type=int, default=2048)
    parser.add_argument("--max-periods", type=int, default=5)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--device", type=str, default="cpu")
    parser.add_argument("--sample-seed", type=int, default=42)
    parser.add_argument("--fast-risk-lookback", type=int, default=60)
    parser.add_argument("--fast-risk-candidate-pool", type=int, default=50)
    parser.add_argument("--no-adaptive-gate", action="store_true", help="Disable adaptive_gate strategy in comparison")
    parser.add_argument("--regime-skip-threshold", type=float, default=45.0)
    parser.add_argument("--regime-mini-threshold", type=float, default=80.0)
    parser.add_argument("--regime-lookback", type=int, default=120)
    parser.add_argument("--disable-regime-hard-veto", action="store_true")
    parser.add_argument(
        "--calibration-skip-values",
        type=str,
        default=",".join(f"{value:g}" for value in DEFAULT_CALIBRATION_SKIP_VALUES),
        help="Comma-separated skip thresholds for offline adaptive_gate calibration",
    )
    parser.add_argument(
        "--calibration-mini-values",
        type=str,
        default=",".join(f"{value:g}" for value in DEFAULT_CALIBRATION_MINI_VALUES),
        help="Comma-separated mini_only thresholds for offline adaptive_gate calibration",
    )
    parser.add_argument("--output-prefix", type=str, default="ranking_compare_h10_p5")
    parser.add_argument("--resume", action="store_true", help="Resume from completed windows saved under the same output prefix")
    parser.add_argument("--quiet", action="store_true")
    return parser


def _parse_float_tuple(text: str) -> tuple[float, ...]:
    values: list[float] = []
    for item in text.split(","):
        item = item.strip()
        if item:
            values.append(float(item))
    if not values:
        raise ValueError("threshold list cannot be empty")
    return tuple(values)


def parse_args() -> CompareConfig:
    args = build_arg_parser().parse_args()
    return CompareConfig(
        start_date=args.start_date,
        hold_days=args.hold_days,
        lookback=args.lookback,
        max_context=args.max_context,
        max_periods=args.max_periods,
        top_k=args.top_k,
        batch_size=args.batch_size,
        device=args.device,
        sample_seed=args.sample_seed,
        fast_risk_lookback=args.fast_risk_lookback,
        fast_risk_candidate_pool=args.fast_risk_candidate_pool,
        include_adaptive_gate=not args.no_adaptive_gate,
        regime_skip_threshold=args.regime_skip_threshold,
        regime_mini_threshold=args.regime_mini_threshold,
        regime_hard_veto=not args.disable_regime_hard_veto,
        regime_lookback=args.regime_lookback,
        calibration_skip_values=_parse_float_tuple(args.calibration_skip_values),
        calibration_mini_values=_parse_float_tuple(args.calibration_mini_values),
        resume=args.resume,
        output_prefix=args.output_prefix,
        quiet=args.quiet,
    )


def main() -> None:
    result = run_comparison(parse_args())
    summary = result["summary"]
    if summary.empty:
        print("[compare] no results")
    else:
        print(summary.to_string(index=False))
    thresholds = result.get("thresholds")
    if isinstance(thresholds, pd.DataFrame) and not thresholds.empty:
        print("\n[compare] top threshold calibration")
        print(thresholds.head(10).to_string(index=False))
    print(f"[compare] report saved: {result['report_path']}")


if __name__ == "__main__":
    main()
