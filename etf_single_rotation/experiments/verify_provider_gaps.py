"""Provider-level recheck of every currently missing ETF trading date.

Only the *same* Tushare provider is queried on a narrow date range. A missing
quote on retry is NOT proof of an exchange suspension. Results remain unresolved
until an independent exchange notice/provider can establish the true cause.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import tushare as ts

ROOT = Path(__file__).resolve().parent.parent
GAPS = ROOT / "results" / "data_quality_gaps.csv"
OUT = ROOT / "results"
MAX_GAPS_TO_RECHECK = 30


def rows_on_date(raw, date_text):
    if raw is None or raw.empty or "trade_date" not in raw.columns:
        return 0
    dates = pd.to_datetime(raw["trade_date"].astype(str), format="%Y%m%d", errors="coerce")
    return int((dates == pd.Timestamp(date_text)).sum())


def main():
    if not GAPS.exists():
        raise SystemExit("Missing data-quality gap list; do not fabricate recheck records")
    gaps = pd.read_csv(GAPS, dtype=str)
    if "issue" not in gaps.columns:
        raise SystemExit("Gap list lacks issue classification")
    missing = gaps.loc[gaps.issue == "MISSING_PRICE_ON_EXCHANGE_OPEN_DAY"].copy()
    if len(missing) > MAX_GAPS_TO_RECHECK:
        raise SystemExit("Too many gaps for targeted API recheck; batch separately")
    if missing.empty:
        result = {
            "status": "NO_GAPS_TO_RECHECK",
            "gap_count": 0, "independent_exchange_verification": False
        }
        pd.DataFrame(columns=[
            "symbol", "date", "price_records_exact", "price_records_window",
            "factor_records_exact", "provider_access", "classification",
            "explanation"
        ]).to_csv(OUT / "provider_gap_recheck.csv", index=False)
        (OUT / "provider_gap_recheck_summary.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        print("PROVIDER_GAP_RECHECK " + json.dumps(result))
        return
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if not token:
        raise SystemExit("TUSHARE_TOKEN absent; provider gap recheck not possible")
    ts.set_token(token)
    pro = ts.pro_api()
    detail = []
    for item in missing.itertuples(index=False):
        symbol = str(item.symbol)
        d = pd.Timestamp(item.date)
        start = (d - pd.Timedelta(days=7)).strftime("%Y%m%d")
        end = (d + pd.Timedelta(days=7)).strftime("%Y%m%d")
        exact_count = 0
        nearby_count = 0
        factors_exact = 0
        status = "NOT_CHECKED"
        note = ""
        try:
            # A narrow historical window can reveal a provider pagination/
            # omitted-row bug in the long-range download, without guessing.
            prices = pro.fund_daily(
                ts_code=symbol, start_date=start, end_date=end,
                fields="ts_code,trade_date,open,close,vol"
            )
            nearby_count = int(len(prices)) if prices is not None else 0
            exact_count = rows_on_date(prices, d)
            factors = pro.fund_adj(
                ts_code=symbol, start_date=start, end_date=end,
                fields="ts_code,trade_date,adj_factor"
            )
            factors_exact = rows_on_date(factors, d)
            status = "OK"
            if exact_count:
                classification = "RECOVERABLE_QUOTE_MISSING_FROM_LONG_QUERY"
                note = "Retry returned a real quote; verify factor and rebuild raw dataset before using"
            elif factors_exact:
                classification = "NO_QUOTE_WITH_FACTOR_ROW_UNRESOLVED"
                note = "Provider returned adjustment factor but no quote; cannot infer suspension"
            else:
                classification = "NO_QUOTE_ON_RETRY_UNRESOLVED"
                note = "No provider quote in narrow query; an independent exchange notice is still required"
        except Exception as err:
            status = f"ERROR_{type(err).__name__}"
            classification = "PROVIDER_RECHECK_UNAVAILABLE"
            note = "Provider request failed; must not infer missing data cause"
        detail.append({
            "symbol": symbol, "date": d.date().isoformat(),
            "price_records_exact": int(exact_count),
            "price_records_window": int(nearby_count),
            "factor_records_exact": int(factors_exact),
            "provider_access": status, "classification": classification,
            "explanation": note
        })
    frame = pd.DataFrame(detail)
    frame.to_csv(OUT / "provider_gap_recheck.csv", index=False)
    classification_counts = frame.classification.value_counts().to_dict()
    result = {
        "gap_count": int(len(frame)), "classifications": classification_counts,
        "provider": "Tushare Pro fund_daily/fund_adj narrow historical range",
        "status": "REVIEW_REQUIRED",
        "independent_exchange_verification": False,
        "no_auto_fill_or_imputed_trade": True,
        "warning": "Same-provider retry alone cannot distinguish market suspension from a data omission; do not mark missing ETF sessions resolved.",
        "next_step": "Find exchange announcements or independent source for each date, then validate adjusted-close/total-return semantics."
    }
    (OUT / "provider_gap_recheck_summary.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print("PROVIDER_GAP_RECHECK_DETAIL " + frame.to_json(orient="records",force_ascii=False))
    print("PROVIDER_GAP_RECHECK " + json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
