from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


REGIME_ACTION_NO_TRADE = "no_trade"
REGIME_ACTION_FAST_RISK = "fast_risk"
REGIME_ACTION_MINI_ONLY = "mini_only"


@dataclass(frozen=True)
class RegimeGateConfig:
    skip_threshold: float = 45.0
    mini_only_threshold: float = 80.0
    hard_veto: bool = True
    lookback: int = 120


@dataclass(frozen=True)
class RegimeGateResult:
    signal_date: pd.Timestamp
    action: str
    adapt_score: float
    trend_score: float
    breadth_score: float
    risk_score: float
    dispersion_score: float
    liquidity_score: float
    hard_veto: bool
    veto_reasons: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)

    def to_record(self) -> dict[str, object]:
        return {
            "signal_date": self.signal_date.date(),
            "action": self.action,
            "adapt_score": round(self.adapt_score, 4),
            "trend_score": round(self.trend_score, 4),
            "breadth_score": round(self.breadth_score, 4),
            "risk_score": round(self.risk_score, 4),
            "dispersion_score": round(self.dispersion_score, 4),
            "liquidity_score": round(self.liquidity_score, 4),
            "hard_veto": self.hard_veto,
            "veto_reasons": "; ".join(self.veto_reasons),
            **{key: _round_metric(value) for key, value in self.metrics.items()},
        }


def _round_metric(value: object) -> object:
    if isinstance(value, (float, np.floating)):
        if pd.isna(value):
            return np.nan
        return round(float(value), 6)
    return value


def _clip_score(value: float) -> float:
    if pd.isna(value):
        return 50.0
    return float(np.clip(value, 0.0, 100.0))


def _score_linear(value: float, bad: float, good: float) -> float:
    if pd.isna(value):
        return 50.0
    if good == bad:
        return 50.0
    return _clip_score((float(value) - bad) / (good - bad) * 100.0)


def _score_lower_better(value: float, good: float, bad: float) -> float:
    if pd.isna(value):
        return 50.0
    if good == bad:
        return 50.0
    return _clip_score((bad - float(value)) / (bad - good) * 100.0)


def _period_return(daily_returns: pd.Series, days: int) -> float:
    window = daily_returns.dropna().tail(days)
    if window.empty:
        return np.nan
    return float((1.0 + window).prod() - 1.0)


def _max_drawdown(daily_returns: pd.Series, days: int) -> float:
    window = daily_returns.dropna().tail(days)
    if window.empty:
        return np.nan
    curve = pd.concat(
        [pd.Series([1.0]), (1.0 + window).cumprod().reset_index(drop=True)],
        ignore_index=True,
    )
    drawdown = curve / curve.cummax() - 1.0
    return float(drawdown.min())


def _valid_mean(mask: pd.Series) -> float:
    mask = mask.dropna()
    if mask.empty:
        return np.nan
    return float(mask.mean())


def _build_close_matrix(
    stock_data: dict[str, pd.DataFrame],
    signal_date: pd.Timestamp,
    lookback: int,
) -> pd.DataFrame:
    histories: dict[str, pd.Series] = {}
    rows = max(int(lookback), 80) + 65
    for ts_code, df in stock_data.items():
        hist = df[df.index <= signal_date]
        if hist.empty or "Close" not in hist.columns:
            continue
        series = pd.to_numeric(hist["Close"].tail(rows), errors="coerce")
        series = series[series > 0]
        if len(series) >= 30:
            histories[str(ts_code)] = series
    if not histories:
        return pd.DataFrame()
    closes = pd.concat(histories, axis=1).sort_index()
    closes = closes[closes.index <= signal_date]
    return closes.tail(rows)


def evaluate_regime_gate(
    stock_data: dict[str, pd.DataFrame],
    signal_date: pd.Timestamp,
    config: RegimeGateConfig,
) -> RegimeGateResult:
    closes = _build_close_matrix(stock_data, signal_date, config.lookback)
    if closes.empty:
        return RegimeGateResult(
            signal_date=signal_date,
            action=REGIME_ACTION_NO_TRADE,
            adapt_score=0.0,
            trend_score=0.0,
            breadth_score=0.0,
            risk_score=0.0,
            dispersion_score=0.0,
            liquidity_score=0.0,
            hard_veto=True,
            veto_reasons=["no usable close history"],
            metrics={"coverage_ratio": 0.0},
        )

    filled = closes.ffill()
    returns = closes.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
    market_returns = returns.mean(axis=1, skipna=True).dropna()

    current = filled.iloc[-1]
    valid_current = current.dropna()
    total_codes = max(len(stock_data), 1)
    coverage_ratio = len(valid_current) / total_codes

    market_return_5 = _period_return(market_returns, 5)
    market_return_10 = _period_return(market_returns, 10)
    market_return_20 = _period_return(market_returns, 20)
    market_return_60 = _period_return(market_returns, 60)
    market_max_drawdown_20 = _max_drawdown(market_returns, 20)
    market_volatility_20 = float(market_returns.tail(20).std(ddof=0)) if len(market_returns) >= 5 else np.nan
    crash_days_5 = int((market_returns.tail(5) <= -0.025).sum()) if not market_returns.empty else 0

    rolling_volatility_20 = market_returns.rolling(20).std(ddof=0).dropna()
    market_volatility_20_p90 = (
        float(rolling_volatility_20.quantile(0.90)) if len(rolling_volatility_20) >= 20 else np.nan
    )

    ma20 = filled.rolling(20, min_periods=20).mean().iloc[-1]
    ma60 = filled.rolling(60, min_periods=60).mean().iloc[-1]
    prev_5 = filled.shift(5).iloc[-1]
    prev_10 = filled.shift(10).iloc[-1]
    stock_return_5 = current / prev_5 - 1.0
    stock_return_10 = current / prev_10 - 1.0

    above_ma20 = (current > ma20).where(current.notna() & ma20.notna())
    above_ma60 = (current > ma60).where(current.notna() & ma60.notna())
    stock_win_5 = (stock_return_5 > 0).where(stock_return_5.notna())

    above_ma20_ratio = _valid_mean(above_ma20)
    above_ma60_ratio = _valid_mean(above_ma60)
    stock_win_5_ratio = _valid_mean(stock_win_5)
    dispersion_10 = float(stock_return_10.replace([np.inf, -np.inf], np.nan).dropna().std(ddof=0))
    enough_history_ratio = float(filled.count().ge(60).mean()) if not filled.empty else 0.0

    trend_score = (
        0.30 * _score_linear(market_return_5, bad=-0.04, good=0.04)
        + 0.45 * _score_linear(market_return_20, bad=-0.08, good=0.08)
        + 0.25 * _score_linear(market_return_60, bad=-0.12, good=0.12)
    )
    breadth_score = (
        0.30 * _clip_score(stock_win_5_ratio * 100.0)
        + 0.40 * _clip_score(above_ma20_ratio * 100.0)
        + 0.30 * _clip_score(above_ma60_ratio * 100.0)
    )
    crash_score = _clip_score(100.0 - crash_days_5 * 50.0)
    risk_score = (
        0.30 * _score_linear(market_return_10, bad=-0.10, good=0.02)
        + 0.35 * _score_linear(market_max_drawdown_20, bad=-0.12, good=-0.02)
        + 0.25 * _score_lower_better(market_volatility_20, good=0.010, bad=0.035)
        + 0.10 * crash_score
    )
    dispersion_score = _score_linear(dispersion_10, bad=0.03, good=0.10)
    liquidity_score = 0.60 * _clip_score(coverage_ratio * 100.0) + 0.40 * _clip_score(enough_history_ratio * 100.0)

    adapt_score = (
        0.25 * trend_score
        + 0.25 * breadth_score
        + 0.25 * risk_score
        + 0.15 * dispersion_score
        + 0.10 * liquidity_score
    )

    veto_reasons: list[str] = []
    if market_return_10 <= -0.08:
        veto_reasons.append("market_return_10 <= -8%")
    if market_max_drawdown_20 <= -0.12:
        veto_reasons.append("market_max_drawdown_20 <= -12%")
    if crash_days_5 >= 2:
        veto_reasons.append("crash_days_5 >= 2")
    if above_ma20_ratio < 0.10:
        veto_reasons.append("above_ma20_ratio < 10%")
    if (
        pd.notna(market_volatility_20)
        and pd.notna(market_volatility_20_p90)
        and market_volatility_20 >= market_volatility_20_p90
        and market_return_20 < 0
    ):
        veto_reasons.append("volatility_20 >= p90 and market_return_20 < 0")

    hard_veto = bool(config.hard_veto and veto_reasons)
    if hard_veto or adapt_score < config.skip_threshold:
        action = REGIME_ACTION_NO_TRADE
    elif adapt_score < config.mini_only_threshold:
        action = REGIME_ACTION_FAST_RISK
    else:
        action = REGIME_ACTION_MINI_ONLY

    metrics = {
        "market_return_5": market_return_5,
        "market_return_10": market_return_10,
        "market_return_20": market_return_20,
        "market_return_60": market_return_60,
        "market_max_drawdown_20": market_max_drawdown_20,
        "market_volatility_20": market_volatility_20,
        "market_volatility_20_p90": market_volatility_20_p90,
        "crash_days_5": float(crash_days_5),
        "above_ma20_ratio": above_ma20_ratio,
        "above_ma60_ratio": above_ma60_ratio,
        "stock_win_5_ratio": stock_win_5_ratio,
        "dispersion_10": dispersion_10,
        "coverage_ratio": coverage_ratio,
        "enough_history_ratio": enough_history_ratio,
    }

    return RegimeGateResult(
        signal_date=signal_date,
        action=action,
        adapt_score=float(round(adapt_score, 4)),
        trend_score=float(round(trend_score, 4)),
        breadth_score=float(round(breadth_score, 4)),
        risk_score=float(round(risk_score, 4)),
        dispersion_score=float(round(dispersion_score, 4)),
        liquidity_score=float(round(liquidity_score, 4)),
        hard_veto=hard_veto,
        veto_reasons=veto_reasons,
        metrics=metrics,
    )


def build_regime_markdown(
    result: RegimeGateResult,
    title: str,
    config: RegimeGateConfig | None = None,
) -> str:
    record = result.to_record()
    veto_text = record["veto_reasons"] or "-"
    lines = [
        f"# {title}",
        "",
        f"- Signal date: {result.signal_date.strftime('%Y-%m-%d')}",
        f"- Action: `{result.action}`",
        f"- Adapt score: {result.adapt_score:.2f}",
        f"- Hard veto: `{result.hard_veto}`",
        f"- Veto reasons: {veto_text}",
    ]
    if config is not None:
        lines += [
            f"- Skip threshold: {config.skip_threshold:.2f}",
            f"- Mini-only threshold: {config.mini_only_threshold:.2f}",
            f"- Hard veto enabled: `{config.hard_veto}`",
        ]
    lines += [
        "",
        "## Scores",
        "",
        "| Component | Score |",
        "| --- | ---: |",
        f"| Trend | {result.trend_score:.2f} |",
        f"| Breadth | {result.breadth_score:.2f} |",
        f"| Risk | {result.risk_score:.2f} |",
        f"| Dispersion | {result.dispersion_score:.2f} |",
        f"| Liquidity | {result.liquidity_score:.2f} |",
        "",
        "## Metrics",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
    ]
    for key, value in result.metrics.items():
        if key.startswith("market_return") or key.endswith("ratio") or key in {
            "market_max_drawdown_20",
            "market_volatility_20",
            "market_volatility_20_p90",
            "dispersion_10",
        }:
            value_text = f"{float(value):.2%}" if pd.notna(value) else "-"
        else:
            value_text = f"{float(value):.0f}" if pd.notna(value) else "-"
        lines.append(f"| {key} | {value_text} |")
    lines += [
        "",
        "## Decision Rule",
        "",
        "- no_trade: hard veto or score below skip threshold",
        "- fast_risk: score is medium-high",
        "- mini_only: score is high",
    ]
    return "\n".join(lines) + "\n"
