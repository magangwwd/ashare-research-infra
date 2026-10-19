"""Monthly one-ETF momentum rotation with explicit position stop and safe price marking."""
from __future__ import annotations
import argparse, json, subprocess, sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "prices.csv"
OUT = ROOT / "results"
FEE = 0.0005
SLIPPAGE = 0.0005
ALERT_DD = 0.15
POSITION_EXIT_DD = 0.12

def load_prices(path):
    df = pd.read_csv(path, parse_dates=["date"])
    required = {"date", "symbol", "open", "close"}
    if not required.issubset(df.columns):
        raise ValueError(f"Missing columns: {sorted(required - set(df.columns))}")
    df = df.drop_duplicates(["date", "symbol"]).sort_values(["date", "symbol"])
    if df.empty or df[["open", "close"]].dropna().le(0).any().any():
        raise ValueError("Empty or invalid real price data")
    op = df.pivot(index="date", columns="symbol", values="open").sort_index()
    cl = df.pivot(index="date", columns="symbol", values="close").sort_index()
    return op, cl

def build_signals(closes):
    # Signal after each calendar month's last observed close; execute at the next usable open.
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
    # Forward-fill is ONLY for portfolio valuation on missing/suspended sessions.
    # Signals and executions always use observed prices; missing opens never become trades.
    mark_closes = closes.ffill()
    dates = list(closes.index)
    cash, shares, held = 1.0, 0.0, None
    portfolio_peak = 1.0
    position_peak = None
    stopped_symbol = None
    rows, trade_rows = [], []
    for i, d in enumerate(dates):
        stopped_today = False

        # Position-level trailing stop: evaluate the prior close, execute at today's observed open.
        if i > 0 and held is not None and position_peak is not None:
            prev_d = dates[i-1]
            prev_close = mark_closes.at[prev_d, held]
            if pd.notna(prev_close) and prev_close / position_peak - 1 <= -POSITION_EXIT_DD:
                stop_px = opens.at[d, held] if held in opens.columns else np.nan
                if pd.notna(stop_px) and stop_px > 0:
                    cash += shares * float(stop_px) * (1 - SLIPPAGE) * (1 - FEE)
                    trade_rows.append({"date": d.date().isoformat(), "action": "POSITION_STOP", "symbol": held, "price": float(stop_px)})
                    stopped_symbol = held
                    shares, held, position_peak = 0.0, None, None
                    stopped_today = True

        # Month-end signal generated at the previous close. Rebalance atomically:
        # if any required open is missing, skip the whole rebalance rather than corrupt equity.
        if i > 0 and dates[i-1] in signals and not stopped_today:
            target = signals[dates[i-1]]
            if target == stopped_symbol:
                target = None
            if target != held:
                old_symbol = held
                old_px = opens.at[d, old_symbol] if old_symbol is not None and old_symbol in opens.columns else np.nan
                new_px = opens.at[d, target] if target is not None and target in opens.columns else np.nan
                prices_ready = (
                    (old_symbol is None or (pd.notna(old_px) and old_px > 0))
                    and (target is None or (pd.notna(new_px) and new_px > 0))
                )
                if prices_ready:
                    if held is not None:
                        cash += shares * float(old_px) * (1 - SLIPPAGE) * (1 - FEE)
                        trade_rows.append({"date": d.date().isoformat(), "action": "SELL", "symbol": held, "price": float(old_px)})
                        shares, held, position_peak = 0.0, None, None
                    if target is not None:
                        buy_price = float(new_px) * (1 + SLIPPAGE)
                        shares = cash * (1 - FEE) / buy_price
                        cash = 0.0
                        held = target
                        position_peak = float(mark_closes.at[d, held]) if pd.notna(mark_closes.at[d, held]) else float(new_px)
                        trade_rows.append({"date": d.date().isoformat(), "action": "BUY", "symbol": held, "price": float(new_px)})
                    if target is not None and target != stopped_symbol:
                        stopped_symbol = None

        # Mark to market using last observed close if an ETF has no row on this exchange session.
        if held is not None and pd.notna(mark_closes.at[d, held]):
            close_px = float(mark_closes.at[d, held])
            position_peak = max(position_peak or close_px, close_px)
            equity = cash + shares * close_px
        else:
            equity = cash
        portfolio_peak = max(portfolio_peak, equity)
        dd = equity / portfolio_peak - 1 if portfolio_peak else 0
        rows.append({"date": d.date().isoformat(), "equity": equity, "held": held or "CASH",
                     "drawdown": dd, "alert_15pct": dd <= -ALERT_DD})
    curve = pd.DataFrame(rows)
    curve["daily_return"] = curve.equity.pct_change().replace([np.inf, -np.inf], np.nan).fillna(0)
    years = max((pd.to_datetime(curve.date.iloc[-1]) - pd.to_datetime(curve.date.iloc[0])).days / 365.25, 1/365.25)
    total = float(curve.equity.iloc[-1] - 1)
    cagr = float(curve.equity.iloc[-1] ** (1/years) - 1) if curve.equity.iloc[-1] > 0 else -1.0
    maxdd = float(curve.drawdown.min())
    vol = float(curve.daily_return.std(ddof=1) * np.sqrt(252))
    sharpe = float(curve.daily_return.mean() / curve.daily_return.std(ddof=1) * np.sqrt(252)) if curve.daily_return.std(ddof=1) else 0.0
    trades = pd.DataFrame(trade_rows, columns=["date", "action", "symbol", "price"])
    OUT.mkdir(parents=True, exist_ok=True)
    curve.to_csv(OUT / "equity_curve.csv", index=False)
    trades.to_csv(OUT / "trades.csv", index=False)
    summary = {
        "data_start": curve.date.iloc[0], "data_end": curve.date.iloc[-1],
        "observations": len(curve), "etf_count": int(closes.notna().any().sum()),
        "total_return": total, "cagr": cagr, "max_drawdown": maxdd,
        "annualized_volatility": vol, "sharpe_rf0": sharpe, "trade_events": len(trades),
        "fees_per_side": FEE, "slippage_per_side": SLIPPAGE,
        "alert_drawdown": ALERT_DD, "position_trailing_stop": POSITION_EXIT_DD,
        "limitations": [
            "ETF prices use Tushare fund_adj factors scaled to the latest factor; independently validate total-return semantics against distributions and a benchmark.",
            "Position stop is evaluated on prior close and filled at the next observed open; gap risk can exceed the stop threshold.",
            "Missing closes are carried forward for valuation only; missing execution prices cause the rebalance to be skipped.",
            "Calendar gaps remain under review; no benchmark comparison or walk-forward split yet. Do not treat as final acceptance."
        ]
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    if maxdd < -0.20:
        print("FAIL: maximum drawdown exceeded the 20% target.")
    elif cagr <= 0:
        print("FAIL: CAGR is not positive.")
    else:
        print("PRELIMINARY ONLY: benchmark and out-of-sample validation still required.")
    return summary

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("csv", nargs="?", default=str(DATA))
    args = parser.parse_args()
    run(Path(args.csv))
    # The existing workflow's backtest step also runs the new exploratory comparison harness.
    subprocess.run([sys.executable, str(Path(__file__).with_name("experiments") / "run_strategy_experiments.py")], check=True)
