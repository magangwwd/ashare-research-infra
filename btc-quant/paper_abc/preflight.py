"""Pure, fail-closed BTC A/B/C *simulation-only* candidate gate.

This module NEVER sends orders, modifies balances, or writes a ledger.
Only OKX-origin quotes may price candidates. Binance is auxiliary evidence.
An ALLOW_CANDIDATE verdict is NOT a fill or permission for real trading.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import json
import re

VERSION = "btc-abc-preflight-v0.1.0"
MAX_QUOTE_AGE_MS = 60_000
MAX_FUNDING_AGE_MS = 60_000
ACCOUNTS = {"A": "SPOT", "B": "SPOT", "C": "SWAP"}


def _d(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("invalid_decimal") from None
    if not number.is_finite():
        raise ValueError("nonfinite_decimal")
    return number


def _integer(value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("invalid_integer")
    return value


def evaluate(account, state, quote, proposal, now_ms):
    """Return an auditable preflight verdict; fail closed on missing evidence.

    Caller must independently verify the ledger hash, account continuity,
    risk/liquidation engine, and Drive write/readback. Boolean proof fields
    are declarations from those systems, not proofs created by this module.
    """
    reasons = []
    account = str(account)
    expected = ACCOUNTS.get(account)
    if expected is None:
        reasons.append("UNKNOWN_ACCOUNT")
    if not isinstance(state, dict) or not isinstance(quote, dict) or not isinstance(proposal, dict):
        reasons.append("INVALID_PAYLOAD")
        return {"version": VERSION, "account": account, "decision": "BLOCK", "reasons": reasons}

    if state.get("account_id") != account or state.get("schema") != "BTC_ABC_LEDGER_V1":
        reasons.append("ACCOUNT_LEDGER_IDENTITY_UNVERIFIED")
    if state.get("ledger_verified") is not True or state.get("drive_readback_verified") is not True:
        reasons.append("LEDGER_OR_DRIVE_UNVERIFIED")
    sha = state.get("ledger_sha256")
    if not isinstance(sha, str) or re.fullmatch(r"[0-9a-f]{64}", sha) is None:
        reasons.append("LEDGER_HASH_MISSING")
    if proposal.get("idempotency_key") is None or not isinstance(proposal.get("idempotency_key"), str) or not proposal["idempotency_key"].strip():
        reasons.append("IDEMPOTENCY_KEY_MISSING")
    if not isinstance(proposal.get("policy_version"), str) or not proposal["policy_version"] or state.get("policy_version") != proposal.get("policy_version"):
        reasons.append("POLICY_VERSION_UNVERIFIED")
    if proposal.get("cost_model_verified") is not True or proposal.get("risk_engine_verified") is not True:
        reasons.append("COST_OR_RISK_ENGINE_UNVERIFIED")
    if quote.get("source") != "OKX" or quote.get("kind") != expected:
        reasons.append("WRONG_EXECUTION_PRICE_SOURCE")
    instrument = "BTC-USDT-SWAP" if expected == "SWAP" else "BTC-USDT"
    if quote.get("instrument") != instrument:
        reasons.append("WRONG_INSTRUMENT")
    now = 0
    try:
        now = _integer(now_ms)
        quote_ts = _integer(quote.get("ts_ms"))
        if not 0 <= now - quote_ts <= MAX_QUOTE_AGE_MS:
            reasons.append("STALE_OR_FUTURE_QUOTE")
    except ValueError:
        reasons.append("QUOTE_TIME_INVALID")
    try:
        bid, ask = _d(quote.get("bid")), _d(quote.get("ask"))
        if bid <= 0 or ask <= 0 or bid > ask:
            reasons.append("INVALID_SPREAD")
    except ValueError:
        reasons.append("INVALID_SPREAD")
        bid = ask = Decimal(0)
    try:
        notional = _d(proposal.get("notional_usdt"))
        fee_rate = _d(proposal.get("fee_rate"))
        slip_rate = _d(proposal.get("slippage_rate"))
        equity = _d(state.get("equity_usdt"))
        cash = _d(state.get("cash_usdt"))
        btc_qty = _d(state.get("btc_qty"))
        if notional <= 0 or equity <= 0 or cash < 0 or btc_qty < 0:
            reasons.append("INVALID_POSITION_OR_NOTIONAL")
        if not (Decimal(0) <= fee_rate <= Decimal("0.02") and Decimal(0) <= slip_rate <= Decimal("0.02")):
            reasons.append("COST_PARAMETERS_OUT_OF_BOUNDS")
    except ValueError:
        reasons.append("INVALID_FINANCIAL_INPUT")
        notional = fee_rate = slip_rate = equity = cash = btc_qty = Decimal(0)
    action = proposal.get("action")
    if expected == "SPOT":
        if action not in ("BUY", "SELL"):
            reasons.append("SPOT_ACTION_INVALID")
        elif action == "BUY" and notional * (1 + fee_rate + slip_rate) > cash:
            reasons.append("INSUFFICIENT_SPOT_CASH")
        elif action == "SELL" and (bid <= 0 or notional / bid > btc_qty):
            reasons.append("INSUFFICIENT_SPOT_BTC")
        if proposal.get("leverage", 1) != 1:
            reasons.append("SPOT_LEVERAGE_FORBIDDEN")
    elif expected == "SWAP":
        if action not in ("LONG", "SHORT"):
            reasons.append("SWAP_ACTION_INVALID")
        try:
            lev = _d(proposal.get("leverage"))
            if not Decimal(1) <= lev <= Decimal(2):
                reasons.append("LEVERAGE_EXCEEDS_LIMIT")
            elif notional > equity * lev:
                reasons.append("EXCESS_NOTIONAL")
            elif notional / lev + notional * (2 * fee_rate + 2 * slip_rate) > cash:
                reasons.append("INSUFFICIENT_ISOLATED_MARGIN")
            if proposal.get("margin_mode") != "isolated":
                reasons.append("NOT_ISOLATED")
            if proposal.get("liquidation_model_verified") is not True:
                reasons.append("LIQUIDATION_MODEL_UNVERIFIED")
            funding_rate = _d(quote.get("funding_rate"))
            funding_ts = _integer(quote.get("funding_ts_ms"))
            if abs(funding_rate) > Decimal("0.01"):
                reasons.append("FUNDING_RATE_OUT_OF_BOUNDS")
            if not 0 <= now - funding_ts <= MAX_FUNDING_AGE_MS:
                reasons.append("STALE_FUNDING_RATE")
        except ValueError:
            reasons.append("FUNDING_OR_LEVERAGE_INVALID")
            funding_rate = Decimal(0)
    adverse_funding = abs(funding_rate) if expected == "SWAP" else Decimal(0)
    conservative_cost = notional * (2 * fee_rate + 2 * slip_rate + adverse_funding)
    if conservative_cost >= equity:
        reasons.append("COST_EXCEEDS_EQUITY")
    result = {"version": VERSION, "account": account,
              "decision": "BLOCK" if reasons else "ALLOW_CANDIDATE",
              "reasons": sorted(set(reasons)),
              "estimated_roundtrip_cost_usdt": str(conservative_cost),
              "warning": "NOT_A_FILL_OR_REAL_ORDER"}
    result["fingerprint"] = hashlib.sha256(json.dumps(result, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return result
