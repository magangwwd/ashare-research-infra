"""Forward A/B paper comparison from *later* independently observed fills.

This cannot infer next-session open or execution from same-day R1.  A and B
always share identical observed prices.  Output is a research trade proxy,
never a fully audited portfolio backtest or real broker fills.
"""
from __future__ import annotations
import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path

from ai_research.manual_ai_review_pack import AUTHORIZATION, SYMBOLS

SLIPPAGE_ONE_SIDE = 0.002
MAX_SINGLE_WEIGHT = 0.10
MAX_EQUITY_RISK_PER_SIGNAL = 0.0025
COMMISSION_MIN_CNY = 5.0
BUY_LOT = 100


def metric(returns):
    if not returns:
        return {"completed": 0, "win_rate": None, "mean_net_trade_return": None,
                "sum_weighted_pnl_fraction": None, "proxy_max_drawdown": None}
    equity, high, draw = 1.0, 1.0, 0.0
    for t in sorted(returns, key=lambda x: (x["exit_date"], x["symbol"])):
        equity *= 1 + t["weight"] * t["net_trade_return"]
        high = max(high, equity)
        draw = max(draw, 1 - equity / high)
    return {"completed": len(returns),
            "win_rate": round(sum(t["net_trade_return"] > 0 for t in returns) / len(returns), 5),
            "mean_net_trade_return": round(sum(t["net_trade_return"] for t in returns) / len(returns), 6),
            "sum_weighted_pnl_fraction": round(equity - 1, 6),
            "proxy_max_drawdown": round(draw, 6)}


def evaluate(reports, observed):
    if not reports:
        raise ValueError("No immutable as-of decisions")
    if not isinstance(observed, dict) or not isinstance(observed.get("records"), list):
        raise ValueError("Observations must have records list")
    decisions = {}
    for report in reports:
        day = report.get("as_of")
        if (report.get("authorization") != AUTHORIZATION
                or report.get("version") != "FIVE_STOCK_DECISION_V1"
                or [s["symbol"] for s in report.get("stocks", [])] != list(SYMBOLS)):
            raise ValueError("Unknown or incomplete five-stock research report")
        date.fromisoformat(day)
        if day in decisions:
            raise ValueError("Duplicate immutable research date")
        decisions[day] = report
    fills = {(r.get("signal_date"), r.get("symbol")): r for r in observed["records"]}
    if len(fills) != len(observed["records"]):
        raise ValueError("Duplicate observed fill record")
    unexpected = set(fills) - {(d, s["symbol"]) for d, r in decisions.items()
                               for s in r["stocks"] if s["r1_candidate"]}
    if unexpected:
        raise ValueError("Observed fill without an original dated R1 candidate")
    a, b, pending, rejected = [], [], [], []
    for day, report in sorted(decisions.items()):
        for stock in report["stocks"]:
            if not stock["r1_candidate"]:
                continue
            symbol = stock["symbol"]
            fill = fills.get((day, symbol))
            if fill is None:
                pending.append({"signal_date": day, "symbol": symbol,
                                "reason": "NO_FUTURE_VERIFIED_OBSERVATION"})
                continue
            entry_day = fill.get("entry_date")
            exit_day = fill.get("exit_date")
            try:
                entry = float(fill["entry_raw_cny"])
                exit_ = float(fill["exit_raw_cny"])
                stop = float(stock["r1_estimated_2atr_fraction"])
                date.fromisoformat(entry_day)
                date.fromisoformat(exit_day)
                valid = (entry_day > day and exit_day > entry_day and
                         0.015 <= stop <= 0.04 and entry > 0 and exit_ > 0 and
                         fill.get("same_adjustment_basis") is True and
                         fill.get("entry_quality_valid") is True and
                         fill.get("exit_quality_valid") is True and
                         fill.get("entry_risk_pass") is True and
                         fill.get("entry_order_book_liquidity_confirmed") is True and
                         isinstance(fill.get("source_sha256"), str) and
                         len(fill["source_sha256"]) == 64 and
                         isinstance(fill.get("paid_gap_including_slippage"), (float, int)) and
                         fill["paid_gap_including_slippage"] <= 0.01)
            except (ValueError, TypeError, KeyError):
                valid = False
            if not valid:
                rejected.append({"signal_date": day, "symbol": symbol,
                                 "reason": "PIT_NEXT_OPEN_OR_EXECUTION_UNVERIFIED"})
                continue
            buy = entry * (1 + SLIPPAGE_ONE_SIDE)
            sell = exit_ * (1 - SLIPPAGE_ONE_SIDE)
            commission_buy = max(COMMISSION_MIN_CNY, BUY_LOT * buy * 0.0003)
            commission_sell = max(COMMISSION_MIN_CNY, BUY_LOT * sell * 0.0003)
            net = ((BUY_LOT * (sell - buy) - commission_buy - commission_sell)
                   / (BUY_LOT * buy + commission_buy))
            weight = min(MAX_SINGLE_WEIGHT, MAX_EQUITY_RISK_PER_SIGNAL / stop)
            event = {"signal_date": day, "entry_date": entry_day, "exit_date": exit_day,
                     "symbol": symbol, "source_sha256": fill["source_sha256"],
                     "net_trade_return": round(net, 7),
                     "weight": round(weight, 7), "estimated_stop_fraction": stop}
            a.append(event)
            if stock.get("research_status") == "SETUP_PAPER_NEXT_OPEN_UNVERIFIED":
                b.append(event)
    avoided = { (t["signal_date"], t["symbol"]) for t in a } - {
        (t["signal_date"], t["symbol"]) for t in b}
    avoided_events = [t for t in a if (t["signal_date"], t["symbol"]) in avoided]
    status = ("NO_ORIGINAL_R1_CANDIDATES" if not any(
        s["r1_candidate"] for r in reports for s in r["stocks"])
              else ("INCOMPLETE_DO_NOT_CLAIM_OUTPERFORMANCE" if pending or rejected
                    else "OBSERVED_PAPER_PROXY_ONLY"))
    return {
        "status": status, "analysis": "A/B_RESEARCH_PROXY_NOT_PORTFOLIO_BACKTEST",
        "authorization": AUTHORIZATION, "signals_considered": len(a) + len(pending) + len(rejected),
        "a_r1": metric(a), "b_r1_plus_review": metric(b),
        "blocked_losing_events": sum(t["net_trade_return"] < 0 for t in avoided_events),
        "missed_profitable_events": sum(t["net_trade_return"] > 0 for t in avoided_events),
        "pending": pending, "rejected": rejected, "common_fill_protocol": True,
        "assumptions": {"entry": "independently observed next-session open, paper-only",
                        "exit": "independently observed future close, same for both groups",
                        "slippage_each_side": SLIPPAGE_ONE_SIDE,
                        "min_commission_each_side_cny": COMMISSION_MIN_CNY,
                        "buy_lot": BUY_LOT, "max_single_weight": MAX_SINGLE_WEIGHT,
                        "equity_risk_limit": MAX_EQUITY_RISK_PER_SIGNAL},
        "limitations": ["Requires authentic later opening/exit observation plus exchange constraints",
                        "Only identical observed entry/exit facts; no fills fabricated from R1",
                        "Proxy drawdown does not account for overlapping positions or cash",
                        "Forward evidence begins 2026-10-12; no backdated profitability claim"],
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--report-json", type=Path, action="append", required=True,
                   help="Repeat for immutable future daily five-stock reports")
    p.add_argument("--observed-executions", type=Path, required=True)
    p.add_argument("--output-json", type=Path, required=True)
    args = p.parse_args()
    result = evaluate(
        [json.loads(x.read_text(encoding="utf-8")) for x in args.report_json],
        json.loads(args.observed_executions.read_text(encoding="utf-8")))
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(result["status"])


if __name__ == "__main__":
    main()
