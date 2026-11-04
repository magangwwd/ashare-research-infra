"""Download ETF daily prices, ETF adjustment factors, and SSE trading calendar."""
from __future__ import annotations
import os
import subprocess
import sys
from pathlib import Path
import pandas as pd
import tushare as ts

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
END = pd.Timestamp.now().strftime("%Y%m%d")
DATA_DIR = Path("etf_single_rotation/data")
OUT = DATA_DIR / "prices.csv"
CALENDAR_OUT = DATA_DIR / "trade_calendar.csv"

def fetch_adjustment_factors(pro, code):
    """fund_adj has a 2,000-row request limit; fetch one calendar year per request."""
    parts = []
    start_year = int(START[:4])
    end_year = int(END[:4])
    for year in range(start_year, end_year + 1):
        start_date = max(START, f"{year}0101")
        end_date = min(END, f"{year}1231")
        factors = pro.fund_adj(ts_code=code, start_date=start_date, end_date=end_date,
                               fields="ts_code,trade_date,adj_factor")
        if factors is not None and not factors.empty:
            parts.append(factors)
    if not parts:
        raise ValueError("fund_adj returned no rows")
    out = pd.concat(parts, ignore_index=True).drop_duplicates("trade_date")
    out["date"] = pd.to_datetime(out["trade_date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
    out["adj_factor"] = pd.to_numeric(out["adj_factor"], errors="coerce")
    out = out[["date", "adj_factor"]].dropna().sort_values("date")
    if out.empty or (out["adj_factor"] <= 0).any():
        raise ValueError("invalid adjustment factors")
    return out

def main():
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if not token:
        raise SystemExit("TUSHARE_TOKEN missing; refusing to fabricate price data")
    ts.set_token(token)
    pro = ts.pro_api()
    chunks, failures = [], []
    for code, label in ETF_POOL.items():
        try:
            raw = pro.fund_daily(ts_code=code, start_date=START, end_date=END,
                fields="ts_code,trade_date,open,high,low,close,pre_close,vol,amount")
            if raw is None or raw.empty:
                failures.append(f"{code}: empty fund_daily response")
                continue
            raw["date"] = pd.to_datetime(raw["trade_date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
            raw["symbol"], raw["name"] = code, label
            for col in ["open", "high", "low", "close", "pre_close", "vol", "amount"]:
                raw[col] = pd.to_numeric(raw[col], errors="coerce")
            factors = fetch_adjustment_factors(pro, code)
            merged = raw.merge(factors, on="date", how="left", validate="one_to_one")
            if merged["adj_factor"].isna().any():
                raise ValueError(f"missing adjustment factors for {int(merged['adj_factor'].isna().sum())} price dates")
            latest_factor = float(merged.sort_values("date")["adj_factor"].iloc[-1])
            if latest_factor <= 0:
                raise ValueError("latest adjustment factor is invalid")
            # Constant latest-factor scaling preserves returns while incorporating fund_adj adjustments.
            for col in ["open", "high", "low", "close", "pre_close"]:
                merged[f"raw_{col}"] = merged[col]
                merged[col] = merged[col] * merged["adj_factor"] / latest_factor
            merged["adjustment_status"] = "fund_adj_scaled_to_latest_factor"
            chunks.append(merged[["date", "symbol", "name", "open", "high", "low", "close",
                                  "pre_close", "vol", "amount", "raw_open", "raw_close",
                                  "adj_factor", "adjustment_status"]])
            print(f"{code}: {len(merged)} prices; adjustment factors verified for all price dates")
        except Exception as exc:
            # Keep logs free of provider response bodies and credentials.
            failures.append(f"{code}: {type(exc).__name__}: {str(exc)[:180]}")
    if failures:
        raise SystemExit("Incomplete ETF price/adjustment dataset; refusing backtest: " + "; ".join(failures))
    if not chunks:
        raise SystemExit("No ETF price data returned")
    panel = pd.concat(chunks, ignore_index=True).drop_duplicates(["date", "symbol"])
    panel = panel.sort_values(["date", "symbol"])
    if panel[["date", "symbol", "open", "close", "adj_factor"]].isna().any().any():
        raise SystemExit("Adjusted prices contain missing required fields")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    panel.to_csv(OUT, index=False)

    calendar_parts = []
    for exchange in ["SSE", "SZSE"]:
        cal_part = pro.trade_cal(exchange=exchange, start_date=START, end_date=END,
                                 fields="exchange,cal_date,is_open")
        if cal_part is None or cal_part.empty:
            raise SystemExit(f"{exchange} trade calendar unavailable; refusing to certify coverage")
        cal_part = cal_part[cal_part["is_open"].astype(str) == "1"].copy()
        cal_part["date"] = pd.to_datetime(cal_part["cal_date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
        calendar_parts.append(cal_part[["date", "exchange", "is_open"]])
    calendar = pd.concat(calendar_parts, ignore_index=True).drop_duplicates(["date", "exchange"])
    calendar[["date", "exchange", "is_open"]].sort_values(["date", "exchange"]).to_csv(CALENDAR_OUT, index=False)
    print(f"Downloaded {len(panel)} adjusted rows, {panel.symbol.nunique()} ETFs, "
          f"{panel.date.min()} to {panel.date.max()}; SSE/SZSE calendar rows={len(calendar)}")

    # Legacy workflow reruns do not include a separate audit step; run it here as well.
    subprocess.run([sys.executable, str(Path(__file__).with_name("audit_data.py"))], check=True)

if __name__ == "__main__":
    main()
