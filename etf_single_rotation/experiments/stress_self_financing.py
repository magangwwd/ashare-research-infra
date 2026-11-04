"""Fee/slippage stress test of the independent ETF self-financing model.

The candidate pool is frozen. The same past-only training selection made under
the 10bp per-side reference fee is replayed under 30bp and 50bp assumptions.
No additional lookahead-driven parameter search or altered strategy signals.
"""
from __future__ import annotations
import json
import tempfile
from pathlib import Path

import pandas as pd

from etf_single_rotation.experiments.run_strategy_experiments import (
    ROOT, build_monthly_targets,
)
from etf_single_rotation.experiments.simulate_self_financing import (
    OUT, executable_walk_forward, performance, simulate,
)
from etf_single_rotation.experiments.walk_forward import CANDIDATES

FEE_CASES = (0.001, 0.003, 0.005)  # each direction, includes fee and slippage


def main():
    real_source = ROOT / "data" / "prices.csv"
    if not real_source.exists():
        raise SystemExit("Real Tushare ETF prices missing: never fabricate history.")
    raw = pd.read_csv(real_source, parse_dates=["date"])
    if raw.duplicated(["date", "symbol"]).any():
        raise SystemExit("Duplicate ETF/date records in cost study")
    closes = raw.pivot(index="date", columns="symbol", values="close").sort_index()
    opens = raw.pivot(index="date", columns="symbol", values="open").sort_index()
    if closes.empty or not closes.index.equals(opens.index):
        raise SystemExit("No aligned ETF open/close prices for fee stress")
    signals_by_strategy = {
        strategy: build_monthly_targets(closes.ffill(), strategy)[0]
        for strategy in CANDIDATES
    }
    # Freeze the reference selection policy before applying higher fees.
    reference_histories = {}
    rows = []
    for strategy in CANDIDATES:
        ledger, _ = simulate(
            closes, opens, signals_by_strategy[strategy], strategy, fee_rate=FEE_CASES[0]
        )
        reference_histories[strategy] = pd.Series(
            ledger.equity.to_numpy(), index=pd.DatetimeIndex(ledger.date)
        )
    wf_rows = []
    for fee in FEE_CASES:
        for strategy in CANDIDATES:
            ledger, trades = simulate(
                closes, opens, signals_by_strategy[strategy], strategy, fee_rate=fee
            )
            metric = performance(ledger, trades, strategy)
            rows.append({
                "fee_slippage_per_side": fee,
                **metric,
                "passes_historical_drawdown_numeric_gate": bool(
                    metric["max_drawdown"] >= -0.25 and metric["cagr"] > 0
                ),
                "status": "STRESS_EXPLORATORY_NOT_FOR_TRADING",
            })
            print("SELF_FINANCING_FEE_STRESS " + json.dumps({
                "strategy": strategy, "fee_slippage_per_side": fee,
                "cagr": metric["cagr"], "max_drawdown": metric["max_drawdown"],
                "sharpe_rf0": metric["sharpe_rf0"]
            }, ensure_ascii=False))
        with tempfile.TemporaryDirectory() as tmp:
            wf = executable_walk_forward(
                closes, opens, signals_by_strategy, reference_histories,
                candidates=CANDIDATES, fee_rate=fee, output_dir=Path(tmp)
            )
        wf_rows.append({
            "fee_slippage_per_side": fee,
            **wf["stitched_executable_holdout"],
            "executed_trade_legs": wf["executed_trade_legs"],
            "charged_fee_slippage": wf["charged_fee_slippage"],
            "selected_strategies_by_fold": ";".join(
                f["selected_strategy"] for f in wf["folds"]
            ),
            "passes_historical_drawdown_numeric_gate": bool(
                wf["stitched_executable_holdout"]["max_drawdown"] >= -0.25
                and wf["stitched_executable_holdout"]["cagr"] > 0
            ),
            "status": "STRESS_EXPLORATORY_NOT_FOR_TRADING",
        })
        print("EXECUTABLE_WALK_FORWARD_FEE_STRESS " + json.dumps(
            wf_rows[-1], ensure_ascii=False
        ))
    OUT.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(OUT / "cost_stress_strategy_comparison.csv", index=False)
    pd.DataFrame(wf_rows).to_csv(OUT / "cost_stress_executable_walk_forward.csv", index=False)
    manifest = {
        "status": "STRESS_EXPLORATORY_NOT_FOR_TRADING",
        "fee_slippage_per_side_tested": list(FEE_CASES),
        "candidate_strategies": list(CANDIDATES),
        "fold_strategy_selection": "Frozen at 0.10% reference fee; train-past-only choice applied at each fold.",
        "data_last_date": str(closes.index.max().date()),
        "drawdown_numeric_target": 0.25,
        "caveats": [
            "0.10%/0.30%/0.50% per leg are stylized all-in cost assumptions, not broker quotes.",
            "Historical candidate parameters were previously explored using full history.",
            "Historical missing ETF rows and ETF dividend total-return semantics remain unresolved.",
            "No benchmark, forward-live, liquidity capacity or real order fill validation."
        ],
    }
    (OUT / "cost_stress_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print("COST_STRESS_MANIFEST " + json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
