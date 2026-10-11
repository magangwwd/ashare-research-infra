"""Risk-first scenario comparison: paper only, not a new winning strategy.

Frozen historical entry signal definitions are retained. Five strictly
predeclared conservative execution/risk scenarios are replayed from identical
chronologically scanned main-board 2024-2026 data. Ashare_Quant is audited,
NOT merged into historic signals when incomplete.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import pandas as pd

import research_mainboard_daily_pit_v0 as base

EXPERIMENTS = {
    "BASELINE_UNCHANGED": {},
    "ATR_STOP_LE_5PCT": {"max_stop_pct": 0.05},
    "ATR5_AND_MAX_UP_GAP1": {"max_stop_pct": 0.05, "max_gap_up": 0.01},
    "ATR5_GAP1_RISK025_WEIGHT10": {
        "max_stop_pct": 0.05, "max_gap_up": 0.01,
        "risk_budget": 0.0025, "max_weight": 0.10,
    },
    "ATR4_GAP1_RISK025_WEIGHT10": {
        "max_stop_pct": 0.04, "max_gap_up": 0.01,
        "risk_budget": 0.0025, "max_weight": 0.10,
    },
}
KNOWN_GAPS = (
    "Ashare_Quant is incomplete and not used as a historical PIT risk filter.",
    "ST, true historical exchange price limits, and corporate announcements need point-in-time joins.",
    "2024-2026 results are all inspected in-sample diagnostics; no fresh unseen holdout.",
    "ATR stop is not guaranteed: overnight gaps, limit-down or missing bars can exceed planned risk.",
    "Cash-only has lowest market risk; this study compares trading policies, not guaranteed safety.",
    "Thresholds are exploratory after viewing earlier trades, not optimized on an independent sample.",
)

def asharereadiness(current_file: Path, progress_file: Path) -> dict:
    current = json.loads(current_file.read_text(encoding="utf-8"))
    progress = json.loads(progress_file.read_text(encoding="utf-8"))
    if not current.get("validated"):
        raise RuntimeError("Ashare_Quant snapshot is not verified")
    names = ["daily", "adj_factor", "daily_basic", "stk_limit",
             "suspend_d", "namechange", "margin_detail",
             "index_daily", "income", "balancesheet", "cashflow",
             "fina_indicator", "stk_holdernumber"]
    datasets = {}
    for name in names:
        x = progress.get("datasets", {}).get(name, {})
        clean = x.get("layers", {}).get("clean", {})
        dates = clean.get("date_ranges", {})
        datasets[name] = {
            "status": x.get("status", "UNKNOWN"),
            "clean_rows": clean.get("rows", 0),
            "clean_files": clean.get("files", 0),
            "date_ranges": dates,
            "permission_status": x.get("permission_status"),
        }
    return {
        "source": "Ashare_Quant/versioned/current.json + metadata/history_progress.json",
        "version": current.get("version"),
        "snapshot_as_of": current.get("snapshot_as_of"),
        "published_at": current.get("published_at"),
        "validated": bool(current.get("validated")),
        "critical_history_complete": current.get("critical_history_complete"),
        "full_requested_core_range_complete": current.get("full_requested_core_range_complete"),
        "datasets": datasets,
        "eligible_for_full_2024_2026_pit_replay": False,
        "reason": "Incomplete critical history and auxiliary dataset date coverage",
    }

def nav_periods(nav: pd.DataFrame) -> dict:
    x = nav.set_index("trade_date")
    splits = {
        "2024": ("2024-01-01", "2024-12-31"),
        "2025_2026": ("2025-01-01", "2026-10-09")
    }
    result = {}
    for name, (first, last) in splits.items():
        frame = x.loc[first:last]
        previous = x.loc[:first].iloc[0]["nav"] if name == "2024" else x.loc[x.index < first].iloc[-1]["nav"]
        # 2024 begins at initial cash before any position.
        if name == "2024":
            previous = base.INITIAL_CASH
        pnl = float(frame.iloc[-1]["nav"] / previous - 1)
        max_dd = float((frame["nav"] / frame["nav"].cummax() - 1).min())
        result[name] = {"return": round(pnl, 5), "max_dd_internal": round(max_dd, 5)}
    return result

def evaluate_scenario(sessions, prices, signals, params: dict, folder: Path, title: str) -> dict:
    trades, nav, execution, pending = base.run_portfolio(sessions, prices, signals, **params)
    if nav.empty:
        raise ValueError("Empty NAV series")
    folder.mkdir(parents=True, exist_ok=True)
    trades.to_csv(folder / "trades.csv", index=False, encoding="utf-8-sig")
    nav.to_csv(folder / "daily_nav.csv", index=False, encoding="utf-8-sig")
    result = base.stats(trades, nav)
    returns = trades["net_return"] if len(trades) else pd.Series(dtype=float)
    tail = returns.nsmallest(max(1, (len(returns) + 9) // 10)) if len(returns) else returns
    result.update({
        "scenario": title,
        "max_stop_pct": params.get("max_stop_pct", 0.10),
        "max_gap_up": params.get("max_gap_up", 0.03),
        "max_per_trade_equity_risk": params.get("risk_budget", 0.005),
        "max_weight": params.get("max_weight", 0.15),
        "open_positions": pending,
        "trade_loss_below_minus_5pct_count": int(returns.le(-.05).sum()),
        "trade_loss_below_minus_5pct_rate": round(float(returns.le(-.05).mean()),4)
              if len(returns) else None,
        "trade_worst_decile_mean": round(float(tail.mean()), 5) if len(tail) else None,
        "periods": nav_periods(nav),
        "execution_rejections": execution
    })
    return result

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--market-root", required=True, type=Path)
    p.add_argument("--ashare-current", required=True, type=Path)
    p.add_argument("--ashare-history-progress", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    readiness = asharereadiness(args.ashare_current, args.ashare_history_progress)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "ashare_quant_source_audit.json").write_text(
        json.dumps(readiness, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    panel = base.load_panel(args.market_root)
    sessions, prices, signals, latest, counters = base.plan_market(panel)
    del panel
    scenarios = {}
    for title, params in EXPERIMENTS.items():
        print("SIMULATING", title, flush=True)
        output = args.output / "scenarios" / title
        scenarios[title] = evaluate_scenario(sessions, prices, signals, params, output, title)
        print("RESULT", title, json.dumps(
            {k: scenarios[title][k] for k in
             ("closed_trades","net_total_return","daily_nav_max_drawdown","trade_profit_factor")},
            ensure_ascii=False
        ), flush=True)
    summary = {
        "status": "EXPLORATORY_RISK_GATES_NOT_BUY_AUTHORIZATION",
        "as_of_date": "2026-10-09",
        "universe": "Historically available MAIN_BOARD records in down_data",
        "price_history": "2023 warmup, 2024..2026-10-09 retrospective",
        "as_hare_auxiliary": {
            "as_of": readiness["snapshot_as_of"],
            "critical_history_complete": readiness["critical_history_complete"],
            "replay_usage": "DATA_READINESS_AUDIT_ONLY",
        },
        "scenarios": scenarios,
        "warnings": KNOWN_GAPS,
        "decision": "HOLD_NO_ENTRY_AUTHORIZATION; forward evaluation required",
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# A股主板风险优先入场阈值：探索性回测",
        "",
        "所有阈值只改变执行和仓位风控，不用2026-10-09的A级股票倒灌到历史。",
        "原始冻结V1不变。本项目是历史诊断，**不能自动发买入建议**。",
        "",
        "## 历史风险指标比较（净值为模拟账户，非真实策略授权）",
        "",
        "| 预设方案 | 平仓笔数 | 净收益 | 最大净值回撤 | 平均仓位暴露 | 胜率 | 利润因子 | 单笔亏损≥5%比例 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|"
    ]
    for key,s in scenarios.items():
        def pct(q):
            return "-" if q is None else f"{100*q:+.2f}%"
        lines.append(
            f"| {key} | {s['closed_trades']} | "
            f"{pct(s['net_total_return'])} | {pct(s['daily_nav_max_drawdown'])} | "
            f"{pct(s['average_exposure'])} | "
            f"{pct(s.get('trade_win_rate'))} | {s.get('trade_profit_factor','-')} | "
            f"{pct(s['trade_loss_below_minus_5pct_rate'])} |"
        )
    lines += [
        "",
        "## Ashare_Quant数据源可用性",
        "",
        f"- 当前归档版本：{readiness['version']}。",
        f"- 标记截止：{readiness['snapshot_as_of']}，并非10月9日最新行情。",
        f"- 历史核心完整性：{readiness['critical_history_complete']}。",
        "- 不覆盖整个2024—2026日级历史，因此本轮仅做可用性审计，不进行不可靠的合并回测。",
        "- 后续需在公告生效日做严格point-in-time连接并核对每只证券的涨跌停、停牌与风险警示。",
        "",
        "## 结论边界",
        ""
    ] + ["- "+w for w in KNOWN_GAPS]
    lines += [
        "",
        "不能仅因低仓位让回撤变小就认定策略优于空仓，需要同时报告交易次数、资金暴露、收益和尾部风险。",
        "一个阈值仅当历史开发和诊断两个阶段表现一致、风险收益合理且未来前向验证通过，才可以考虑升级为研究候选。",
    ]
    (args.output / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("RISK_FIRST_SUMMARY=" + json.dumps({
        k: {field: r[field] for field in
            ("closed_trades","net_total_return","daily_nav_max_drawdown",
             "average_exposure","trade_profit_factor","trade_loss_below_minus_5pct_rate")}
        for k,r in scenarios.items()}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
