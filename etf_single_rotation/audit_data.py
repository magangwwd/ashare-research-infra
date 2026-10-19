"""Audit ETF coverage, gaps, duplicates, price anomalies, and adjustment-factor matching."""
from pathlib import Path
import json
import pandas as pd

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
SOURCE = DATA_DIR / "prices.csv"
CALENDAR = DATA_DIR / "trade_calendar.csv"
OUT = ROOT / "results"
EXPECTED_ETFS = {"510300.SH", "510500.SH", "159915.SZ", "510050.SH",
                 "510880.SH", "518880.SH", "512100.SH", "512690.SH"}

def main():
    if not SOURCE.exists():
        raise SystemExit("No prices.csv: run data download first.")
    if not CALENDAR.exists():
        raise SystemExit("No trade_calendar.csv: cannot audit exchange sessions.")
    df = pd.read_csv(SOURCE, parse_dates=["date"])
    required = {"date", "symbol", "open", "close", "adj_factor", "adjustment_status", "raw_open", "raw_close"}
    if not required.issubset(df.columns):
        raise SystemExit(f"Missing columns: {sorted(required - set(df.columns))}")
    cal = pd.read_csv(CALENDAR, parse_dates=["date"])
    if cal.empty or "date" not in cal.columns or "exchange" not in cal.columns:
        raise SystemExit("Exchange calendar is empty or malformed.")
    exchange_dates = pd.DatetimeIndex(sorted(cal["date"].dropna().unique()))
    if df.empty:
        raise SystemExit("Empty price panel")
    OUT.mkdir(parents=True, exist_ok=True)
    rows, gaps = [], []
    duplicate_rows = df[df.duplicated(["date", "symbol"], keep=False)]
    for _, dup in duplicate_rows.iterrows():
        gaps.append({"symbol": dup["symbol"], "date": dup["date"].date().isoformat(),
                     "issue": "DUPLICATE_DATE_SYMBOL", "detail": "Duplicate price record"})
    for symbol in sorted(EXPECTED_ETFS | set(df["symbol"].dropna().astype(str))):
        group = df[df["symbol"] == symbol].sort_values("date")
        if group.empty:
            rows.append({"symbol": symbol, "first_date": None, "last_date": None,
                         "observed_sessions": 0, "expected_exchange_sessions": 0,
                         "missing_sessions": None, "missing_dates_sample": "",
                         "duplicate_dates": 0, "valid_price_rows": 0, "nonpositive_price_rows": 0,
                         "abs_adjusted_close_change_over_20pct": 0, "adjustment_factor_matched": False,
                         "status": "MISSING_ETF"})
            continue
        first, last = group.date.min(), group.date.max()
        expected = exchange_dates[(exchange_dates >= first) & (exchange_dates <= last)]
        observed = pd.DatetimeIndex(group.date.dropna().unique())
        missing = expected.difference(observed)
        dup = int(group.duplicated("date").sum())
        valid = group.dropna(subset=["open", "close", "adj_factor", "raw_open", "raw_close"])
        valid = valid[(valid.open > 0) & (valid.close > 0) & (valid.adj_factor > 0)
                      & (valid.raw_open > 0) & (valid.raw_close > 0)]
        nonpositive = int(((group[["open", "close", "adj_factor", "raw_open", "raw_close"]] <= 0)
                           .any(axis=1) | group[["open", "close", "adj_factor", "raw_open", "raw_close"]].isna().any(axis=1)).sum())
        jump = group.close.pct_change(fill_method=None).abs()
        matched = bool(
            group["adjustment_status"].eq("fund_adj_scaled_to_latest_factor").all()
            and group["adj_factor"].notna().all()
            and group["adj_factor"].gt(0).all()
            and group[["raw_open", "raw_close"]].notna().all().all()
        )
        for date in missing:
            gaps.append({"symbol": symbol, "date": date.date().isoformat(),
                         "issue": "MISSING_PRICE_ON_EXCHANGE_OPEN_DAY",
                         "detail": "May be suspension, pre-listing calendar mismatch, or data omission; verify against provider/listing records"})
        for _, row in group.loc[jump > .20].iterrows():
            gaps.append({"symbol": symbol, "date": row["date"].date().isoformat(),
                         "issue": "ADJUSTED_CLOSE_JUMP_OVER_20PCT",
                         "detail": "Review corporate action, factor semantics, and source data"})
        status = "REVIEW_REQUIRED" if len(missing) or dup or nonpositive or not matched else "PRELIMINARY_PASS"
        rows.append({
            "symbol": symbol, "first_date": str(first.date()), "last_date": str(last.date()),
            "observed_sessions": int(len(observed)), "expected_exchange_sessions": int(len(expected)),
            "missing_sessions": int(len(missing)),
            "missing_dates_sample": ";".join(x.strftime("%Y-%m-%d") for x in missing[:20]),
            "duplicate_dates": dup, "valid_price_rows": int(len(valid)),
            "nonpositive_price_rows": nonpositive,
            "abs_adjusted_close_change_over_20pct": int((jump > .20).sum()),
            "adjustment_factor_matched": matched, "status": status,
        })
    result = pd.DataFrame(rows).sort_values("symbol")
    result.to_csv(OUT / "data_quality.csv", index=False)
    gap_df = pd.DataFrame(gaps, columns=["symbol", "date", "issue", "detail"])
    gap_df.to_csv(OUT / "data_quality_gaps.csv", index=False)
    missing_total = int(result["missing_sessions"].fillna(0).sum())
    adjustment_matched = bool(result["adjustment_factor_matched"].all())
    missing_etfs = sorted(set(EXPECTED_ETFS) - set(df["symbol"].dropna().astype(str)))
    summary = {
        "source": str(SOURCE),
        "rows": int(len(df)),
        "symbols": int(df.symbol.nunique()),
        "expected_symbols": sorted(EXPECTED_ETFS),
        "missing_symbols": missing_etfs,
        "first_date": str(df.date.min().date()),
        "last_date": str(df.date.max().date()),
        "calendar_source": "Tushare Pro trade_cal; SSE and SZSE open sessions combined by unique date",
        "missing_etf_session_records": missing_total,
        "gap_issue_rows": int(len(gap_df)),
        "adjustment_factor_status": "MATCHED_TO_PRICE_ROWS_ONLY_TOTAL_RETURN_SEMANTICS_UNVERIFIED" if adjustment_matched else "NOT_MATCHED",
        "calendar_missing_sessions_detected": missing_total > 0,
        "data_quality_file": "data_quality.csv",
        "data_quality_gaps_file": "data_quality_gaps.csv",
        "acceptance": "REVIEW_REQUIRED" if missing_total or missing_etfs or not adjustment_matched or
            result["duplicate_dates"].fillna(0).gt(0).any() or result["nonpositive_price_rows"].fillna(0).gt(0).any()
            else "PRELIMINARY_PASS_NOT_STRATEGY_ACCEPTANCE",
        "note": "Missing rows are not automatically assumed to be data errors or suspensions. Verify each against listing history and provider data. Factor matching alone does not verify distribution reinvestment or total-return semantics."
    }
    (OUT / "data_quality_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(result.to_string(index=False))
    print(gap_df.to_string(index=False))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["acceptance"] == "REVIEW_REQUIRED":
        print("WARNING: data-quality review remains open; strategy metrics are exploratory only.")

if __name__ == "__main__":
    main()
