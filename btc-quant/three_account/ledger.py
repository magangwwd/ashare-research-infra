"""Fail-closed, offline-only three-account simulation ledger core.

No exchange client, API credentials, network access, or real order submission.
Decisions and quotes must be independently supplied and verified by caller.
"""
from decimal import Decimal, InvalidOperation
import hashlib
import json

D = Decimal
ACCOUNTS = {"A": "spot_rule", "B": "spot_gpt", "C": "perp_gpt"}

def positive(value, name, allow_zero=False):
    try:
        x = D(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"invalid {name}")
    if not x.is_finite() or (x < 0 if allow_zero else x <= 0):
        raise ValueError(f"invalid {name}")
    return x

def initial_state():
    return {"version": 1, "accounts": {
        k: {"cash": "1000", "btc": "0", "position": "0", "entry": "0",
            "realized_pnl": "0", "fees": "0", "funding": "0"}
        for k in ACCOUNTS}, "events": [], "last_event_hash": None}

def validate_state(state):
    if set(state.get("accounts", {})) != set(ACCOUNTS):
        raise ValueError("missing or unexpected account")
    if not isinstance(state.get("events"), list):
        raise ValueError("invalid event history")
    for k, a in state["accounts"].items():
        for field in ("cash", "btc", "position", "entry", "realized_pnl", "fees", "funding"):
            positive(a[field], field, allow_zero=True) if field not in ("realized_pnl", "funding", "position") else finite(a[field])
        if k != "C" and (D(a["position"]) != 0 or D(a["btc"]) < 0):
            raise ValueError("spot account position invalid")
        if k == "C" and D(a["btc"]) != 0:
            raise ValueError("perp must not hold spot BTC")
    return True

def finite(value):
    try:
        v = D(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("invalid number")
    if not v.is_finite():
        raise ValueError("nonfinite number")
    return v

def apply(state, account, decision, quote, risk):
    """Return a new state; never persist automatically. Caller must verify provenance."""
    state = json.loads(json.dumps(state))
    validate_state(state)
    if account not in ACCOUNTS:
        raise ValueError("unknown account")
    if decision.get("action") not in ("BUY", "SELL", "HOLD"):
        raise ValueError("unknown action")
    if not quote.get("verified") or quote.get("venue") != "OKX":
        raise ValueError("unverified OKX quote")
    if quote.get("instrument") != ("BTC-USDT-SWAP" if account == "C" else "BTC-USDT"):
        raise ValueError("instrument mismatch")
    if not quote.get("timestamp_utc") or not quote.get("source_hash"):
        raise ValueError("missing quote provenance")
    if risk.get("approved") is not True or risk.get("stale") is not False:
        raise ValueError("risk gate closed")
    if not risk.get("independent_validator"):
        raise ValueError("independent risk validator required")
    px = positive(quote["price"], "price")
    fee = positive(risk["fee_rate"], "fee", True)
    slip = positive(risk["slippage_rate"], "slippage", True)
    if fee >= 1 or slip >= 1:
        raise ValueError("invalid costs")
    action = decision["action"]
    a = state["accounts"][account]
    if action == "HOLD":
        return state
    qty = positive(decision["quantity_btc"], "quantity")
    if account != "C":
        if action == "BUY":
            total = qty * px * (1 + slip) * (1 + fee)
            if total > D(a["cash"]):
                raise ValueError("insufficient spot cash")
            a["cash"] = str(D(a["cash"]) - total)
            a["btc"] = str(D(a["btc"]) + qty)
        else:
            if qty > D(a["btc"]):
                raise ValueError("insufficient BTC")
            proceeds = qty * px * (1 - slip) * (1 - fee)
            a["cash"] = str(D(a["cash"]) + proceeds)
            a["btc"] = str(D(a["btc"]) - qty)
    else:
        leverage = positive(risk.get("leverage"), "leverage")
        if leverage > 2 or risk.get("margin_mode") != "isolated":
            raise ValueError("perpetual risk limit")
        signed = qty if action == "BUY" else -qty
        old = D(a["position"])
        new = old + signed
        if old and new and old * new < 0:
            raise ValueError("one-step reversal prohibited")
        if abs(new) * px > D("2") * (D(a["cash"]) + abs(old) * D(a["entry"])):
            raise ValueError("leverage cap exceeded")
        charge = qty * px * (fee + slip)
        if charge > D(a["cash"]):
            raise ValueError("insufficient collateral")
        closed = min(abs(old), qty) if old * signed < 0 else D(0)
        pnl = closed * (px - D(a["entry"])) * (1 if old > 0 else -1)
        cash = D(a["cash"]) + pnl - charge
        if cash < 0:
            raise ValueError("negative collateral")
        a["cash"] = str(cash)
        a["realized_pnl"] = str(D(a["realized_pnl"]) + pnl)
        a["position"] = str(new)
        a["entry"] = str(px if old == 0 or old * signed > 0 else (D(0) if new == 0 else D(a["entry"])))
    a["fees"] = str(D(a["fees"]) + qty * px * fee)
    event = {"account": account, "action": action, "qty": str(qty),
             "quote": quote, "decision_reason": decision.get("reason", ""),
             "risk_validator": risk["independent_validator"],
             "previous_hash": state["last_event_hash"]}
    digest = hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()
    event["hash"] = digest
    state["events"].append(event)
    state["last_event_hash"] = digest
    validate_state(state)
    return state
