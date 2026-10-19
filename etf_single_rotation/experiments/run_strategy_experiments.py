"""Comparable exploratory ETF strategy experiments. Uses only past data for signals."""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUT = ROOT / "results"
GOLD = "518880.SH"
STOCKS = ["510300.SH", "510500.SH", "159915.SZ", "510050.SH", "510880.SH", "512100.SH", "512690.SH"]
FEE_SLIPPAGE_PER_SIDE = 0.0010  # 5 bps fee + 5 bps slippage, conservative proxy
VOL_TARGET = 0.12

def metrics(equity, daily_ret, weights, name):
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1 / 365.25)
    final = float(equity.iloc[-1])
    cagr = final ** (1 / years) - 1 if final > 0 else -1.0
    dd = equity / equity.cummax() - 1
    vol = float(daily_ret.std(ddof=1) * np.sqrt(252)) if len(daily_ret) > 1 else 0.0
    sharpe = float(daily_ret.mean() / daily_ret.std(ddof=1) * np.sqrt(252)) if daily_ret.std(ddof=1) else 0.0
    return {
        "strategy": name, "start": equity.index[0].date().isoformat(),
        "end": equity.index[-1].date().isoformat(), "sessions": int(len(equity)),
        "total_return": final - 1, "cagr": cagr, "max_drawdown": float(dd.min()),
        "annualized_volatility": vol, "sharpe_rf0": sharpe,
        "exposure_mean": float(weights.sum(axis=1).mean()),
        "cash_session_pct": float((weights.sum(axis=1) <= 1e-12).mean()),
        "turnover_events": int((weights.diff().abs().sum(axis=1) > 1e-12).sum()),
        "acceptance": "FAIL_DRAWDOWN" if dd.min() < -0.20 else ("FAIL_NONPOSITIVE_CAGR" if cagr <= 0 else "PRELIMINARY_ONLY"),
    }

def momentum_score(hist, sym):
    s = hist[sym].dropna()
    if len(s) < 127:
        return None
    px = float(s.iloc[-1])
    sma120 = float(s.iloc[-120:].mean())
    if px <= sma120:
        return None
    return 0.2 * (px / float(s.iloc[-21]) - 1) + 0.5 * (px / float(s.iloc[-63]) - 1) + 0.3 * (px / float(s.iloc[-127]) - 1)

def build_monthly_targets(prices, strategy):
    targets = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    vol_daily = prices.pct_change(fill_method=None).rolling(20, min_periods=20).std() * np.sqrt(252)
    month_last = prices.groupby([prices.index.year, prices.index.month]).tail(1).index
    signals = []
    stopped_until_month = None
    for d in month_last:
        hist = prices.loc[:d]
        scores = {s: momentum_score(hist, s) for s in prices.columns}
        scores = {s: v for s, v in scores.items() if v is not None and np.isfinite(v)}
        target = None
        exposure = 1.0
        regime = "RISK_ON"
        if strategy == "trend_filter":
            if scores:
                target = max(scores, key=scores.get)
        elif strategy == "volatility_control":
            if scores:
                target = max(scores, key=scores.get)
                v = float(vol_daily.loc[d, target]) if target in vol_daily.columns and pd.notna(vol_daily.loc[d, target]) else np.nan
                exposure = min(1.0, VOL_TARGET / v) if np.isfinite(v) and v > 0 else 0.0
                if exposure < 0.05:
                    target, exposure = None, 0.0
        elif strategy == "cross_asset_defensive":
            stock_scores = {s: scores[s] for s in STOCKS if s in scores}
            if stock_scores:
                target = max(stock_scores, key=stock_scores.get)
            else:
                gold_score = momentum_score(hist, GOLD) if GOLD in prices.columns else None
                if gold_score is not None:
                    target, regime = GOLD, "DEFENSIVE_GOLD"
                else:
                    regime = "CASH"
        elif strategy in {"regime_switch", "regime_switch_lowvol", "regime_switch_ultralow", "regime_dd_guard15", "regime_dd_guard20"}:
            broad = [s for s in ["510300.SH", "510500.SH", "510050.SH"] if s in hist.columns]
            above = []
            for sym in STOCKS:
                ss = hist[sym].dropna() if sym in hist.columns else pd.Series(dtype=float)
                if len(ss) >= 120:
                    above.append(float(ss.iloc[-1]) > float(ss.iloc[-120:].mean()))
            broad_bear = True
            for sym in broad:
                ss = hist[sym].dropna()
                if len(ss) >= 120 and float(ss.iloc[-1]) > float(ss.iloc[-120:].mean()):
                    broad_bear = False
                    break
            risk_off = bool(broad_bear and (len(above) == 0 or sum(above) < max(2, int(len(above) * 0.4))))
            if risk_off:
                gs = momentum_score(hist, GOLD) if GOLD in prices.columns else None
                if gs is not None:
                    target, regime = GOLD, "RISK_OFF_GOLD"
                else:
                    regime = "RISK_OFF_CASH"
            elif scores:
                target = max(scores, key=scores.get)
            if target is not None and target != GOLD:
                v = float(vol_daily.loc[d, target]) if target in vol_daily.columns and pd.notna(vol_daily.loc[d, target]) else np.nan
                vol_target = 0.06 if strategy == "regime_switch_ultralow" else (0.08 if strategy == "regime_switch_lowvol" or strategy.startswith("regime_dd_guard") else VOL_TARGET)
                exposure = min(1.0, vol_target / v) if np.isfinite(v) and v > 0 else 1.0
        if target is not None:
            # Signal made at this close. It becomes active next observed session only.
            effective = prices.index[prices.index.get_loc(d) + 1] if prices.index.get_loc(d) + 1 < len(prices.index) else None
            if effective is not None:
                targets.loc[effective:, :] = 0.0
                targets.loc[effective:, target] = exposure
        signals.append({"signal_date": d.date().isoformat(), "strategy": strategy, "target": target or "CASH",
                        "exposure": exposure if target is not None else 0.0, "regime": regime})
    return targets, signals

def run_one(prices, strategy):
    targets, signals = build_monthly_targets(prices, strategy)
    returns = prices.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    desired = targets.reindex_like(returns).fillna(0.0)
    # Predeclared portfolio-level circuit breaker. Trigger uses only the prior close;
    # the next day's target is held in cash for 20 sessions, then normal monthly
    # regime signals may resume. This is an experiment, not a guaranteed drawdown cap.
    guard_threshold = 0.15 if strategy == "regime_dd_guard15" else (0.20 if strategy == "regime_dd_guard20" else None)
    actual_rows, ret_values, equity_values = [], [], []
    prev_w = pd.Series(0.0, index=returns.columns)
    equity_value, peak_value, cooldown = 1.0, 1.0, 0
    for i, date in enumerate(returns.index):
        if i > 0:
            prev_dd = equity_value / peak_value - 1.0 if peak_value else 0.0
            if guard_threshold is not None and prev_dd <= -guard_threshold:
                cooldown = max(cooldown, 20)
        if cooldown > 0:
            current_w = pd.Series(0.0, index=returns.columns)
            cooldown -= 1
        else:
            current_w = desired.iloc[i].copy()
        turnover = float((current_w - prev_w).abs().sum())
        daily_ret = float((prev_w * returns.iloc[i]).sum() - turnover * FEE_SLIPPAGE_PER_SIDE)
        equity_value *= (1.0 + daily_ret)
        peak_value = max(peak_value, equity_value)
        actual_rows.append(current_w)
        ret_values.append(daily_ret)
        equity_values.append(equity_value)
        prev_w = current_w
    actual_weights = pd.DataFrame(actual_rows, index=returns.index, columns=returns.columns)
    portfolio_ret = pd.Series(ret_values, index=returns.index)
    equity = pd.Series(equity_values, index=returns.index)
    return metrics(equity, portfolio_ret, actual_weights.shift(1).fillna(0.0), strategy), equity, actual_weights.shift(1).fillna(0.0), signals

def main():
    source = DATA_DIR / "prices.csv"
    if not source.exists():
        raise SystemExit("Missing real prices.csv; refusing to fabricate data.")
    df = pd.read_csv(source, parse_dates=["date"])
    required = {"date", "symbol", "close"}
    if not required.issubset(df.columns):
        raise SystemExit(f"Missing required columns: {sorted(required - set(df.columns))}")
    if df.duplicated(["date", "symbol"]).any():
        raise SystemExit("Duplicate ETF/date rows; resolve data quality before experiments.")
    prices = df.pivot(index="date", columns="symbol", values="close").sort_index()
    if prices.empty or prices.notna().sum().min() == 0:
        raise SystemExit("Empty or unusable ETF panel.")
    prices = prices.ffill()  # valuation returns only; data gaps are separately flagged and acceptance remains preliminary.
    OUT.mkdir(parents=True, exist_ok=True)
    strategy_names = ["trend_filter", "volatility_control", "cross_asset_defensive", "regime_switch", "regime_switch_lowvol", "regime_switch_ultralow", "regime_dd_guard15", "regime_dd_guard20"]
    summaries, curves, signals_all, weights_all = [], [], [], []
    for strategy in strategy_names:
        summary, equity, weights, signals = run_one(prices, strategy)
        summaries.append(summary)
        curves.append(pd.DataFrame({"date": equity.index, "strategy": strategy, "equity": equity.values}))
        signals_all.extend(signals)
        w = weights.copy()
        w["date"] = w.index
        w["strategy"] = strategy
        weights_all.append(w.reset_index(drop=True))
        print("EXPERIMENT " + json.dumps(summary, ensure_ascii=False))
    pd.DataFrame(summaries).to_csv(OUT / "strategy_comparison.csv", index=False)
    pd.concat(curves, ignore_index=True).to_csv(OUT / "strategy_equity_curves.csv", index=False)
    pd.DataFrame(signals_all).to_csv(OUT / "strategy_monthly_signals.csv", index=False)
    pd.concat(weights_all, ignore_index=True).to_csv(OUT / "strategy_daily_weights.csv", index=False)
    audit_path = OUT / "data_quality_summary.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8")) if audit_path.exists() else {}
    manifest = {
        "purpose": "exploratory comparison only; not an accepted investment strategy",
        "strategies": strategy_names, "etf_count": int(prices.shape[1]),
        "data_quality_acceptance": audit.get("acceptance", "NOT_AVAILABLE"),
        "data_gaps_present": audit.get("calendar_missing_sessions_detected", None),
        "cost_model": "0.10% per 100% change in portfolio weight (5bp fee + 5bp slippage proxy)",
        "signal_timing": "monthly close signal active from next observed date; daily returns applied to prior-day weights",
        "limitations": [
            "Current adjusted-price total-return semantics are not independently verified.",
            "Missing dates are forward-filled for valuation only and remain under data-quality review.",
            "No hyperparameter optimization or walk-forward test is performed in this phase.",
            "ETF-level max drawdown does not guarantee portfolio drawdown control; every result remains preliminary.",
            "regime_switch_lowvol is a predeclared lower-volatility sensitivity test (8% target versus 12%), not an accepted strategy.",
            "regime_switch_ultralow is a predeclared 6% volatility-target sensitivity test; all outputs remain preliminary.",
            "regime_dd_guard15 and regime_dd_guard20 apply portfolio drawdown circuit breakers with 20-session cash cooldowns; these are not hard drawdown guarantees."
        ],
        "acceptance": "NOT_FOR_TRADING"
    }
    (OUT / "strategy_experiment_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("EXPERIMENT_MANIFEST " + json.dumps(manifest, ensure_ascii=False))

if __name__ == "__main__":
    main()
