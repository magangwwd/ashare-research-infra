#!/usr/bin/env python3
"""v1.2 read-only retrospective audit: same-open sale/buy funding is not guaranteed."""
import argparse
import hashlib
import json
from pathlib import Path
from dividend_cash_v11 import load_daily, validate_events, simulate

PRIORITY = ["D_breakout", "G_upper_wick", "B_breakdown", "F_tight_lowvol"]

def same_open_cases(trades, baseline, daily):
    by_entry = {t["entry_date"]: t for t in trades}
    flips = sorted(set(by_entry) & {t["exit_date"] for t in trades})
    curve = baseline["daily_equity"]
    prev = {curve[i]["date"]: curve[i-1] for i in range(1, len(curve))}
    cases = []
    for day in flips:
        if day not in prev:
            raise ValueError("no prior cash: " + day)
        available = prev[day]["cash"]
        gross_lot = 100 * daily[day]["open"] * 1.0005
        required = gross_lot + max(5., gross_lot * .0003)
        cases.append({
            "date": day,
            "prior_close_cash_yuan": available,
            "one_lot_buy_cost_yuan": round(required, 2),
            "one_lot_shortfall_yuan": round(max(0, required - available), 2),
            "can_pre_fund_one_lot": available + 1e-8 >= required,
            "signal": by_entry[day]["signal"]
        })
    return cases

def run(audit, daily, events, enforce_frozen=True):
    trades = audit["selected_trades"]
    if audit["priority"] != PRIORITY or (enforce_frozen and len(trades) != 60):
        raise ValueError("frozen schedule mismatch")
    gross = simulate(trades, daily, events)
    tax20 = simulate(trades, daily, events, tax_rate=.2)
    cases = same_open_cases(trades, gross, daily)
    excluded = {x["date"] for x in cases}
    kept = [t for t in trades if t["entry_date"] not in excluded]
    conservative = simulate(kept, daily, events)
    conservative20 = simulate(kept, daily, events, tax_rate=.2)
    keys = ("ending_equity_yuan", "total_return_pct", "max_drawdown_pct",
            "trades_executed", "dividend_gross_yuan")
    compact = lambda result: {k: result[k] for k in keys}
    return {
        "scope": "retrospective sensitivity; not verified auction fills or prospective holdout",
        "same_open_flip_count": len(cases),
        "cases": cases,
        "excluded_entries": len(trades) - len(kept),
        "baseline_gross": compact(gross),
        "baseline_tax20": compact(tax20),
        "skip_same_open_gross": compact(conservative),
        "skip_same_open_tax20": compact(conservative20),
    }

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--audit", required=True)
    p.add_argument("--daily", nargs="+", required=True)
    p.add_argument("--events", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()
    paths = [args.audit, args.events, *args.daily]
    audit = json.loads(Path(args.audit).read_text(encoding="utf-8"))
    daily = load_daily(args.daily)
    if len(daily) != audit["input_sessions"]:
        raise ValueError("coverage mismatch")
    events = validate_events(daily, json.loads(Path(args.events).read_text(encoding="utf-8")))
    result = run(audit, daily, events)
    result["source_sha256"] = {Path(x).name: hashlib.sha256(Path(x).read_bytes()).hexdigest()
                               for x in paths}
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print({k: v for k, v in result.items() if k not in ("cases", "source_sha256")})

if __name__ == "__main__":
    main()
