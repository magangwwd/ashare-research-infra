"""Independently diagnose implicit daily rebalancing in target-weight ETF backtests.

The current exploratory model subtracts fees when two successive target weights
differ. With cash and a nonzero ETF allocation, price movement changes actual
weights even if the target remains the same. Maintaining the same percentage
requires a trade, whose turnover is not included in this model.

This file is a *diagnostic*; it does not silently alter any strategy metrics.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "results"
FEE_SLIPPAGE_PER_TURNOVER = 0.0010


def compute_hidden_rebalancing(prices: pd.DataFrame, applied_weights: pd.DataFrame):
    """Audit the rebalance needed to sustain tomorrow's recorded weight.

    applied_weights[t] is the weight whose price return is earned on day t.
    In the current engine, the next row is the target after day t's close.
    Under self-financing trading, weight after today's price return drifts.
    The gap measures *uncharged* required turnover on days where the engine
    records identical before/after weights and therefore charges zero.
    """
    if not prices.index.equals(applied_weights.index):
        raise ValueError("Misaligned date indexes")
    if list(prices.columns) != list(applied_weights.columns):
        raise ValueError("Mismatched ETF columns")
    rets = prices.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    weights = applied_weights.fillna(0.0)
    if (weights < -1e-9).any().any() or weights.sum(axis=1).gt(1.0 + 1e-9).any():
        raise ValueError("Weights violate long-only unleveraged constraints")

    details = []
    for i in range(len(weights) - 1):
        day, next_day = weights.index[i], weights.index[i + 1]
        before = weights.iloc[i]
        target_after = weights.iloc[i + 1]
        daily = rets.iloc[i]
        portfolio_growth = 1.0 + float(before.mul(daily).sum())
        if portfolio_growth <= 0:
            raise ValueError("Portfolio value nonpositive; cannot infer drift")
        drift = before.mul(1.0 + daily) / portfolio_growth
        # This is what the simulation charges at the signal close, whether or
        # not weights otherwise drift.
        modeled_turnover = float((target_after - before).abs().sum())
        needed_turnover = float((target_after - drift).abs().sum())
        unchanged_target = modeled_turnover < 1e-12
        missing_turnover = needed_turnover if unchanged_target else 0.0
        if unchanged_target and missing_turnover > 1e-9:
            details.append({
                "date": day.date().isoformat(),
                "next_date": next_day.date().isoformat(),
                "portfolio_return_gross": float(before.mul(daily).sum()),
                "recorded_target_weight": float(before.sum()),
                "uncharged_turnover_notional": missing_turnover,
                "illustrative_unmodeled_cost": missing_turnover * FEE_SLIPPAGE_PER_TURNOVER,
            })
    return pd.DataFrame(details, columns=[
        "date", "next_date", "portfolio_return_gross", "recorded_target_weight",
        "uncharged_turnover_notional", "illustrative_unmodeled_cost",
    ])


def audit(prices: pd.DataFrame, weights_by_strategy: dict[str, pd.DataFrame]):
    rows, details = [], []
    for name, weights in sorted(weights_by_strategy.items()):
        gaps = compute_hidden_rebalancing(prices, weights)
        count = len(gaps)
        hidden_turnover = float(gaps.uncharged_turnover_notional.sum()) if count else 0.0
        rows.append({
            "strategy": name,
            "sessions_with_unmodeled_rebalance": count,
            "unmodeled_turnover_sum": hidden_turnover,
            "illustrative_sum_of_unmodeled_cost": hidden_turnover * FEE_SLIPPAGE_PER_TURNOVER,
            "review_required": bool(count),
        })
        if count:
            details.append(gaps.assign(strategy=name))
    return pd.DataFrame(rows), (
        pd.concat(details, ignore_index=True) if details else pd.DataFrame(
            columns=["date", "next_date", "portfolio_return_gross",
                     "recorded_target_weight", "uncharged_turnover_notional",
                     "illustrative_unmodeled_cost", "strategy"]
        )
    )


def main():
    df = pd.read_csv(ROOT / "data" / "prices.csv", parse_dates=["date"])
    prices = df.pivot(index="date", columns="symbol", values="close").sort_index().ffill()
    data = pd.read_csv(OUT / "strategy_daily_weights.csv", parse_dates=["date"])
    symbols = list(prices.columns)
    weights_by_strategy = {}
    for name, g in data.groupby("strategy", sort=True):
        if g.date.duplicated().any():
            raise SystemExit(f"Duplicate weights: {name}")
        weights = g.set_index("date").sort_index()
        weights_by_strategy[name] = weights.reindex(index=prices.index, columns=symbols).fillna(0.0)
    summary, detail = audit(prices, weights_by_strategy)
    summary.to_csv(OUT / "execution_accounting_audit.csv", index=False)
    detail.to_csv(OUT / "execution_accounting_implicit_turnover.csv", index=False)
    affected = int(summary.review_required.sum())
    manifest = {
        "status": "REVIEW_REQUIRED" if affected else "PRELIMINARY_PASS",
        "description": "Conservative diagnostic of daily target-weight rebalancing not charged in the exploratory engine",
        "affected_strategies": affected,
        "total_strategies": int(len(summary)),
        "cost_assumption": FEE_SLIPPAGE_PER_TURNOVER,
        "warning": ("Illustrative cost sums are NOT equivalent to corrected compounded "
                    "strategy equity; a self-financing position simulator is necessary."),
        "next_step": "Independently implement and compare a self-financing monthly execution model before investment acceptance."
    }
    (OUT / "execution_accounting_summary.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("EXECUTION_ACCOUNTING_AUDIT " + summary.to_json(orient="records"))
    print("EXECUTION_ACCOUNTING_MANIFEST " + json.dumps(manifest, ensure_ascii=False))
    if affected:
        print("WARNING: current strategy performance has unresolved daily rebalancing/cost-model discrepancies")


if __name__ == "__main__":
    main()
