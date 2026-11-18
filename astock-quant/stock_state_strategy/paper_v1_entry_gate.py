"""Pure, fail-closed entry gate for frozen LOCK_MARKUP_SINGLE_PAPER_V1.

This module is an offline component. Caller must supply verified point-in-time
V1 candidate rows and a verified next-session open bar. It never fetches prices,
places orders or writes production account state. No signal is live authorization.
"""
from __future__ import annotations

import math
from typing import Any

PAPER_ONLY = "PAPER_ONLY_NO_ENTRY_AUTHORIZATION"
BUY_COST = 0.0013
MAX_GAP = 0.03
LOT = 100


def number(row: dict[str, Any], key: str) -> float:
    try:
        value = float(row[key])
    except (TypeError, ValueError, KeyError) as exc:
        raise ValueError(f"MISSING_OR_INVALID_{key}") from exc
    if not math.isfinite(value):
        raise ValueError(f"MISSING_OR_INVALID_{key}")
    return value


def _base(status: str, reason: str, **details: Any) -> dict[str, Any]:
    return {
        "strategy_id": "LOCK_MARKUP_SINGLE_PAPER_V1",
        "authorization": PAPER_ONLY,
        "status": status,
        "reason": reason,
        **details,
    }


def select_close_candidate(
    signal_date: str,
    candidates: list[dict[str, Any]],
    *,
    verified_source: bool,
    flat_account: bool,
) -> dict[str, Any]:
    """T-close: select only the frozen top-ranked qualifying candidate."""
    if not verified_source:
        return _base("BLOCKED", "UNVERIFIED_PIT_SOURCE")
    if not flat_account:
        return _base("NO_ENTRY", "ACCOUNT_NOT_FLAT")
    if not signal_date or len(signal_date) != 10:
        return _base("BLOCKED", "INVALID_SIGNAL_DATE")
    eligible = []
    invalid = []
    for row in candidates:
        sym = row.get("vendor_symbol", "")
        try:
            if row.get("signal_date") != signal_date or row.get("trade_date") != signal_date:
                raise ValueError("SIGNAL_DATE_MISMATCH")
            if not (row.get("defined_state") == "markup"
                    and row.get("pool_status") == "A_STRATEGY_CANDIDATE"
                    and row.get("previous_defined_state") == "lock"
                    and row.get("transition") == "lock_to_markup"):
                raise ValueError("NOT_LOCK_TO_A_MARKUP")
            if not (1 <= number(row, "transition_gap_days") <= 45
                    and number(row, "state_score") >= 65
                    and number(row, "state_margin") >= 10
                    and number(row, "score_coverage") >= 0.8
                    and number(row, "stable_days_5") >= 3
                    and number(row, "confidence") >= 80
                    and number(row, "average_turnover_cny_20") >= 1e8):
                raise ValueError("STATE_GATE_FAILED")
            if row.get("decision") != "SHADOW_SIGNAL_NO_LIVE_AUTHORIZATION":
                raise ValueError("RANKING_NOT_VERIFIED")
            pl, percentile = number(row, "estimated_pl_ratio"), number(row, "net_r_percentile")
            atr, stop = number(row, "atr20_pct"), number(row, "stop_pct")
            if atr <= 0 or not (0.04 <= stop <= 0.08):
                raise ValueError("INVALID_STOP")
            expected_stop = min(0.08, max(0.04, 1.5 * atr))
            if abs(stop - expected_stop) > 1e-6:
                raise ValueError("FROZEN_STOP_MISMATCH")
            if not sym or not row.get("instrument_id"):
                raise ValueError("MISSING_SYMBOL")
            eligible.append(((-pl, -percentile, str(row["instrument_id"])), row))
        except ValueError as exc:
            invalid.append({"symbol": sym, "reason": str(exc)})
    if not eligible:
        return _base("NO_ENTRY", "NO_VERIFIED_QUALIFYING_SIGNAL", signal_date=signal_date,
                     rejected=invalid)
    eligible.sort(key=lambda pair: pair[0])
    selected = eligible[0][1]
    return _base(
        "PENDING_NEXT_OPEN", "FROZEN_CLOSE_GATE_PASSED",
        signal_date=signal_date, symbol=selected["vendor_symbol"],
        instrument_id=selected["instrument_id"], signal_close=number(selected, "close"),
        stop_pct=number(selected, "stop_pct"),
        atr20_pct=number(selected, "atr20_pct"),
        estimated_pl_ratio=number(selected, "estimated_pl_ratio"),
        net_r_percentile=number(selected, "net_r_percentile"),
        rejected=invalid, qualifying_count=len(eligible),
    )


def check_next_open(
    plan: dict[str, Any],
    bar: dict[str, Any],
    account: dict[str, Any],
    *,
    expected_next_session: str,
    verified_session_and_bar: bool,
) -> dict[str, Any]:
    """T+1: determine a paper execution only after a *verified* opening bar.

    The caller must prove expected_next_session with an exchange calendar and
    verify the bar's source. Missing or ambiguous buyability always cancels.
    """
    if plan.get("status") != "PENDING_NEXT_OPEN" or plan.get("authorization") != PAPER_ONLY:
        return _base("CANCELLED", "NO_VALID_PENDING_PLAN")
    details = {"signal_date": plan["signal_date"], "symbol": plan["symbol"]}
    if not verified_session_and_bar or bar.get("verified") is not True:
        return _base("CANCELLED", "UNVERIFIED_NEXT_SESSION_BAR", **details)
    if (not expected_next_session or expected_next_session <= plan["signal_date"]
            or bar.get("trade_date") != expected_next_session
            or bar.get("vendor_symbol") != plan["symbol"]):
        return _base("CANCELLED", "NEXT_TRADING_SESSION_MISMATCH", **details)
    if (bar.get("paused") is not False or bar.get("historical_is_st") is not False
            or bar.get("buyable") is not True or bar.get("one_word_up") is not False):
        return _base("CANCELLED", "STOP_ST_OR_NOT_BUYABLE", **details)
    if account.get("flat") is not True or account.get("pending_other_order") is not False:
        return _base("CANCELLED", "ACCOUNT_NOT_FLAT", **details)
    try:
        open_price = number(bar, "open")
        close = number(plan, "signal_close")
        stop_pct = number(plan, "stop_pct")
        equity = number(account, "equity")
        cash = number(account, "cash")
    except ValueError as exc:
        return _base("CANCELLED", str(exc), **details)
    if min(open_price, close, equity, cash) <= 0:
        return _base("CANCELLED", "INVALID_PRICE_OR_ACCOUNT", **details)
    opening_gap = open_price / close - 1
    if opening_gap > MAX_GAP + 1e-12:
        return _base("CANCELLED", "OPENING_GAP_ABOVE_3_PERCENT", gap_pct=round(100 * opening_gap, 3), **details)
    target_fraction = min(0.15, 0.005 / stop_pct)
    max_notional = min(target_fraction * equity, cash / (1 + BUY_COST))
    shares = int(max_notional // (LOT * open_price)) * LOT
    if shares < LOT:
        return _base("CANCELLED", "LESS_THAN_ONE_BOARD_LOT", **details)
    notional = shares * open_price
    if notional * stop_pct > equity * 0.005 + 1e-8:
        return _base("CANCELLED", "RISK_BUDGET_EXCEEDED", **details)
    return _base("PAPER_BUY_CONFIRMED", "NEXT_OPEN_EXECUTION_GATES_PASSED",
                 **details, entry_date=expected_next_session,
                 entry_price=open_price, shares=shares,
                 buy_cost=round(notional * BUY_COST, 4),
                 position_weight=round(notional / equity, 8),
                 stop_pct=stop_pct,
                 stop_price=round(open_price * (1 - stop_pct), 4),
                 gap_pct=round(100 * opening_gap, 3))
