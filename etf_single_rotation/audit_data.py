"""Audit ETF price coverage and consistency; no performance claims."""
from pathlib import Path
import json
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "data" / "prices.csv"
OUT = ROOT / "results"
def main():
    if not SOURCE.exists():
        raise SystemExit("No prices.csv: run data download first.")
    df = pd.read_csv(SOURCE, parse_dates=["date"])
    required = {"date", "symbol", "open", "close"}
    if not required.issubset(df.columns):
        raise SystemExit(f"Missing columns: {required - set(df.columns)}")
    if df.empty:
        raise SystemExit("Empty price panel")
    OUT.mkdir(parents=True, exist_ok=True)
    cal = pd.DatetimeIndex(sorted(df["date"].dropna().unique()))
    rows = []
    for symbol, group in df.groupby("symbol"):
        group = group.sort_values("date")
        first, last = group.date.min(), group.date.max()
        expected = cal[(cal >= first) & (cal <= last)]
        valid = group.dropna(subset=["open", "close"])
        valid = valid[(valid.open > 0) & (valid.close > 0)]
        observed = pd.DatetimeIndex(group.date.unique())
        missing = expected.difference(observed)
        dup = int(group.duplicated("date").sum())
        nonpositive = int(((group.open <= 0) | (group.close <= 0)).sum())
        jump = group.close.pct_change(fill_method=None).abs()
        rows.append(dict(symbol=symbol, first_date=str(first.date()), last_date=str(last.date()),
            observed_sessions=int(len(observed)), expected_union_sessions=int(len(expected)),
            missing_sessions=int(len(missing)), missing_rate=float(len(missing)/len(expected)) if len(expected) else None,
            duplicate_dates=dup, valid_price_rows=int(len(valid)), nonpositive_price_rows=nonpositive,
            abs_close_change_over_20pct=int((jump > .20).sum()),
            adjusted_for_distributions="NOT_VERIFIED"))
    result = pd.DataFrame(rows).sort_values("symbol")
    result.to_csv(OUT / "data_quality.csv", index=False)
    summary = {"source": str(SOURCE), "rows": int(len(df)), "symbols": int(df.symbol.nunique()),
        "first_date": str(df.date.min().date()), "last_date": str(df.date.max().date()),
        "coverage_calendar": "union of observed ETF dates (proxy, not exchange calendar)",
        "adjustment_status": "unverified; dividend/split adjusted total-return series required before strategy acceptance",
        "quality_file": "data_quality.csv"}
    (OUT / "data_quality_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(result.to_string(index=False))
    print(json.dumps(summary, indent=2))
    if (result.missing_sessions.gt(0) | result.duplicate_dates.gt(0) | result.nonpositive_price_rows.gt(0)).any():
        print("WARNING: price coverage issues detected; inspect before accepting backtest.")
if __name__ == "__main__":
    main()
