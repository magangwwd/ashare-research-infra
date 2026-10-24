"""Comparable ETF benchmarks over exactly the executable walk-forward period.

Buy/hold 510300.SH and 518880.SH individually (one ETF each), paying the
same per-leg modeled costs as the research portfolio. Cash has zero assumed
interest. This is an independent *strategy baseline*, NOT an independent data
vendor or an untouched prospective test. No automatic trading approval.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from etf_single_rotation.experiments.run_strategy_experiments import ROOT
from etf_single_rotation.experiments.simulate_self_financing import simulate
from etf_single_rotation.experiments.walk_forward import score

PRICE_PATH = ROOT / "data" / "prices.csv"
OUT = ROOT / "results" / "self_financing"
BENCHMARKS = ("510300.SH", "518880.SH")
REFERENCE_COST = 0.001


def compare(closes: pd.DataFrame, opens: pd.DataFrame, first: str, last: str,
            fee_rate: float = REFERENCE_COST, symbols=BENCHMARKS):
    """Return comparable benchmark scores and daily curves, using actual opens."""
    if not closes.index.equals(opens.index) or not closes.columns.equals(opens.columns):
        raise ValueError("Price panels must align")
    if closes.index.has_duplicates or not closes.index.is_monotonic_increasing:
        raise ValueError("Nonchronological or repeated price dates")
    first_date, last_date = pd.Timestamp(first), pd.Timestamp(last)
    index = closes.index
    sample = index[(index >= first_date) & (index <= last_date)]
    prior = index[index < first_date]
    if len(sample) < 2 or prior.empty:
        raise ValueError("Benchmark comparison requires a preceding day and two holdout days")
    previous_date = prior[-1]
    if any(s not in closes.columns for s in symbols):
        raise ValueError("A required ETF benchmark has no source quotes")
    rows, curves = [], []
    test_dates = pd.DatetimeIndex([previous_date]).append(sample)
    for name in ("CASH", *symbols):
        if name == "CASH":
            equity = pd.Series(1.0, index=test_dates)
            executed = pd.DataFrame()
            missing_executions = 0
        else:
            targets = pd.DataFrame(0.0, index=index, columns=closes.columns)
            targets.loc[sample, name] = 1.0
            # Simulate from actual open, carrying shares, including first purchase
            # on first *tradable* holdout session. No future data in decision.
            ledger, trades = simulate(
                closes, opens, targets, f"benchmark_{name}", fee_rate=fee_rate
            )
            full = ledger.set_index("date")
            equity = full.loc[test_dates, "equity"].astype(float)
            executed = trades.loc[
                (pd.to_datetime(trades.date) >= sample[0]) &
                (pd.to_datetime(trades.date) <= sample[-1])
            ] if not trades.empty else trades
            missing_executions = int(
                (full.loc[sample, "blocked_reason"] != "").sum()
            )
        if len(equity) != len(test_dates) or equity.isna().any():
            raise ValueError("Benchmark equity timeline is incomplete")
        metric = score(equity)
        rows.append({
            "benchmark": "cash_zero_interest" if name == "CASH" else f"etf_buy_hold_{name}",
            **metric,
            "fee_slippage_per_executed_leg": fee_rate,
            "executed_trade_legs": int(len(executed)),
            "blocked_sessions": missing_executions,
            "status": "RETROSPECTIVE_SAME_SOURCE_PRELIMINARY_NOT_FOR_TRADING",
        })
        normalized = equity / float(equity.loc[previous_date])
        curves.append(pd.DataFrame({
            "date": sample,
            "benchmark": rows[-1]["benchmark"],
            "net_equity": normalized.loc[sample].to_numpy(),
        }))
    return pd.DataFrame(rows), pd.concat(curves, ignore_index=True)


def main():
    if not PRICE_PATH.is_file():
        raise SystemExit("Actual ETF price dataset is missing; no synthetic benchmark")
    wf_path = OUT / "executable_walk_forward_summary.json"
    if not wf_path.is_file():
        raise SystemExit("Executed fold summary is missing; require identical dates")
    wf = json.loads(wf_path.read_text("utf-8"))
    folds = wf.get("folds") or []
    if not folds:
        raise SystemExit("Walk-forward period is not defined")
    first, last = folds[0]["test_start"], folds[-1]["test_end"]
    raw = pd.read_csv(PRICE_PATH, parse_dates=["date"])
    if raw.duplicated(["date", "symbol"]).any():
        raise SystemExit("Duplicate source price rows")
    closes = raw.pivot(index="date", columns="symbol", values="close").sort_index()
    opens = raw.pivot(index="date", columns="symbol", values="open").sort_index()
    if closes.empty or opens.empty:
        raise SystemExit("Price panels are empty")
    table, curves = compare(closes, opens, first, last)
    strategy_metrics = wf["stitched_executable_holdout"]
    report = {
        "source_sha256": hashlib.sha256(PRICE_PATH.read_bytes()).hexdigest(),
        "method": "single ETF buy-and-hold at first tradable test open; cash 0% interest; fee/slippage charged on actual purchases",
        "period_first": first,
        "period_last": last,
        "fee_slippage_per_executed_leg": REFERENCE_COST,
        "strategy": {
            "name": "retrospective_executable_walk_forward",
            "cagr": float(strategy_metrics["cagr"]),
            "max_drawdown": float(strategy_metrics["max_drawdown"]),
            "total_return": float(strategy_metrics["total_return"]),
            "sharpe_rf0": float(strategy_metrics["sharpe_rf0"]),
        },
        "benchmarks": table.to_dict("records"),
        "status": "BENCHMARK_EXPLORATORY_NOT_FOR_TRADING",
        "limitations": [
            "All price series use the same Tushare fund_adj-adjusted data source; dividend total-return semantics remain unverified.",
            "Historical selected candidates were explored using full past data, hence this is NOT untouched forward proof.",
            "Gold and equity ETF buy/hold baselines do not match strategy exposure or drawdown limits; compare risk metrics alongside return.",
            "Commission, fund liquidity, spread, tax and corporate actions are only approximated by modeled per-leg fee/slippage.",
            "Cash assumes 0% interest; results are not a recommendation or a future return guarantee.",
        ],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    table.to_csv(OUT / "benchmark_self_financing_comparison.csv", index=False)
    curves.to_csv(OUT / "benchmark_self_financing_equity.csv", index=False)
    (OUT / "benchmark_self_financing_manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    print("ETF_SELF_FINANCING_BENCHMARK " + json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
