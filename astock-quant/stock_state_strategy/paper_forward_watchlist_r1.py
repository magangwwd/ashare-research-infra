"""R1 five-stock prospective CLOSE-only screen, no trade and no guarantees.

FROZEN candidate from in-sample 2024..2026-10-09 research. No backdated
prospective signals. Scans five requested stocks; computes breadth from the
WHOLE dated market, not just these five. Data or PIT unknown => no buy claim.
Does not import PR #7/#8, mutate frozen V1, or call any broker.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from gold_pit_risk_guards import GoldPITRisk
from research_mainboard_daily_pit_v0 import history_features

FIRST_UNSEEN = pd.Timestamp("2026-10-12")
WATCHLIST = {
    "600351.SH": "亚宝药业",
    "002107.SZ": "沃华医药",
    "600641.SH": "证券名称待当日核实",
    "002568.SZ": "百润股份",
    "002296.SZ": "辉煌科技",
}
POLICY = {
    "id": "R1_PULLBACK_BREADTH50_WATCH5_PAPER",
    "first_unseen_date": "2026-10-12",
    "pattern": "PULLBACK",
    "ma20_above_minimum": 0.50,
    "breadth_minimum_valid": 500,
    "min_stop_fraction": 0.015,
    "max_stop_fraction": 0.04,
    "slippage_one_side": 0.002,
    "max_paid_gap_over_prior_adjusted_close": 0.01,
    "max_account_planned_risk": 0.0025,
    "max_single_stock_weight": 0.10,
    "max_open_positions": 5,
    "buy_lot": 100,
    "tick_cny": 0.01,
    "assumed_broker_minimum_commission_cny": 5,
}
COLUMNS = {
    "daily_prices": ["vendor_symbol", "trade_date", "open", "high", "low",
                     "close", "volume_shares", "turnover_cny", "is_quality_valid"],
    "adjustment_factors": ["vendor_symbol", "trade_date", "adjustment_factor"],
}


def validate_manifest(meta: dict, day: pd.Timestamp) -> None:
    day = pd.Timestamp(day).normalize()
    if day < FIRST_UNSEEN:
        raise RuntimeError("Previously researched data must not be labeled forward")
    observed = pd.to_datetime(meta.get("as_of_date"), errors="coerce")
    if pd.isna(observed) or observed.normalize() != day:
        raise RuntimeError("Manifest as_of_date differs from requested snapshot")
    checks = meta.get("validation", {})
    if checks.get("primary_keys_unique") is not True or checks.get("ohlc_valid") is not True:
        raise RuntimeError("Gold is not validated for primary keys and OHLC")
    # The screen may run at night after source publication but never for a
    # future session not yet closed at 15:00 Asia/Shanghai.
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    if day.date() > now.date() or (
            day.date() == now.date() and (now.hour, now.minute) < (15, 30)):
        raise RuntimeError("Requested Gold close is in the future")


def read_market(root: Path, day: pd.Timestamp) -> pd.DataFrame:
    result = {}
    years = (int(day.year) - 1, int(day.year))
    for name, cols in COLUMNS.items():
        paths = [
            file for year in years
            for file in sorted((root / name / f"year={year}").glob("month=*/data.parquet"))
        ]
        if not paths:
            raise RuntimeError("Missing Gold parquet dataset: " + name)
        result[name] = pd.concat(
            (pd.read_parquet(file, columns=cols) for file in paths), ignore_index=True
        )
    panel = result["daily_prices"].merge(
        result["adjustment_factors"],
        on=["vendor_symbol", "trade_date"], how="left", validate="one_to_one")
    panel["trade_date"] = pd.to_datetime(panel["trade_date"]).dt.normalize()
    panel = panel.loc[panel.trade_date.le(day)].copy()
    if panel.empty or panel.trade_date.max() != day:
        raise RuntimeError("Gold lacks the exact requested trade date")
    if panel["adjustment_factor"].isna().any():
        raise RuntimeError("Missing adjustment factor in historical panel")
    for col in ("open", "high", "low", "close", "volume_shares",
                "turnover_cny", "adjustment_factor"):
        panel[col] = pd.to_numeric(panel[col], errors="coerce")
    valid = (
        panel["is_quality_valid"].eq(True)
        & panel[["open", "high", "low", "close", "adjustment_factor"]].gt(0).all(axis=1)
        & panel[["open", "high", "low", "close", "adjustment_factor"]].apply(
            np.isfinite).all(axis=1)
        & panel.volume_shares.gt(0) & panel.turnover_cny.ge(0)
    )
    invalid_today = panel.trade_date.eq(day) & ~valid
    if invalid_today.any():
        # Complete-current-day integrity is necessary for breadth; do not
        # silently inflate the ratio by dropping today's losing names.
        raise RuntimeError("Invalid date-level price records in Gold: fail closed")
    panel = panel.loc[valid].copy()
    if panel.loc[panel.trade_date.eq(day), "vendor_symbol"].nunique() < 500:
        raise RuntimeError("Too few dated symbols for robust breadth")
    for column in ("open", "high", "low", "close"):
        panel["p_" + column] = panel[column] * panel["adjustment_factor"]
    return panel.sort_values(["vendor_symbol", "trade_date"], kind="stable")


def market_breadth(panel: pd.DataFrame, day: pd.Timestamp) -> dict:
    """Strict current-day >MA20 ratio among 20-bar-valid symbols.

    This is a *historically retained* universe, not an audit of delistings.
    Each stock's rolling mean only uses observations through this close.
    """
    dated = panel.sort_values(["vendor_symbol", "trade_date"], kind="stable").copy()
    dated["ma20"] = dated.groupby("vendor_symbol", sort=False)["p_close"].transform(
        lambda series: series.rolling(20, min_periods=20).mean())
    last = dated.loc[dated.trade_date.eq(day) & dated.ma20.notna()].copy()
    n = int(last.vendor_symbol.nunique())
    if n < POLICY["breadth_minimum_valid"]:
        raise RuntimeError("Too few 20-session histories to measure market breadth")
    above = int(last.p_close.gt(last.ma20).sum())
    ratio = above / n
    return {
        "eligible_symbols": n, "above_ma20": above, "fraction": round(ratio, 8),
        "passed": ratio >= POLICY["ma20_above_minimum"],
    }


def screen_five(panel: pd.DataFrame, guard: GoldPITRisk, day: pd.Timestamp) -> dict:
    market = market_breadth(panel, day)
    checked = []
    candidates = []
    for sym, label in WATCHLIST.items():
        row = {"symbol": sym, "display_label_unverified": label,
               "signal_date": str(day.date()), "candidate": False}
        history = panel.loc[panel.vendor_symbol.eq(sym)]
        if history.empty or history.trade_date.iloc[-1] != day:
            row.update(status="DATA_MISSING_FAIL_CLOSED")
            checked.append(row)
            continue
        data = history_features(history)
        r = data.iloc[-1]
        row["signal_close_raw"] = round(float(r["close"]), 4)
        row["signal_close_adjusted"] = round(float(r["p_close"]), 6)
        row["pattern"] = str(r["signal"])
        if not market["passed"]:
            row["status"] = "BREADTH_NOT_PASSED"
        elif r["signal"] != "PULLBACK":
            row["status"] = "NO_R1_PULLBACK"
        else:
            # Risk check on the observed SIGNAL day. Tomorrow's upper limit,
            # gap, ST, suspension and order book are still UNKNOWN.
            raw = {k: float(r[k]) for k in ("open", "high", "low", "close")}
            # Never substitute a previous day's risk record.
            reject = guard.entry_reason(sym, day, raw)
            if reject:
                row["status"] = "PIT_" + reject
            elif not (math.isfinite(float(r["atr14"])) and float(r["atr14"]) > 0):
                row["status"] = "ATR_UNKNOWN"
            else:
                approx_atr_frac = (2 * float(r["atr14"]) /
                                   (float(r["p_close"]) * 1.002))
                row["estimated_2atr_fraction"] = round(approx_atr_frac, 7)
                if not POLICY["min_stop_fraction"] <= approx_atr_frac <= POLICY["max_stop_fraction"]:
                    row["status"] = "ATR_OUTSIDE_R0_LIMITS"
                else:
                    row["status"] = "PAPER_CLOSE_SIGNAL_WAIT_NEXT_OPEN"
                    row["candidate"] = True
                    row["strength"] = round(float(r["signal_strength"]), 5)
                    row["indicative_raw_chase_cap"] = round(float(r["close"]) * 1.01, 4)
                    row["next_open_verified"] = False
                    row["execution_authorized"] = False
                    row["next_open_checks"] = [
                        "fresh previous-day/current ex-rights factors and actual next-open",
                        "paid price including slippage and tick <= +1% over same-basis adjusted signal close",
                        "PIT new-session ST, suspension, exact exchange upper/lower limits",
                        "100-share lots, <=10% weight, <=0.25% equity risk",
                        "order book liquidity; stops are not guaranteed",
                    ]
                    candidates.append(dict(row))
        checked.append(row)
    candidates.sort(key=lambda item: (-item["strength"], item["symbol"]))
    return {"breadth": market, "checked": checked, "candidates": candidates}


def make_report(manifest: dict, manifest_bytes: bytes, panel: pd.DataFrame,
                guard: GoldPITRisk, day: pd.Timestamp) -> dict:
    validate_manifest(manifest, day)
    if not any(x[1] == day for x in guard.limits):
        raise RuntimeError("New session has no historical PIT limits at all")
    result = screen_five(panel, guard, day)
    policy_sha = hashlib.sha256(json.dumps(
        POLICY, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        "status": "PAPER_ONLY_R1_FIVE_NO_ENTRY_AUTHORIZATION",
        "snapshot_date": str(day.date()),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "gold_as_of": manifest["as_of_date"],
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "policy": POLICY,
        "policy_sha256": policy_sha,
        "scope": list(WATCHLIST),
        "source_rows_today": int(panel.trade_date.eq(day).sum()),
        "market_breadth": result["breadth"],
        "checked": result["checked"],
        "candidate_count": len(result["candidates"]),
        "candidates": result["candidates"],
        "disclosure": [
            "R1 was selected after repeated inspection of 2024-2026 data; no independent profit validation.",
            "Five tracked stocks only, breadth still requires full historically retained main board.",
            "Names are unverified display labels; instrument ID/symbol is authoritative.",
            "Signal-day restrictions checked; tomorrow's open/limit/status must be checked anew.",
            "No broker, no market order, no guaranteed execution or stop, no live buy instruction.",
            "Corporate actions are adjusted-return proxies without audited share/dividend ledger.",
        ],
    }


def run(manifest: Path, market_root: Path, risk_root: Path,
        as_of: str, output: Path) -> Path:
    day = pd.Timestamp(as_of).normalize()
    payload = manifest.read_bytes()
    meta = json.loads(payload)
    validate_manifest(meta, day)
    panel = read_market(market_root, day)
    risk = GoldPITRisk(risk_root)
    report = make_report(meta, payload, panel, risk, day)
    dest = output / str(day.date()) / "r1_watchlist_5.json"
    if dest.exists():
        raise RuntimeError("This forward observation already exists, never overwrite")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print("R1_WATCHLIST_PAPER="+json.dumps({
        "as_of": report["snapshot_date"], "breadth": report["market_breadth"],
        "candidate_count": report["candidate_count"], "state": report["status"],
    }, ensure_ascii=False), flush=True)
    return dest


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--market-root", type=Path, required=True)
    ap.add_argument("--risk-root", type=Path, required=True)
    ap.add_argument("--as-of", required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    run(args.manifest, args.market_root, args.risk_root, args.as_of, args.output)
