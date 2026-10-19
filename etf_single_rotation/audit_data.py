"""Audit ETF price coverage against the SSE calendar and verify adjustment-factor fields."""
from pathlib import Path
import json
import pandas as pd

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
SOURCE = DATA_DIR / "prices.csv"
CALENDAR = DATA_DIR / "trade_calendar.csv"
OUT = ROOT / "results"

def main():
    if not SOURCE.exists():
        raise SystemExit("No prices.csv: run data download first.")
    if not CALENDAR.exists():
        raise SystemExit("No trade_calendar.csv: cannot audit coverage against actual exchange sessions.")
    df = pd.read_csv(SOURCE, parse_dates=["date"])
    required = {"date", "symbol", "open", "close", "adj_factor", "adjustment_status"}
    if not required.issubset(df.columns):
        raise SystemExit(f"Missing columns: {sorted(required - set(df.columns))}")
    cal = pd.read_csv(CALENDAR, parse_dates=["date"])
    if cal.empty or "date" not in cal.columns:
        raise SystemExit("SSE trade calendar is empty or malformed.")
    exchange_dates = pd.DatetimeIndex(sorted(cal["date"].dropna().unique()))
    if df.empty:
        raise SystemExit("Empty price panel")
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for symbol, group in df.groupby("symbol"):
        group = group.sort_values("date")
        first, last = group.date.min(), group.date.max()
        expected = exchange_dates[(exchange_dates >= first) & (exchange_dates <= last)]
        observed = pd.DatetimeIndex(group.date.dropna().unique())
        missing = expected.difference(observed)
        dup = int(group.duplicated("date").sum())
        valid = group.dropna(subset=["open", "close", "adj_factor"])
        valid = valid[(valid.open > 0) & (valid.close > 0) & (valid.adj_factor > 0)]
        nonpositive = int(((group.open <= 0) | (group.close <= 0) | (group.adj_factor <= 0)).fillna(True).sum())
        jump = group.close.pct_change(fill_method=None).abs()
        adjustment_ok = (
            group["adjustment_status"].eq("fund_adj_scaled_to_latest_factor").all()
            and group["adj_factor"].notna().all()
            and group["adj_factor"].gt(0).all()
            and {"raw_open", "raw_close"}.issubset(group.columns)
        )
        rows.append({
            "symbol": symbol,
            "first_date": str(first.date()),
            "last_date": str(last.date()),
            "observed_sessions": int(len(observed)),
            "expected_sse_sessions": int(len(expected)),
            "missing_sessions": int(len(missing)),
            "missing_dates_sample": ";".join(x.strftime("%Y-%m-%d") for x in missing[:10]),
            "missing_rate": float(len(missing) / len(expected)) if len(expected) else None,
            "duplicate_dates": dup,
            "valid_price_rows": int(len(valid)),
            "nonpositive_price_rows": nonpositive,
            "abs_adjusted_close_change_over_20pct": int((jump > .20).sum()),
            "adjustment_factor_verified": bool(adjustment_ok),
        })
    result = pd.DataFrame(rows).sort_values("symbol")
    result.to_csv(OUT / "data_quality.csv", index=False)
    adjustment_ok = bool(result["adjustment_factor_verified"].all())
    calendar_issue = bool((result["missing_sessions"] > 0).any())
    summary = {
        "source": str(SOURCE),
        "rows": int(len(df)),
        "symbols": int(df.symbol.nunique()),
        "first_date": str(df.date.min().date()),
        "last_date": str(df.date.max().date()),
        "calendar_source": "Tushare Pro trade_cal, SSE open sessions",
        "adjustment_status": "fund_adj factors present and joined to all price rows" if adjustment_ok else "NOT_VERIFIED",
        "calendar_missing_sessions_detected": calendar_issue,
        "data_quality_file": "data_quality.csv",
        "acceptance": "REVIEW_REQUIRED" if calendar_issue or not adjustment_ok or
            result["duplicate_dates"].gt(0).any() or result["nonpositive_price_rows"].gt(0).any()
            else "PRELIMINARY_PASS_NOT_STRATEGY_ACCEPTANCE",
        "note": "A data-quality pass does not establish strategy profitability; validate total-return treatment and out-of-sample performance separately."
    }
    (OUT / "data_quality_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(result.to_string(index=False))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["acceptance"] == "REVIEW_REQUIRED":
        print("WARNING: data coverage or adjustment issues detected; do not accept strategy results.")
if __name__ == "__main__":
    main()
