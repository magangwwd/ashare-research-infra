"""A single historical *price-path* paper trade, not a confirmed fill or RL trade."""
from __future__ import annotations
import argparse
import json

def simulate(signal_close: float, entry_open: float, exit_open: float,
             fee_rate: float, slippage_rate: float) -> dict:
    if min(signal_close, entry_open, exit_open) <= 0:
        raise ValueError("All prices must be positive")
    if not (0 <= fee_rate < 0.1 and 0 <= slippage_rate < 0.1):
        raise ValueError("Invalid cost assumptions")
    gross = exit_open / entry_open - 1
    entry_paid = entry_open * (1 + slippage_rate) * (1 + fee_rate)
    exit_received = exit_open * (1 - slippage_rate) * (1 - fee_rate)
    net = exit_received / entry_paid - 1
    return {
        "signal_close": signal_close,
        "entry_reference_open": entry_open,
        "exit_reference_open": exit_open,
        "gross_reference_return": round(gross, 8),
        "net_reference_return": round(net, 8),
        "fee_rate_each_side": fee_rate,
        "slippage_rate_each_side": slippage_rate,
        "fill_confirmed": False,
        "rl_trained": False,
        "warning": "Price-path illustration only. Open auction fills, limits, ST history, duties and T+1 execution must be independently validated."
    }

def main():
    p = argparse.ArgumentParser()
    for name in ("signal-close","entry-open","exit-open"):
        p.add_argument("--"+name, type=float, required=True)
    for name in ("signal-date","entry-date","exit-date"):
        p.add_argument("--"+name, required=True)
    p.add_argument("--fee-rate", type=float, default=0.0003)
    p.add_argument("--slippage-rate", type=float, default=0.001)
    a = p.parse_args()
    if not (a.signal_date < a.entry_date < a.exit_date):
        raise ValueError("Date sequence must be strictly increasing")
    out = simulate(a.signal_close,a.entry_open,a.exit_open,a.fee_rate,a.slippage_rate)
    out.update(symbol="603580.SH",signal_date=a.signal_date,entry_date=a.entry_date,exit_date=a.exit_date)
    print(json.dumps(out,indent=2,ensure_ascii=False))

if __name__ == "__main__":
    main()
