"""Immutable *prospective* candidate shortlist for studied R1 pullback+breadth.

The R1 rule was selected from already examined historical 2024-2026-10-09
data. FIRST FUTURE SESSION >=2026-10-12. This records CLOSE-only candidates,
never an actual or authorized order; next open/limits/corporate changes unknown.
R0, frozen V1, and all live monitor/trading flows remain unmodified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import numpy as np

import paper_forward_r0 as forward_r0
import research_mainboard_daily_pit_v0 as base
from gold_pit_risk_guards import GoldPITRisk
from research_entry_quality_v1 import point_in_time_breadth

POLICY_NAME = "R1_PULLBACK_BREADTH_50_STRICT20BP"
LOCK_DATE = "2026-10-11"
UNSEEN_START = pd.Timestamp("2026-10-12")
POLICY = {
    "signal": "PULLBACK",
    "breadth_ma_sessions": 20,
    "breadth_min_valid_symbols": 500,
    "breadth_min_fraction": .50,
    "atr_sessions": 14,
    "stop_distance_atr": 2,
    "target_distance_atr": 4,
    "max_stop_fraction": .04,
    "min_stop_fraction": .015,
    "max_execution_gap_up_fraction": .01,
    "max_equity_risk_per_trade": .0025,
    "max_position_weight": .10,
    "max_concurrent_positions": 5,
    "max_holding_sessions": 20,
    "slippage_per_side": .002,
    "buy_lot_shares": 100,
    "broker_min_commission_cny_assumed": 5.,
    "price_tick_cny": .01,
}


def validate_forward_manifest(manifest: dict, day: pd.Timestamp, now: pd.Timestamp):
    """Do not retrospectively label history as a new unseen observation."""
    day = pd.Timestamp(day).normalize()
    now = pd.Timestamp(now).normalize()
    if day < UNSEEN_START:
        raise RuntimeError("R1 prospective samples cannot begin before 2026-10-12")
    if day > now:
        raise RuntimeError("R1 cannot record a future trading close not yet observed")
    forward_r0.verified_as_of(manifest, day)


def make_snapshot(panel: pd.DataFrame, guard: GoldPITRisk, day: pd.Timestamp):
    day = pd.Timestamp(day).normalize()
    if panel.empty or panel["trade_date"].max() != day:
        raise RuntimeError("Latest validated market day differs from requested close")
    px = {sym: frame.set_index("trade_date")[["p_close"]].sort_index()
          for sym, frame in panel.groupby("vendor_symbol", sort=True)}
    breadth = point_in_time_breadth(px)
    point = breadth.get(day)
    if point is None:
        raise RuntimeError("Not enough historically observed names for R1 breadth")
    is_broad = point["fraction"] >= POLICY["breadth_min_fraction"]
    candidates = []
    signals_seen = 0
    if not is_broad:
        return {
            "valid_symbols": point["valid"], "above_ma20": point["above"],
            "fraction": point["fraction"], "market_breadth_pass": False,
            "raw_pullback_patterns": 0, "candidates": [],
        }
    for sym, history in panel.groupby("vendor_symbol", sort=True):
        if history["trade_date"].iloc[-1] != day:
            continue
        feat = base.history_features(history)
        last = feat.iloc[-1]
        if last["signal"] != "PULLBACK":
            continue
        signals_seen += 1
        raw = {k: float(last[k]) for k in ("open", "high", "low", "close")}
        reason = guard.entry_reason(str(sym), day, {"open": raw["close"]})
        if reason is not None:
            continue
        if not np.isfinite(last["atr14"]) or last["atr14"] <= 0 or last["p_close"] <= 0:
            continue
        # 20bp entry-slippage stress is PRELIMINARY. Exact next-day factor,
        # exchange limit, tick-rounded price and resulting ATR need recheck.
        estimated_frac = float(2 * last["atr14"] / (last["p_close"] * (1 + .002)))
        if not POLICY["min_stop_fraction"] <= estimated_frac <= POLICY["max_stop_fraction"]:
            continue
        candidates.append({
            "symbol": str(sym),
            "signal_date": str(day.date()),
            "pattern": "PULLBACK",
            "signal_strength": round(float(last["signal_strength"]), 5),
            "raw_signal_close": round(raw["close"], 3),
            "preliminary_raw_close_plus_1pct": round(raw["close"] * 1.01, 3),
            "preliminary_stop_risk_fraction": round(estimated_frac, 6),
            "entry_authorized": False,
            "next_open_status": "UNKNOWN_PENDING_NEW_TRADING_SESSION",
            "mandatory_next_open_checks": [
                "PIT ST/status and suspension, price limits and valid data",
                "tick-rounded paid open including 20bp stress <=1% over signal adjusted close",
                "100-share lots, existing cash, at most 0.25% planned account risk",
                "actual ex-rights/dividend factor and corporate disclosures",
            ],
        })
    candidates.sort(key=lambda x: (-x["signal_strength"], x["symbol"]))
    return {
        "valid_symbols": point["valid"],
        "above_ma20": point["above"],
        "fraction": point["fraction"],
        "market_breadth_pass": True,
        "raw_pullback_patterns": signals_seen,
        "candidates": candidates,
    }


def output_record(manifest: dict, source_bytes: bytes, panel, guard, day):
    data = make_snapshot(panel, guard, day)
    rules_json = json.dumps(POLICY, sort_keys=True, separators=(",", ":")).encode()
    return {
        "status": "CLOSE_ONLY_UNEXECUTED_R1_CANDIDATE_NO_BUY_AUTHORIZATION",
        "policy_name": POLICY_NAME,
        "policy_locked_on": LOCK_DATE,
        "policy_sha256": hashlib.sha256(rules_json).hexdigest(),
        "policy": POLICY,
        "date": str(day.date()),
        "first_unseen_date": str(UNSEEN_START.date()),
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "gold_manifest_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "gold_manifest_as_of": manifest["as_of_date"],
        "source_rows_today": int(panel["trade_date"].eq(day).sum()),
        "breadth": {
            "eligible_symbols": data["valid_symbols"],
            "above_ma20": data["above_ma20"],
            "fraction": data["fraction"],
            "passed": data["market_breadth_pass"],
        },
        "raw_pullback_patterns": data["raw_pullback_patterns"],
        "candidate_count": len(data["candidates"]),
        "candidates": data["candidates"],
        "limitations": [
            "Selected after extensive review of 2024-2026 historical results; strong in-sample selection bias.",
            "None of these are buy orders or confirmed T+1 opens; no broker connections.",
            "Cannot call a pattern reliable until genuinely subsequent observed trades show stability.",
            "Source adjusted factors approximate return and need actual dividend/share actions for audited performance.",
            "Missing/ambiguous risk data rejects candidates, not interpreted as safe.",
        ],
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", required=True, type=Path)
    p.add_argument("--market-root", required=True, type=Path)
    p.add_argument("--risk-root", required=True, type=Path)
    p.add_argument("--as-of", required=True)
    p.add_argument("--output", required=True, type=Path)
    a = p.parse_args()
    day = pd.Timestamp(a.as_of).normalize()
    manifest_bytes = a.manifest.read_bytes()
    manifest = json.loads(manifest_bytes)
    validate_forward_manifest(manifest, day, pd.Timestamp.now(tz="UTC").tz_localize(None))
    panel = forward_r0.recent_panel(a.market_root, day)
    guard = GoldPITRisk(a.risk_root)
    if day not in {date for _, date in guard.limits}:
        raise RuntimeError("No dated PIT upper/lower limits for requested close")
    record = output_record(manifest, manifest_bytes, panel, guard, day)
    a.output.mkdir(parents=True, exist_ok=True)
    outfile = a.output / f"r1_unexecuted_{day.date()}.json"
    if outfile.exists():
        raise RuntimeError("Immutable R1 prospective record already exists")
    outfile.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    print("R1_FORWARD_PAPER="+json.dumps({
        "as_of": record["date"], "breadth": record["breadth"],
        "candidate_count": record["candidate_count"], "status": record["status"]
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
