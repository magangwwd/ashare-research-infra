"""R0 independent 100-share/cent-tick/fee/slippage historical stress.

This is retrospective development evidence, not a fresh holdout or a signal.
The existing R0 historical replay, frozen V1 and live connectors are unchanged.
Adjustment factors are a total-return accounting PROXY; missing corporate
cash dividend/share records preclude calling it real brokerage P&L.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

import pandas as pd

import research_mainboard_daily_pit_v0 as base
from gold_pit_risk_guards import GoldPITRisk
from research_risk_first_gate_scenarios import evaluate_scenario

R0 = {"max_stop_pct": .04, "max_gap_up": .01,
      "risk_budget": .0025, "max_weight": .10}
STRESS_CASES = {
    "R0_PIT_LEGACY_REFERENCE": {**R0},
    "R0_PIT_LOT100_TICK_FEE_10BP": {
        **R0, "execution_model": "lot100_minfee",
        "execution_slippage": .001,
    },
    "R0_PIT_LOT100_TICK_FEE_20BP": {
        **R0, "execution_model": "lot100_minfee",
        "execution_slippage": .002,
    },
    "R0_PIT_LOT100_TICK_FEE_30BP": {
        **R0, "execution_model": "lot100_minfee",
        "execution_slippage": .003,
    },
}


def run(market_root: Path, risk_root: Path, out: Path):
    guard = GoldPITRisk(risk_root)
    panel = base.load_panel(market_root)
    observed = {pd.Timestamp(d).normalize()
                for d in panel.loc[panel.trade_date.ge(base.START), "trade_date"].unique()}
    covered = {d for _, d in guard.limits}
    absent = sorted(observed - covered)
    if absent:
        raise RuntimeError("Missing entire PIT limit trading dates: " +
                           str([str(d.date()) for d in absent[:8]]))
    sessions, prices, signals, future, scanned = base.plan_market(panel)
    del panel
    out.mkdir(parents=True, exist_ok=True)
    result = {}
    for label, params in STRESS_CASES.items():
        print("EXECUTION_STRESS_START " + label, flush=True)
        result[label] = evaluate_scenario(
            sessions, prices, signals, {**params, "risk_context": guard},
            out / label, label)
        print("EXECUTION_STRESS_DONE " + label + " " + json.dumps({
            k: result[label][k] for k in (
                "closed_trades", "net_total_return", "daily_nav_max_drawdown",
                "trade_worst_decile_mean", "trade_loss_below_minus_5pct_rate"
            )}), flush=True)
    ref = result["R0_PIT_LEGACY_REFERENCE"]
    if (ref["closed_trades"] != 294 or
            abs(ref["net_total_return"] - .04921) > .00002 or
            abs(ref["daily_nav_max_drawdown"] + .07190) > .00002):
        raise RuntimeError("R0 PIT reproducibility guard failed; do not report new results.")
    summary = {
        "status": "PAPER_ONLY_RETROSPECTIVE_EXECUTION_STRESS_NO_BUY_AUTHORIZATION",
        "data_cutoff": "2026-10-09",
        "scope": "A-share main board, historically retained Gold",
        "research_universe": len(prices),
        "reproduced_previous_R0_PIT": True,
        "scenarios": result,
        "source": guard.summary(),
        "limitations": [
            "2024-2026 are previously inspected: NOT out-of-sample and NOT a prediction.",
            "100-share round lot only at entry; 0.01 CNY tick and 5 CNY minimum fee are conservative study assumptions.",
            "Broker-specific commissions, stamp duty / exchange fees are represented by existing aggregate 0.13%/0.18% rates, not re-charged separately.",
            "With the existing 10,000 CNY minimum notional, fee minimum usually cannot bind.",
            "Adjustment factor tracks total-return-equivalent marked value, not actual dividend cash, bonus shares or share ledger.",
            "A split/bonus event can create odd share lots; physical settlement and exact corporate event reconciling are absent.",
            "No order book queue, actual intraday fills, full auction microstructure or negative corporate-news screening.",
            "Date-level exchange limits do not certify per-symbol completeness.",
            "Historical 2025-2026 losses remain critical and NO rules authorize buying.",
        ],
        "decision": "PAPER_ONLY_NO_ENTRY_AUTHORIZATION",
    }
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    rows = [
        "# R0 主板执行约束压力回测（历史诊断，不是买入信号）",
        "",
        "冻结参数：2ATR14/成交价≤4%、开盘追高≤1%、单笔账户风险≤0.25%、个股权重≤10%。",
        "比较在同一历史数据下新增100股整手、0.01元价位、模拟最低佣金以及滑点压力后的结果。",
        "",
        "| 模式 | 平仓 | 累计收益 | 最大回撤 | 最差10%交易平均收益 | 2025—2026收益 | ≥5%单笔亏损占比 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, s in result.items():
        rows.append(
            f"| {name} | {s['closed_trades']} | {s['net_total_return']:.2%}"
            f" | {s['daily_nav_max_drawdown']:.2%}"
            f" | {s['trade_worst_decile_mean']:.2%}"
            f" | {s['periods']['2025_2026']['return']:.2%}"
            f" | {s['trade_loss_below_minus_5pct_rate']:.2%} |"
        )
    rows += ["", "## 模型仍有的局限", ""]
    rows += ["- " + x for x in summary["limitations"]]
    (out / "report.md").write_text("\n".join(rows), encoding="utf-8")
    return summary


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--market-root", type=Path, required=True)
    p.add_argument("--risk-root", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    run(a.market_root, a.risk_root, a.out)


if __name__ == "__main__":
    main()
