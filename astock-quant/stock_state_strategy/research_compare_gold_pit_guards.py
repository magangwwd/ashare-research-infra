"""Compare 2024-2026 paper portfolios with and without factual PIT execution constraints.

This DOES NOT enable live trading, nor does it claim unseen backtest samples.
"""
from __future__ import annotations
import argparse, json
from pathlib import Path
from gold_pit_risk_guards import GoldPITRisk
import research_mainboard_daily_pit_v0 as base
from research_risk_first_gate_scenarios import evaluate_scenario

R0={"max_stop_pct":0.04,"max_gap_up":0.01,
    "risk_budget":0.0025,"max_weight":0.10}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--market-root",type=Path,required=True)
    p.add_argument("--risk-root",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    args=p.parse_args()
    guard=GoldPITRisk(args.risk_root)
    panel=base.load_panel(args.market_root)
    observed_dates={d.normalize() for d in panel.loc[panel.trade_date.ge(base.START),"trade_date"].unique()}
    limits_days={day for _,day in guard.limits}
    missing_dates=sorted(d for d in observed_dates if d not in limits_days)
    if missing_dates:
        raise RuntimeError(f"Missing entire days of PIT limits: {[str(d.date()) for d in missing_dates[:7]]}")
    print("GOLD_RISK_CONTEXT="+json.dumps(guard.summary()),flush=True)
    sessions, prices, signals, latest, counts=base.plan_market(panel)
    del panel
    args.out.mkdir(parents=True,exist_ok=True)
    base_run=evaluate_scenario(sessions,prices,signals,R0,args.out/"without_guards","R0_PREVIOUS")
    guarded=evaluate_scenario(sessions,prices,signals,
                              {**R0,"risk_context":guard},
                              args.out/"with_guards","R0_PIT_GUARDS")
    summary={
      "status":"HISTORICAL_PAPER_DIAGNOSTIC_NO_ENTRY_AUTHORIZATION",
      "research_as_of":"2026-10-09",
      "context":guard.summary(),
      "historical_source":"down_data Gold PIT daily_limits, suspension_events, name_history",
      "replay":"2023 indicator warmup and 2024-2026-10-09 historical session replay",
      "base_r0":base_run,
      "with_pit_guards":guarded,
      "missing_limit_dates":[],
      "cautions":[
        "Not a forward out-of-sample result; R0 thresholds were selected after earlier retrospectives.",
        "Stock-name ST PIT check requires dated, announced and effective historical intervals; absent/ambiguous names cannot enter.",
        "Historical limit prices are raw price; adjusted OHLC only for technical indicators.",
        "Lock limit-down at open defers exits when one-word; other intraday queue dynamics still not modeled.",
        "Dataset completeness per symbol is NOT proven merely by complete dates.",
        "Corporate announcements, negative news and all limit queue details remain not modeled.",
        "PIT risk guard cannot turn a structurally losing 2025-2026 segment into an authorized buy.",
        "Forward observation starts no earlier than 2026-10-12, on data genuinely seen that day.",
      ]
    }
    (args.out/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    print("PIT_RISK_GUARD_RESULTS="+json.dumps({
      k: {field:v[field] for field in ("closed_trades","net_total_return","daily_nav_max_drawdown","average_exposure","execution_rejections","periods")}
      for k,v in (("base_r0",base_run),("with_pit_guards",guarded))
    },ensure_ascii=False),flush=True)
    lines=["# R0 风险策略：历史实际涨跌停和ST过滤前后",
            "","2024—2026所有数据均已被研究过，**不是未来样本验证**。数据缺失时拒绝模拟买入。",
            "","| 状态 | 平仓笔数 | 收益 | 最大回撤 | 2024收益 | 2025—2026收益 |",
            "|---|---:|---:|---:|---:|---:|"]
    for name,v in (("历史R0",base_run),("R0 + PIT过滤",guarded)):
       lines.append(f"| {name} | {v['closed_trades']} | {v['net_total_return']:.2%} | {v['daily_nav_max_drawdown']:.2%} | {v['periods']['2024']['return']:.2%} | {v['periods']['2025_2026']['return']:.2%} |")
    lines += ["","## 入场/退出风险拦截明细",""]
    lines += [f"- {k}: {v}" for k,v in guarded["execution_rejections"].items() if k.startswith("risk_")]
    lines += ["","## 重大限制",""]+["- "+x for x in summary["cautions"]]
    (args.out/"report.md").write_text("\n".join(lines),encoding="utf-8")
if __name__=="__main__":main()
