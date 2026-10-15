#!/usr/bin/env python3
"""Conservative Half-Martingale Grid daily-bar research simulator.

This is a research/paper engine only. It never connects to an exchange.
The simulator deliberately avoids intraday sequencing assumptions:
signals are observed at a completed UTC close and any resulting action is
modeled at the NEXT day's recorded open. At most one new grid tranche may be
added per day. If a daily close crosses several grid levels, the remaining
levels are not assumed to have filled intraday.

Risk rules:
  - spot only, no leverage;
  - max 5 layers;
  - tranche multipliers 1.00/1.15/1.25/1.35/1.50;
  - basket take profit +0.8%;
  - account drawdown >=8% blocks new adds and cuts half the position;
  - account drawdown >=15% liquidates and permanently halts;
  - leaving RANGE liquidates the HMG basket instead of continuing to average down.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
from datetime import date
from pathlib import Path


def load_rows(path):
    rows = []
    with Path(path).open(newline="", encoding="utf8") as f:
        for r in csv.DictReader(f):
            rows.append({
                "date": date.fromisoformat(r["date"]),
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": float(r["volume"]),
            })
    if len(rows) < 220:
        raise ValueError("need >=220 daily bars")
    if any((b["date"] - a["date"]).days != 1 for a, b in zip(rows, rows[1:])):
        raise ValueError("daily data has a gap or duplicate")
    return rows


def router_module():
    path = Path(__file__).resolve().parent / "router.py"
    spec = importlib.util.spec_from_file_location("btc_regime_router", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def simulate(rows, regime_by_date, initial_cash=1000.0, cost_rate=0.0015,
             account_cap=0.25, max_layers=5,
             multipliers=(1.0, 1.15, 1.25, 1.35, 1.50),
             basket_tp=0.008):
    if max_layers != len(multipliers):
        raise ValueError("max_layers must equal multiplier count")

    cash = float(initial_cash)
    btc = 0.0
    hwm = float(initial_cash)
    anchor = None
    spacing_pct = None
    layer = 0
    budget = 0.0
    avg_cost = None
    halted = False
    reduced = False
    trades = []
    curve = []

    def nav(px):
        return cash + btc * px

    def sell_all(px, d, reason):
        nonlocal cash, btc, anchor, spacing_pct, layer, budget, avg_cost
        if btc <= 1e-14:
            return
        qty = btc
        cash += qty * px * (1.0 - cost_rate)
        trades.append({
            "date": d.isoformat(), "side": "SELL",
            "quantity_btc": qty, "price": px,
            "reason": reason, "cost_rate": cost_rate,
        })
        btc = 0.0
        anchor = spacing_pct = avg_cost = None
        layer = 0
        budget = 0.0

    for i in range(1, len(rows)):
        prev = rows[i - 1]
        cur = rows[i]
        prev_regime = regime_by_date.get(prev["date"].isoformat(), "TRANSITION")

        current_nav = nav(prev["close"])
        hwm = max(hwm, current_nav)
        dd = (hwm - current_nav) / hwm if hwm else 0.0

        # Emergency controls have precedence over all other actions.
        if dd >= 0.15 and not halted:
            halted = True
            sell_all(cur["open"], cur["date"], "GLOBAL_DRAWDOWN_15PCT")
        elif dd >= 0.08 and not reduced and btc > 0:
            reduced = True
            # Reduce half at next open. No new grid adds after this point.
            qty = btc * 0.5
            cash += qty * cur["open"] * (1.0 - cost_rate)
            btc -= qty
            trades.append({
                "date": cur["date"].isoformat(), "side": "SELL",
                "quantity_btc": qty, "price": cur["open"],
                "reason": "DRAWDOWN_8PCT_RISK_REDUCTION",
                "cost_rate": cost_rate,
            })
            if btc <= 1e-14:
                anchor = spacing_pct = avg_cost = None
                layer = 0
                budget = 0.0

        if halted:
            curve.append({"date": cur["date"].isoformat(), "nav": nav(cur["close"])})
            continue

        # HMG is only permitted in a confirmed RANGE.
        if btc > 0 and prev_regime != "RANGE":
            sell_all(cur["open"], cur["date"], "REGIME_EXIT")
        elif btc > 0 and avg_cost is not None and prev["close"] >= avg_cost * (1.0 + basket_tp):
            sell_all(cur["open"], cur["date"], "BASKET_TP")
        elif btc == 0 and prev_regime == "RANGE" and not reduced:
            # Start a fresh basket on next day's open.
            router = router_module()
            feature_map = {f.date: f for f in router.compute_features(rows[:i])}
            f = feature_map.get(prev["date"].isoformat())
            if f is not None:
                spacing_pct = max(0.018, min(0.90 * f.atr14_pct, 0.035))
                budget = min(account_cap * current_nav, cash)
                tranche_cash = budget * multipliers[0] / sum(multipliers)
                qty = tranche_cash / (cur["open"] * (1.0 + cost_rate))
                if qty > 0:
                    cash -= qty * cur["open"] * (1.0 + cost_rate)
                    btc += qty
                    anchor = cur["open"]
                    avg_cost = cur["open"] * (1.0 + cost_rate)
                    layer = 1
                    trades.append({
                        "date": cur["date"].isoformat(), "side": "BUY",
                        "quantity_btc": qty, "price": cur["open"],
                        "reason": "RANGE_INITIAL_ENTRY",
                        "layer": layer, "spacing_pct": spacing_pct,
                        "cost_rate": cost_rate,
                    })
        elif btc > 0 and prev_regime == "RANGE" and not reduced and layer < max_layers:
            next_level = anchor * (1.0 - spacing_pct * layer)
            if prev["close"] <= next_level:
                tranche_cash = budget * multipliers[layer] / sum(multipliers)
                tranche_cash = min(tranche_cash, cash)
                qty = tranche_cash / (cur["open"] * (1.0 + cost_rate))
                if qty > 0:
                    cash -= qty * cur["open"] * (1.0 + cost_rate)
                    old_value = btc * avg_cost
                    btc += qty
                    fill_cost = cur["open"] * (1.0 + cost_rate)
                    avg_cost = (old_value + qty * fill_cost) / btc
                    layer += 1
                    trades.append({
                        "date": cur["date"].isoformat(), "side": "BUY",
                        "quantity_btc": qty, "price": cur["open"],
                        "reason": "GRID_ADD",
                        "layer": layer, "spacing_pct": spacing_pct,
                        "cost_rate": cost_rate,
                    })

        end_nav = nav(cur["close"])
        hwm = max(hwm, end_nav)
        end_dd = (hwm - end_nav) / hwm if hwm else 0.0
        curve.append({
            "date": cur["date"].isoformat(),
            "nav": end_nav,
            "drawdown": end_dd,
            "btc_qty": btc,
            "cash": cash,
            "layer": layer,
            "regime": prev_regime,
        })

    final_nav = nav(rows[-1]["close"])
    hwm = max(hwm, final_nav)
    max_dd = max((x.get("drawdown", 0.0) for x in curve), default=0.0)
    return {
        "initial_cash": initial_cash,
        "final_nav": final_nav,
        "total_return": final_nav / initial_cash - 1.0,
        "max_drawdown": max_dd,
        "trades": len(trades),
        "max_layer": max((int(t.get("layer", 0)) for t in trades), default=0),
        "halted": halted,
        "reduced": reduced,
        "curve": curve,
        "trades_detail": trades,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--regime-csv", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--cost-multiplier", type=float, default=1.0)
    args = p.parse_args()

    rows = load_rows(args.input)
    regime_by_date = {}
    with Path(args.regime_csv).open(newline="", encoding="utf8") as f:
        for r in csv.DictReader(f):
            regime_by_date[r["date"]] = r["confirmed_regime"]

    result = simulate(rows, regime_by_date, cost_rate=0.0015 * args.cost_multiplier)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    summary = {k: v for k, v in result.items() if k not in {"curve", "trades_detail"}}
    (out / "hmg_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf8"
    )
    with (out / "hmg_equity.csv").open("w", newline="", encoding="utf8") as f:
        w = csv.DictWriter(f, fieldnames=list(result["curve"][0]))
        w.writeheader()
        w.writerows(result["curve"])
    with (out / "hmg_trades.csv").open("w", newline="", encoding="utf8") as f:
        if result["trades_detail"]:
            w = csv.DictWriter(f, fieldnames=sorted(result["trades_detail"][0]))
            w.writeheader()
            w.writerows(result["trades_detail"])
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
