"""Download a reproducible ETF price panel from Tushare Pro."""
from __future__ import annotations
import os
import subprocess
import sys
from pathlib import Path
import pandas as pd
import tushare as ts

# Liquid broad-market / style / defensive / gold ETF pool. Listed dates are respected by API.
ETF_POOL = {
    "510300.SH": "CSI300",
    "510500.SH": "CSI500",
    "159915.SZ": "ChiNext",
    "510050.SH": "SSE50",
    "510880.SH": "Dividend",
    "518880.SH": "Gold",
    "512100.SH": "CSI1000",
    "512690.SH": "Liquor",
}
START = "20150101"
OUT = Path("etf_single_rotation/data/prices.csv")

def main():
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if not token:
        raise SystemExit("TUSHARE_TOKEN missing; refusing to fabricate price data")
    ts.set_token(token)
    pro = ts.pro_api()
    chunks = []
    failures = []
    for code, label in ETF_POOL.items():
        try:
            df = pro.fund_daily(ts_code=code, start_date=START, end_date=pd.Timestamp.now().strftime("%Y%m%d"),
                                fields="ts_code,trade_date,open,high,low,close,pre_close,vol,amount")
            if df is None or df.empty:
                failures.append(f"{code}: empty response")
                continue
            df["date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
            df["symbol"] = code
            df["name"] = label
            for col in ["open", "close", "pre_close", "vol", "amount"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            chunks.append(df[["date", "symbol", "name", "open", "close", "pre_close", "vol", "amount"]])
        except Exception as exc:
            # Do not print provider response bodies or credentials.
            failures.append(f"{code}: request failed ({type(exc).__name__})")
    if not chunks:
        raise SystemExit("No ETF price data returned. " + "; ".join(failures))
    panel = pd.concat(chunks, ignore_index=True).drop_duplicates(["date", "symbol"])
    panel = panel.sort_values(["date", "symbol"])
    if panel[["date", "symbol", "open", "close"]].isna().any().any():
        raise SystemExit("Price data contains missing required fields; no backtest published")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    panel.to_csv(OUT, index=False)
    print(f"Downloaded {len(panel)} real rows, {panel.symbol.nunique()} ETFs, {panel.date.min()} to {panel.date.max()}")
    if failures:
        print("INCOMPLETE ETF POOL: " + "; ".join(failures))
        raise SystemExit("One or more ETF requests failed; refusing to publish performance results")
    # Compatibility with earlier workflow reruns, whose step list is immutable.
    # The current workflow also audits explicitly; duplicate audits are harmless.
    subprocess.run([sys.executable, str(Path(__file__).with_name("audit_data.py"))], check=True)

if __name__ == "__main__":
    main()
