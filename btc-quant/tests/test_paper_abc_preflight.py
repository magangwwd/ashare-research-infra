"""Offline no-network tests; never send an exchange order."""
import importlib.util
from pathlib import Path
import unittest

MODULE = Path(__file__).resolve().parents[1] / "paper_abc" / "preflight.py"
spec = importlib.util.spec_from_file_location("btc_abc_preflight", MODULE)
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)
NOW = 1793660000000


def fixtures(account="B"):
    kind = "SWAP" if account == "C" else "SPOT"
    state = dict(account_id=account, schema="BTC_ABC_LEDGER_V1", ledger_verified=True,
                 drive_readback_verified=True, ledger_sha256="a"*64, equity_usdt="1000",
                 cash_usdt="1000", btc_qty="0.01", policy_version="frozen-v1")
    quote = dict(source="OKX", kind=kind, instrument="BTC-USDT-SWAP" if kind == "SWAP" else "BTC-USDT",
                 ts_ms=NOW-5000, bid="76000", ask="76001", funding_rate="0.0001", funding_ts_ms=NOW-5000)
    proposal = dict(action="LONG" if account == "C" else "BUY", notional_usdt="500",
                    fee_rate="0.001", slippage_rate="0.0005", leverage=2 if account == "C" else 1,
                    margin_mode="isolated", liquidation_model_verified=True,
                    idempotency_key="run-1-B-1", policy_version="frozen-v1",
                    risk_engine_verified=True, cost_model_verified=True)
    return state, quote, proposal


class TestPreflight(unittest.TestCase):
    def verdict(self, account="B", mutate=None):
        state, quote, proposal = fixtures(account)
        if mutate:
            mutate(state, quote, proposal)
        return preflight.evaluate(account, state, quote, proposal, NOW)

    def test_valid_spot_candidate_only(self):
        r=self.verdict(); self.assertEqual(r["decision"], "ALLOW_CANDIDATE")
        self.assertEqual(r["estimated_roundtrip_cost_usdt"], "1.5000")
    def test_valid_swap_candidate_only(self):
        r=self.verdict("C"); self.assertEqual(r["decision"], "ALLOW_CANDIDATE")
        self.assertEqual(r["estimated_roundtrip_cost_usdt"], "1.5500")
    def test_missing_ledger_fail_closed(self):
        self.assertIn("LEDGER_OR_DRIVE_UNVERIFIED",self.verdict(mutate=lambda s,q,p:s.update(ledger_verified=False))["reasons"])
    def test_binance_cannot_price_okx(self):
        self.assertIn("WRONG_EXECUTION_PRICE_SOURCE",self.verdict(mutate=lambda s,q,p:q.update(source="BINANCE"))["reasons"])
    def test_stale_quote(self):
        self.assertIn("STALE_OR_FUTURE_QUOTE",self.verdict(mutate=lambda s,q,p:q.update(ts_ms=NOW-70000))["reasons"])
    def test_future_quote(self):
        self.assertIn("STALE_OR_FUTURE_QUOTE",self.verdict(mutate=lambda s,q,p:q.update(ts_ms=NOW+3000))["reasons"])
    def test_spot_no_short_or_leverage(self):
        r=self.verdict(mutate=lambda s,q,p:p.update(action="SHORT",leverage=2))
        self.assertIn("SPOT_ACTION_INVALID",r["reasons"])
        self.assertIn("SPOT_LEVERAGE_FORBIDDEN",r["reasons"])
    def test_spot_buy_cannot_overspend(self):
        self.assertIn("INSUFFICIENT_SPOT_CASH",self.verdict(mutate=lambda s,q,p:p.update(notional_usdt="1000"))["reasons"])
    def test_swap_isolated_and_leverage(self):
        r=self.verdict("C",mutate=lambda s,q,p:p.update(leverage=3,margin_mode="cross"))
        self.assertIn("LEVERAGE_EXCEEDS_LIMIT",r["reasons"])
        self.assertIn("NOT_ISOLATED",r["reasons"])
    def test_swap_funding_fresh(self):
        self.assertIn("STALE_FUNDING_RATE",self.verdict("C",mutate=lambda s,q,p:q.update(funding_ts_ms=NOW-70000))["reasons"])
    def test_swap_liquidation_model_required(self):
        self.assertIn("LIQUIDATION_MODEL_UNVERIFIED",self.verdict("C",mutate=lambda s,q,p:p.update(liquidation_model_verified=False))["reasons"])
    def test_nonfinite_rejected(self):
        self.assertIn("INVALID_FINANCIAL_INPUT",self.verdict(mutate=lambda s,q,p:p.update(notional_usdt="NaN"))["reasons"])
    def test_idempotency_key_required(self):
        self.assertIn("IDEMPOTENCY_KEY_MISSING",self.verdict(mutate=lambda s,q,p:p.pop("idempotency_key"))["reasons"])
    def test_swap_insufficient_margin(self):
        self.assertIn("INSUFFICIENT_ISOLATED_MARGIN",self.verdict("C",mutate=lambda s,q,p:s.update(cash_usdt="100"))["reasons"])
    def test_policy_version_mismatch(self):
        self.assertIn("POLICY_VERSION_UNVERIFIED",self.verdict(mutate=lambda s,q,p:p.update(policy_version="unapproved"))["reasons"])
    def test_a_not_same_as_b(self):
        self.assertIn("ACCOUNT_LEDGER_IDENTITY_UNVERIFIED",self.verdict("A",mutate=lambda s,q,p:s.update(account_id="B"))["reasons"])
    def test_fingerprint_stable(self):
        self.assertEqual(self.verdict()["fingerprint"],self.verdict()["fingerprint"])

if __name__ == "__main__": unittest.main()
