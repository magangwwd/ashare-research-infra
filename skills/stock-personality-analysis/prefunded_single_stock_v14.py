#!/usr/bin/env python3
"""v1.4 retrospective single-stock 100k prefunded auction cash audit.

The 60 frozen trades and OHLCV are user-provided archived research inputs.
Not real fills, not a forward holdout, and not a production data writer.
"""
import argparse
import csv
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from dividend_cash_v11 import load_daily, validate_events

PRIORITY = ["D_breakout", "G_upper_wick", "B_breakdown", "F_tight_lowvol"]


def size(open_price, budget=40000., min_fee=5.):
    unit = open_price * 1.0005
    shares = int(budget // (100 * unit)) * 100
    while shares and shares * unit + max(shares * unit * .0003, min_fee) > budget + 1e-8:
        shares -= 100
    if not shares:
        raise ValueError("one board lot unaffordable within per-trade budget")
    gross = shares * unit
    return shares, gross + max(gross * .0003, min_fee)


def replay(trades, daily, events, initial=100000., budget=40000.,
           min_fee=5., dividend_tax=0., allow_deficit=False):
    """Check buy against cash *before* same-open sale and ex-date dividend.

    Sales are booked first for end-of-auction accounting only; they cannot
    fund a new opening-auction order. Fixed budget means fixed board-lot size
    independent of the initial account balance.
    """
    if initial <= 0 or budget <= 0 or min_fee < 0 or not 0 <= dividend_tax <= 1:
        raise ValueError("invalid account inputs")
    entries = {}
    for t in trades:
        a, b, c = t["signal_date"], t["entry_date"], t["exit_date"]
        if not a < b < c or b in entries:
            raise ValueError("invalid frozen trade chronology")
        if b not in daily or c not in daily:
            raise ValueError("missing trade day")
        if abs(float(t["entry_open"]) - daily[b]["open"]) > 1e-6 or abs(float(t["exit_open"]) - daily[c]["open"]) > 1e-6:
            raise ValueError("trade price mismatch")
        entries[b] = t
    ex_events = {e["ex_date"]: e for e in events}
    if len(ex_events) != len(events):
        raise ValueError("duplicate ex-date")
    cash = float(initial)
    holding = None
    trades_done, curve, cash_ledger, dividends = [], [], [], []
    min_initial = 0.
    binding_date = None
    peak_buy = 0.
    peak_flip_commitment = 0.
    for day, price in daily.items():
        cash_before_auction = cash
        old = holding if holding and holding["exit_date"] == day else None
        new = entries.get(day)
        if new and holding and not old:
            raise ValueError("overlapping position")
        eligible = holding["shares"] if holding else 0
        shares, spent = size(price["open"], budget, min_fee) if new else (0, 0.)
        if new:
            need = spent - (cash_before_auction - initial)
            if need > min_initial:
                min_initial, binding_date = need, day
            if cash_before_auction + 1e-7 < spent and not allow_deficit:
                raise ValueError("pre-auction cash insufficient: " + day)
            peak_buy = max(peak_buy, spent)
            if old:
                peak_flip_commitment = max(peak_flip_commitment, old["shares"] * price["open"] + spent)
        if old:
            gross = old["shares"] * price["open"] * .9995
            proceeds = gross - max(gross * .0003, min_fee) - gross * .0005
            cash += proceeds
            trades_done.append({**old, "exit_open": price["open"],
                                "proceeds_yuan": round(proceeds, 4),
                                "pnl_ex_dividend_yuan": round(proceeds - old["spent"], 4),
                                "net_trade_return_pct": round(100 * (proceeds / old["spent"] - 1), 5)})
            cash_ledger.append({"date": day, "side": "sell", "cash_delta_yuan": round(proceeds, 4),
                                "cash_after_yuan": round(cash, 4)})
            holding = None
        if new:
            cash -= spent
            holding = {"signal": new["signal"], "entry_date": day, "exit_date": new["exit_date"],
                       "shares": shares, "spent": spent, "entry_open": price["open"],
                       "pre_auction_cash_yuan": round(cash_before_auction, 4),
                       "pre_auction_buy_reservation_yuan": round(spent, 4),
                       "same_open_flip": bool(old)}
            cash_ledger.append({"date": day, "side": "buy", "cash_delta_yuan": round(-spent, 4),
                                "cash_after_yuan": round(cash, 4),
                                "pre_auction_cash_yuan": round(cash_before_auction, 4)})
        if day in ex_events:
            event = ex_events[day]
            gross_div = round(eligible * float(event["cash_per_share_yuan"]), 2)
            tax = round(gross_div * dividend_tax, 2)
            cash += gross_div - tax
            dividends.append({"ex_date": day, "shares": eligible, "gross_yuan": gross_div, "tax_yuan": tax})
            cash_ledger.append({"date": day, "side": "dividend_after_open",
                                "cash_delta_yuan": gross_div - tax, "cash_after_yuan": round(cash, 4)})
        if initial - cash > min_initial:
            min_initial, binding_date = initial - cash, day
        if cash < -1e-7 and not allow_deficit:
            raise ValueError("negative cash")
        curve.append({"date": day, "cash_yuan": round(cash, 4),
                      "shares": holding["shares"] if holding else 0, "close": price["close"],
                      "equity_yuan": round(cash + (holding["shares"] * price["close"] if holding else 0), 4)})
    if holding or len(trades_done) != len(trades):
        raise ValueError("unclosed or missing trade")
    peak, max_dd = initial, 0.
    for row in curve:
        peak = max(peak, row["equity_yuan"])
        row["drawdown_pct"] = round(100 * (row["equity_yuan"] / peak - 1), 5)
        max_dd = min(max_dd, row["drawdown_pct"])
    ending = curve[-1]["equity_yuan"]
    groups = defaultdict(list)
    peers = {t["entry_date"]: t for t in trades}
    for t in trades_done:
        groups[t["signal"]].append(t)
    signals = {key: {"count": len(values),
                     "win_rate_pct": round(100 * sum(t["pnl_ex_dividend_yuan"] > 0 for t in values) / len(values), 2),
                     "net_pnl_ex_dividend_yuan": round(sum(t["pnl_ex_dividend_yuan"] for t in values), 2),
                     "same_date_peer_alpha_mean_gross_pp": round(
                         sum(float(peers[t["entry_date"]]["target_minus_peer_gross_pp"]) for t in values) / len(values), 4)}
               for key, values in groups.items()}
    minimum = math.ceil((min_initial - 1e-8) * 100) / 100
    return {"initial_total_account_yuan": initial, "per_trade_budget_yuan": budget,
            "ending_equity_yuan": round(ending, 2),
            "total_account_return_pct": round(100 * (ending / initial - 1), 4),
            "net_profit_including_dividends_yuan": round(ending - initial, 2),
            "nominal_40k_profit_ratio_pct_not_account_return": round(100 * (ending - initial) / budget, 4),
            "max_drawdown_pct": round(max_dd, 4), "trades_executed": len(trades_done),
            "cash_rejections": 0, "same_open_flip_count": sum(t["same_open_flip"] for t in trades_done),
            "peak_stock_buy_spend_yuan": round(peak_buy, 2),
            "peak_same_open_old_stock_plus_new_buy_yuan": round(peak_flip_commitment, 2),
            "minimum_initial_cash_required_yuan": minimum, "minimum_required_binding_date": binding_date,
            "minimum_extra_buffer_over_40k_yuan": round(max(0, minimum - budget), 2),
            "minimum_cash_balance_yuan": round(min(r["cash_yuan"] for r in curve), 2),
            "maximum_cash_balance_yuan": round(max(r["cash_yuan"] for r in curve), 2),
            "gross_dividends_yuan": round(sum(x["gross_yuan"] for x in dividends), 2),
            "signals": signals, "trades": trades_done, "daily_equity": curve,
            "cash_ledger": cash_ledger, "dividends": dividends}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--audit", required=True)
    p.add_argument("--daily", nargs="+", required=True)
    p.add_argument("--events", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    audit = json.loads(Path(a.audit).read_text(encoding="utf-8"))
    if audit["priority"] != PRIORITY or len(audit["selected_trades"]) != 60:
        raise ValueError("frozen schedule changed")
    daily = load_daily(a.daily)
    if len(daily) != audit["input_sessions"] or len(daily) != 755:
        raise ValueError("daily coverage changed")
    events = validate_events(daily, json.loads(Path(a.events).read_text(encoding="utf-8")))
    gross = replay(audit["selected_trades"], daily, events)
    tax20 = replay(audit["selected_trades"], daily, events, dividend_tax=.2)
    minimum = gross["minimum_initial_cash_required_yuan"]
    replay(audit["selected_trades"], daily, events, initial=minimum + .01)
    try:
        replay(audit["selected_trades"], daily, events, initial=minimum - .01)
    except ValueError as exc:
        if "pre-auction cash insufficient" not in str(exc):
            raise
    else:
        raise AssertionError("minimum cash bound not binding")
    result = {"scope": "RETROSPECTIVE, NOT FILL-VERIFIED OR FORWARD",
              "source_sha256": {Path(x).name: hashlib.sha256(Path(x).read_bytes()).hexdigest()
                                for x in [a.audit, a.events, *a.daily]},
              "gross": gross, "tax20_sensitivity": tax20}
    Path(a.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print({key: gross[key] for key in ("ending_equity_yuan", "total_account_return_pct",
          "max_drawdown_pct", "trades_executed", "minimum_initial_cash_required_yuan")})


if __name__ == "__main__":
    main()
