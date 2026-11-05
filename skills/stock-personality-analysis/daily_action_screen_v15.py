"""Read-only pre_close discontinuity screen; NOT an adj_factor or fill audit."""
import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path


def rows(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def screen(daily_rows, events, trades, target="600031.SH", tolerance=0.015):
    grouped = defaultdict(list)
    for row in daily_rows:
        grouped[row["ts_code"]].append(row)
    if target not in grouped:
        raise ValueError("target missing")
    findings, nulls, duplicates = [], [], []
    for ticker, series in sorted(grouped.items()):
        series.sort(key=lambda r: r["trade_date"])
        seen, prev = set(), None
        for r in series:
            day = r["trade_date"]
            if day in seen:
                duplicates.append({"ts_code": ticker, "date": day})
            seen.add(day)
            p = r.get("pre_close", "")
            if not p or p.lower() in ("nan", "none", "null"):
                nulls.append({"ts_code": ticker, "date": day})
            elif prev is not None:
                delta = round(float(p) - float(prev["close"]), 6)
                if abs(delta) > tolerance:
                    findings.append({"ts_code": ticker, "date": day,
                                     "prior_close": float(prev["close"]),
                                     "pre_close": float(p), "delta_yuan": delta})
            prev = r
    event_map = {e["ex_date"]: e for e in events}
    target_findings = [f for f in findings if f["ts_code"] == target]
    checked = []
    for f in target_findings:
        event = event_map.get(f["date"])
        expected = -float(event["cash_per_share_yuan"]) if event else None
        entitled = []
        if event:
            entitled = [{"signal": t["signal"], "entry_date": t["entry_date"],
                         "exit_date": t["exit_date"], "shares": int(t["shares"])}
                        for t in trades if t["entry_date"] <= event["record_date"] < t["exit_date"]]
        checked.append({**f, "listed_cash_event": event is not None,
                        "cash_per_share_yuan": float(event["cash_per_share_yuan"]) if event else None,
                        "matches_cash_amount": expected is not None and abs(f["delta_yuan"] - expected) <= tolerance,
                        "record_date_entitled_trades": entitled})
    yearly = {}
    for t in trades:
        year = t["entry_date"][:4]
        y = yearly.setdefault(year, {"trades": 0, "winning_trades": 0, "pnl_ex_dividend_yuan": 0.0})
        pnl = float(t["pnl_ex_dividend_yuan"])
        y["trades"] += 1
        y["winning_trades"] += pnl > 0
        y["pnl_ex_dividend_yuan"] += pnl
    for v in yearly.values():
        v["pnl_ex_dividend_yuan"] = round(v["pnl_ex_dividend_yuan"], 2)
    return {"daily_rows": len(daily_rows), "target_rows": len(grouped[target]),
            "duplicate_keys": duplicates, "pre_close_nulls": nulls,
            "all_five_stock_discontinuities": len(findings),
            "target_discontinuities": checked,
            "unmatched_target_event_dates": sorted(set(event_map) - {x["date"] for x in target_findings}),
            "trade_pnl_by_entry_year": yearly,
            "limitations": ["Archived CSV SHA provenance must be checked separately against published manifest.",
                            "Pre_close gaps matching cash dividends do not prove all adj_factor changes or entitlement rules.",
                            "Daily OHLCV cannot verify auction fills, intraday limit touches or suspensions.",
                            "Historical signals were selected with knowledge of 2025-26; no prospective holdout."]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--daily", nargs="+", required=True)
    parser.add_argument("--events", required=True)
    parser.add_argument("--trades", required=True)
    parser.add_argument("--out", required=True)
    a = parser.parse_args()
    daily = [r for f in a.daily for r in rows(f)]
    result = screen(daily, json.loads(Path(a.events).read_text(encoding="utf-8")), rows(a.trades))
    Path(a.out).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"target={result['target_rows']} candidate_events={len(result['target_discontinuities'])} "
          f"missing_pre_close={len(result['pre_close_nulls'])} duplicate={len(result['duplicate_keys'])}")


if __name__ == "__main__":
    main()
