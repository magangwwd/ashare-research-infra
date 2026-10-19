"""Monthly one-ETF momentum rotation; conservative daily mark-to-market simulation."""
from __future__ import annotations
import argparse, json
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "prices.csv"
OUT = ROOT / "results"
FEE = 0.0005
SLIPPAGE = 0.0005
ALERT_DD = 0.15
EXIT_DD = 0.18

def load_prices(path):
    df = pd.read_csv(path, parse_dates=["date"])
    required = {"date","symbol","open","close"}
    if not required.issubset(df.columns):
        raise ValueError(f"Missing columns: {sorted(required - set(df.columns))}")
    df = df.drop_duplicates(["date","symbol"]).sort_values(["date","symbol"])
    if df.empty or df[["open","close"]].isna().any().any() or (df[["open","close"]] <= 0).any().any():
        raise ValueError("Empty or invalid real price data")
    op = df.pivot(index="date", columns="symbol", values="open").sort_index()
    cl = df.pivot(index="date", columns="symbol", values="close").sort_index()
    # Only dates represented by at least one ETF; no price forward-fill for trading.
    return op, cl

def build_signals(closes):
    # Signals after each calendar month's final observed close. Orders execute at next observed session open.
    signals = {}
    month_ends = closes.groupby([closes.index.year, closes.index.month]).tail(1).index
    for d in month_ends:
        hist = closes.loc[:d]
        scores = {}
        for sym in closes.columns:
            s = hist[sym].dropna()
            if len(s) < 121:
                continue
            px = float(s.iloc[-1])
            sma120 = float(s.iloc[-120:].mean())
            if px <= sma120:
                continue
            score = .2*(px/float(s.iloc[-21])-1) + .5*(px/float(s.iloc[-61])-1) + .3*(px/float(s.iloc[-121])-1)
            scores[sym] = score
        signals[d] = max(scores, key=scores.get) if scores else None
    return signals

def run(path=DATA):
    opens, closes = load_prices(path)
    signals = build_signals(closes)
    dates = list(closes.index)
    cash = 1.0
    shares = 0.0
    held = None
    peak = 1.0
    stop_until_rebalance = False
    rows = []
    trade_rows = []
    previous_equity = 1.0
    for i, d in enumerate(dates):
        # Previous close equity triggers protection; execution happens at today's open.
        if i > 0 and previous_equity > 0 and previous_equity / peak - 1 <= -EXIT_DD:
            if held is not None:
                px = opens.at[d, held] if held in opens.columns else np.nan
                if pd.notna(px) and px > 0:
                    cash += shares * float(px) * (1 - SLIPPAGE) * (1 - FEE)
                    trade_rows.append({"date":d.date().isoformat(),"action":"STOP_EXIT","symbol":held,"price":float(px)})
                    shares, held = 0.0, None
            stop_until_rebalance = True
        # Month-end signal generated after previous close; trade next session at today's open.
        if i > 0 and dates[i-1] in signals:
            target = signals[dates[i-1]]
            stop_until_rebalance = False
            if target != held:
                if held is not None:
                    px = opens.at[d, held] if held in opens.columns else np.nan
                    if pd.notna(px) and px > 0:
                        cash += shares * float(px) * (1 - SLIPPAGE) * (1 - FEE)
                        trade_rows.append({"date":d.date().isoformat(),"action":"SELL","symbol":held,"price":float(px)})
                        shares, held = 0.0, None
                if target is not None:
                    px = opens.at[d, target] if target in opens.columns else np.nan
                    if pd.notna(px) and px > 0:
                        buy_price = float(px) * (1 + SLIPPAGE)
                        shares = cash * (1 - FEE) / buy_price
                        cash = 0.0
                        held = target
                        trade_rows.append({"date":d.date().isoformat(),"action":"BUY","symbol":held,"price":float(px)})
        # Mark at close; no forward-filled price used for trades.
        if held is not None and pd.notna(closes.at[d, held]):
            equity = cash + shares * float(closes.at[d, held])
        else:
            equity = cash
        peak = max(peak, equity)
        dd = equity / peak - 1 if peak else 0
        rows.append({"date":d.date().isoformat(),"equity":equity,"held":held or "CASH","drawdown":dd,
                     "alert_15pct":dd <= -ALERT_DD,"exit_threshold_18pct":dd <= -EXIT_DD})
        previous_equity = equity
    curve = pd.DataFrame(rows)
    curve["daily_return"] = curve.equity.pct_change().fillna(0)
    years = max((pd.to_datetime(curve.date.iloc[-1]) - pd.to_datetime(curve.date.iloc[0])).days / 365.25, 1/365.25)
    total = float(curve.equity.iloc[-1] - 1)
    cagr = float(curve.equity.iloc[-1] ** (1/years) - 1) if curve.equity.iloc[-1] > 0 else -1.0
    maxdd = float(curve.drawdown.min())
    vol = float(curve.daily_return.std(ddof=1) * np.sqrt(252))
    sharpe = float(curve.daily_return.mean() / curve.daily_return.std(ddof=1) * np.sqrt(252)) if curve.daily_return.std(ddof=1) else 0.0
    trades = pd.DataFrame(trade_rows, columns=["date","action","symbol","price"])
    OUT.mkdir(parents=True, exist_ok=True)
    curve.to_csv(OUT/"equity_curve.csv", index=False)
    trades.to_csv(OUT/"trades.csv", index=False)
    summary = {"data_start":curve.date.iloc[0],"data_end":curve.date.iloc[-1],"observations":len(curve),
               "etf_count":int(closes.notna().any().sum()),"total_return":total,"cagr":cagr,
               "max_drawdown":maxdd,"annualized_volatility":vol,"sharpe_rf0":sharpe,
               "trade_events":len(trades),"fees_per_side":FEE,"slippage_per_side":SLIPPAGE,
               "alert_drawdown":ALERT_DD,"exit_drawdown":EXIT_DD,
               "limitations":["No distributions adjustment verified; ETF prices use provider close fields.",
                              "Drawdown stop is evaluated on close and filled at next available open.",
                              "No benchmark comparison or walk-forward split yet; do not treat as final acceptance."]}
    (OUT/"summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    print(json.dumps(summary,indent=2))
    if maxdd < -0.20:
        print("FAIL: maximum drawdown exceeded the 20% target.")
    elif cagr <= 0:
        print("FAIL: CAGR is not positive.")
    else:
        print("PRELIMINARY ONLY: benchmark and out-of-sample validation still required.")
    return summary

if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("csv", nargs="?", default=str(DATA))
    args=parser.parse_args()
    run(Path(args.csv))
