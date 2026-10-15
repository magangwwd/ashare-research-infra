"""Fixed, inspectable entry-style and breadth diagnostics on seen historical data.

Research-only. 2024..2026-10-09 were inspected in earlier iterations: NOT
an independent holdout, no profitable-buy claim. R0 thresholds are unchanged.
The conservative model checks final tick-rounded paid price against +1% gap
and exchange limit; corporate-action/queue/microstructure caveats remain.
"""
from __future__ import annotations
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

import research_mainboard_daily_pit_v0 as base
from gold_pit_risk_guards import GoldPITRisk
from research_risk_first_gate_scenarios import evaluate_scenario

R0 = {"max_stop_pct": .04, "max_gap_up": .01,
      "risk_budget": .0025, "max_weight": .10}
STRICT20 = {**R0, "execution_model": "lot100_minfee",
            "execution_slippage": .002}
POLICIES = (
    "STRICT20_ALL",
    "STRICT20_BREAKOUT_ONLY",
    "STRICT20_PULLBACK_ONLY",
    "STRICT20_BREADTH_MAJORITY",
    "STRICT20_BREAKOUT_AND_BREADTH",
    "STRICT20_PULLBACK_AND_BREADTH",
)
BREADTH_MIN_VALID_SYMBOLS = 500


def point_in_time_breadth(price_by_symbol):
    """At each close: fraction of stocks with >=20 observed daily bars above MA20.

    Today's and past closes only. Does NOT use end-date classifications; stale
    retained universe and incomplete historical listings are not resolved.
    """
    num, den = Counter(), Counter()
    for bars in price_by_symbol.values():
        ma20 = bars["p_close"].rolling(20, min_periods=20).mean()
        valid = ma20.notna() & bars["p_close"].notna()
        active = bars.loc[valid, "p_close"]
        moving = ma20.loc[valid]
        for d, flag in zip(active.index, active.gt(moving), strict=True):
            day = pd.Timestamp(d).normalize()
            den[day] += 1
            if bool(flag):
                num[day] += 1
    return {
        d: {"valid": n, "above": num[d], "fraction": num[d] / n}
        for d, n in sorted(den.items())
        if n >= BREADTH_MIN_VALID_SYMBOLS
    }


def policy_accepts(policy, candidate, breadth):
    pattern = candidate["pattern"]
    if policy in ("STRICT20_BREAKOUT_ONLY", "STRICT20_BREAKOUT_AND_BREADTH") and pattern != "BREAKOUT":
        return False
    if policy in ("STRICT20_PULLBACK_ONLY", "STRICT20_PULLBACK_AND_BREADTH") and pattern != "PULLBACK":
        return False
    if policy.endswith("BREADTH") or policy == "STRICT20_BREADTH_MAJORITY":
        day = pd.Timestamp(candidate["signal_date"]).normalize()
        point = breadth.get(day)
        if not point or point["fraction"] < .5:
            return False
    return True


def make_filtered_schedules(scheduled, breadth):
    output = {p: defaultdict(list) for p in POLICIES}
    for date, candidates in scheduled.items():
        for c in candidates:
            for policy in POLICIES:
                if policy_accepts(policy, c, breadth):
                    output[policy][date].append(c)
    return output


def run(market_root: Path, risk_root: Path, out: Path):
    guard = GoldPITRisk(risk_root)
    panel = base.load_panel(market_root)
    observed = {pd.Timestamp(x).normalize()
                for x in panel.loc[panel.trade_date.ge(base.START), "trade_date"].unique()}
    covered = {d for _, d in guard.limits}
    missing = sorted(observed - covered)
    if missing:
        raise RuntimeError("Missing entire days of PIT limits: "+str(missing[:8]))
    sessions, by_symbol, scheduled, future, counts = base.plan_market(panel)
    del panel
    breadth = point_in_time_breadth(by_symbol)
    selected = make_filtered_schedules(scheduled, breadth)
    out.mkdir(parents=True, exist_ok=True)
    outcomes = {}
    for label in ("R0_PREVIOUS_PIT_REFERENCE", *POLICIES):
        params = {**R0, "risk_context": guard} if label == "R0_PREVIOUS_PIT_REFERENCE" else {
            **STRICT20, "risk_context": guard,
        }
        candidates = scheduled if label == "R0_PREVIOUS_PIT_REFERENCE" else selected[label]
        print("ENTRY_QUALITY_START " + label, flush=True)
        summary = evaluate_scenario(sessions, by_symbol, candidates, params, out/label, label)
        outcomes[label] = {
            **summary,
            "scheduled_candidates": int(sum(len(v) for v in candidates.values())),
        }
        print("ENTRY_QUALITY_DONE " + label + " " + json.dumps({
            k: outcomes[label][k] for k in (
                "closed_trades", "net_total_return", "daily_nav_max_drawdown",
                "trade_worst_decile_mean", "trade_loss_below_minus_5pct_rate"
            )}), flush=True)
    ref = outcomes["R0_PREVIOUS_PIT_REFERENCE"]
    if ref["closed_trades"] != 294 or abs(ref["net_total_return"]-.04921)>.00002:
        raise RuntimeError("Previously published R0 reference did not reproduce")

    report = {
        "status": "EXPLORATORY_ENTRY_QUALITY_DIAGNOSTIC_NO_BUY_AUTHORIZATION",
        "as_of_date": "2026-10-09",
        "universe_count": len(by_symbol),
        "breadth_days": len(breadth),
        "breadth_rule": "Observed close day fraction of >=20-bar, historically retained symbols > MA20; threshold >=0.50",
        "breadth_min_valid": BREADTH_MIN_VALID_SYMBOLS,
        "fixed_execution": "100-share whole lots, 0.01 ticks, 20bp per side, min fee scenario; final paid <=1% prior adjusted close, under upper limit",
        "hold_cash_reference_return": 0,
        "outcomes": outcomes,
        "cautions": [
            "This is exploration on already inspected 2024-2026 data; no independent holdout.",
            "No rule can be called a reliable buy point without genuinely unseen, timestamped forward results.",
            "Breadth uses historically retained securities: delisted/missing names may cause survivorship bias.",
            "Daily Gold OHLCV cannot verify auction or order queue fill probabilities.",
            "Adjusted factor is total-return proxy, NOT real corporate action bookkeeping.",
            "No PIT audited negative announcements; symbol-by-date risk coverage incomplete beyond day counts.",
            "Both gross selection and account-level costs matter; reject policies with 2025-2026 losses or weak stress robustness.",
            "Do not optimize the 50% breadth cutoff, pattern definition or stop rules on these seen years.",
            "Paper only, no brokers, no real orders.",
        ],
        "decision": "HOLD_NO_ENTRY_AUTHORIZATION",
    }
    (out/"summary.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    lines = [
        "# R0 买点类型与市场广度：固定规则历史诊断",
        "",
        "不改变原ATR≤4%、追高≤1%、单笔账户风险≤0.25%。所有数据已经被研究过，仅供探索，不是未来验证。",
        "",
        "| 策略 | 平仓数 | 累计收益 | 最大回撤 | 最差10%交易 | 2025-2026收益 | 利润因子 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, x in outcomes.items():
        lines.append(
            f"| {name} | {x['closed_trades']} | {x['net_total_return']:.2%}"
            f" | {x['daily_nav_max_drawdown']:.2%} | "
            f"{x['trade_worst_decile_mean']:.2%} | {x['periods']['2025_2026']['return']:.2%}"
            f" | {x.get('trade_profit_factor')} |"
        )
    lines.extend(["", "## 遗留风险", ""] + ["- " + x for x in report["cautions"]])
    (out/"report.md").write_text("\n".join(lines), encoding="utf-8")
    return report


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--market-root", type=Path, required=True)
    p.add_argument("--risk-root", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    run(a.market_root, a.risk_root, a.out)


if __name__ == "__main__":
    main()
