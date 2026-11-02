"""V5 causal-ranked, single-position PAPER research; no broker or new data vendor.

Tests the hypothesis "avoid chasing the most extended breakout" without using
future exit outcomes. Frozen V1 simulator and V2 LOOSE filter are reused.
The entire 2024-2026 history has been studied; this is NOT untouched OOS.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from hashlib import sha256
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from short_swing_3to5_v1 import Policy, build_signals, load_gold, simulate
from short_swing_3to5_v2_research import SPECS, filter_signals, signal_day_features

VERSION = "MAINBOARD_3TO5_CAUSAL_RANKING_PAPER_V5"
VARIANTS = ("V2_LOOSE_UNCHANGED", "LESS_EXTENDED_BREAKOUT_FIRST", "LESS_RECENT_RUNUP_FIRST")
LOOSE = next(s for s in SPECS if s.name == "LOOSE")


def rank_signals(eligible: dict, features: pd.DataFrame, mode: str) -> dict:
    """Rank the *same* pre-entry survivors using only completed signal-day bars.

    Do not discard individual historically losing trades or use their future
    exit labels. Stable original score ranking breaks exact feature ties.
    """
    if mode not in VARIANTS:
        raise ValueError("Unrecognised predeclared candidate rank")
    if mode == "V2_LOOSE_UNCHANGED":
        return {day: list(choices) for day, choices in eligible.items()}
    factor = "breakout" if mode == "LESS_EXTENDED_BREAKOUT_FIRST" else "prior5_gain"
    result = {}
    for day, choices in eligible.items():
        values = []
        for index, (symbol, score) in enumerate(choices):
            key = (pd.Timestamp(day), symbol)
            if key not in features.index:
                raise ValueError("Missing selected signal-day features")
            feature = features.loc[key]
            if isinstance(feature, pd.DataFrame):
                raise ValueError("Duplicate feature key")
            value = float(feature[factor])
            if not math.isfinite(value):
                raise ValueError("Nonfinite pre-entry rank")
            values.append((value, index, symbol, score))
        # Secondary sort index exactly reproduces original V1 ordering on ties.
        values.sort(key=lambda t: (t[0], t[1]))
        result[day] = [(symbol, score) for _, _, symbol, score in values]
    return result


def yearly_metrics(sim: dict) -> dict:
    nav = sim["daily_nav"]
    result = {}
    for year in sorted({row["date"][:4] for row in nav}):
        sub = [row for row in nav if row["date"].startswith(year)]
        prev = [row for row in nav if row["date"] < year+"-01-01"]
        start_equity = prev[-1]["equity_cny"] if prev else sim["policy"]["initial_cash_cny"]
        prior_peak = start_equity
        dd = 0.
        for row in sub:
            prior_peak = max(prior_peak, float(row["equity_cny"]))
            dd = min(dd, float(row["equity_cny"]) / prior_peak - 1)
        closed = [x for x in sim["trades"] if x["entry_date"].startswith(year)]
        result[year] = {
            "closed_trades_entry_year": len(closed),
            "realized_pnl_entry_year_cny": round(sum(x["pnl_cny"] for x in closed), 2),
            "calendar_equity_change": round(float(sub[-1]["equity_cny"]) / start_equity - 1, 8),
            "calendar_max_drawdown": round(dd, 8),
        }
    return result


def run_rank_comparison(panel: pd.DataFrame, risk, policy: Policy, start: str) -> dict:
    original = build_signals(panel, policy, risk)
    features = signal_day_features(panel)
    survivors, counts = filter_signals(original, features, LOOSE)
    # Rank orders may vary; candidate membership, hard filters and fills do not.
    runs = []
    for name in VARIANTS:
        ranking = rank_signals(survivors, features, name)
        sim = simulate(panel, risk, policy, signals=ranking, start=start)
        if sim["mode"] != "PAPER_ONLY_NO_BROKER_NO_LIVE_AUTHORIZATION":
            raise ValueError("Unexpected execution engine mode")
        if any(x["exit_date"] < x["entry_date"] for x in sim["trades"]):
            raise ValueError("Trade chronology invalid")
        if any(x.get("side") == "BUY_ALL" and x.get("notional_cny",0) > policy.max_order_cny + 1
               for x in sim["orders"]):
            raise ValueError("Oversized order")
        runs.append({
            "name": name,
            "summary": sim["summary"],
            "by_year": yearly_metrics(sim),
            "exit_reasons": dict(sorted(Counter(x["exit_reason"] for x in sim["trades"]).items())),
            "assumed_intraday_fills": sum(x["exit_reason"] in
                ("STOP_INTRADAY_ASSUMED", "TARGET_INTRADAY_ASSUMED") for x in sim["trades"]),
            "orders": sim["orders"], "trades": sim["trades"], "daily_nav": sim["daily_nav"],
            "unclosed_position": sim["open_position"] is not None,
        })
    if runs[0]["summary"]["closed_trades"] == 0:
        raise ValueError("Empty V2 LOOSE baseline is not a historical comparison")
    if len({tuple(s for s,_ in choices) for rank in (rank_signals(survivors,features,n)
             for n in VARIANTS) for choices in rank.values()}) == 0:
        raise AssertionError("No signal candidates")
    return {
        "version": VERSION, "mode": "RESEARCH_ONLY_NO_LIVE_TRADING",
        "policy": asdict(policy),
        "original_v2_filter": asdict(LOOSE),
        "baseline_filter_statistics": counts,
        "fixed_variant_names": VARIANTS,
        "variants": runs,
        "known_limitations": [
            "All 2024-2026 data previously researched; results are historical hypotheses only, NOT unvisited OOS.",
            "Changing ranking changes future single-position capital availability; do not cherry-pick trade subsets.",
            "Execution engine uses daily OHLC stop/target assumptions, not Tushare 15m fills or order book.",
            "Current Ashare_Quant Tushare 15m historical coverage is incomplete; no claim of validated fills.",
            "Frozen V1 simulator is reused; stop, T+1, costs, company action proxies unchanged.",
            "Never auto-select winning variant, claim future profitability, merge PR or place real orders.",
        ]
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--market-root", required=True, type=Path)
    p.add_argument("--risk-root", required=True, type=Path)
    p.add_argument("--start", default="2024-01-01")
    p.add_argument("--end", required=True)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--expected-gold-manifest-sha256", default=None)
    a = p.parse_args()
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    if pd.Timestamp(a.start) < pd.Timestamp("2024-01-01") or pd.Timestamp(a.end) < pd.Timestamp(a.start):
        p.error("Unexpected backtest period")
    if pd.Timestamp(a.end).date() >= now.date() and (
        pd.Timestamp(a.end).date() > now.date() or now.hour < 16):
        p.error("Backtest cannot include an unfinished market session")
    policy = Policy()
    policy.validate()
    panel, manifest, manifest_sha = load_gold(a.market_root, a.start, a.end)
    if a.expected_gold_manifest_sha256 and manifest_sha != a.expected_gold_manifest_sha256:
        raise ValueError("Historical Gold source changed; baseline must be revalidated")
    from gold_pit_risk_guards import GoldPITRisk
    risk = GoldPITRisk(a.risk_root)
    if not risk.limits or not risk.names:
        raise RuntimeError("Missing PIT risk tables")
    output = run_rank_comparison(panel, risk, policy, a.start)
    output["period"] = {"start": a.start, "end": a.end}
    output["gold_source"] = {"as_of": manifest["as_of_date"], "sha256": manifest_sha, "pit": risk.summary()}
    if a.output.exists():
        raise FileExistsError("Never overwrite original research evidence")
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(output, ensure_ascii=False, indent=2, default=str)+"\n", encoding="utf-8")
    print(json.dumps({"result": "PAPER_BACKTEST_NOT_OOS", "gold": output["gold_source"]["sha256"],
                      "variants": [{"name":v["name"], "summary":v["summary"],
                                    "assumed_intraday_fills":v["assumed_intraday_fills"]}
                                   for v in output["variants"]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
