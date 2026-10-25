"""Synthetic and metadata regression tests; no market download or trading."""
import copy
import json
import unittest
from pathlib import Path

from etf_single_rotation.experiments.verify_510300_2025_annual import validate_annual_crosscheck

META = Path(__file__).resolve().parents[1] / "metadata"


def fixtures():
    source = json.loads((META / "510300_2025_annual_report_primary_crosscheck_20261026.json").read_text(encoding="utf-8"))
    candidates = json.loads((META / "510300_dividend_coverage_gap_20261024.json").read_text(encoding="utf-8"))
    return source, candidates


class Annual2025CrosscheckTests(unittest.TestCase):
    def test_issuer_annual_report_matches_candidate_without_cash_claim(self):
        source, candidates = fixtures()
        report = validate_annual_crosscheck(source, candidates)
        self.assertEqual(report["record_date"], "2025-06-17")
        self.assertEqual(report["dividend_per_share_cny"], "0.0880")
        self.assertEqual(report["ex_dividend_date"], "2025-06-18")
        self.assertFalse(report["annual_report_confirms_ex_date_or_payment"])
        self.assertFalse(report["broker_cash_verified"])
        self.assertEqual(report["trading_status"], "NOT_FOR_TRADING")

    def test_changed_amount_or_record_date_fails_closed(self):
        source, candidates = fixtures()
        for value in ("0.0890", "0.8800", "NaN", "0"):
            changed = copy.deepcopy(candidates)
            for row in changed["events"]:
                if row["ex_dividend_date"] == "2025-06-18":
                    row["dividend_per_share_cny"] = value
            with self.subTest(amount=value), self.assertRaisesRegex(ValueError, "dividend"):
                validate_annual_crosscheck(source, changed)
        changed = copy.deepcopy(candidates)
        for row in changed["events"]:
            if row["ex_dividend_date"] == "2025-06-18":
                row["registration_date"] = "2025-06-16"
        with self.assertRaisesRegex(ValueError, "Record date"):
            validate_annual_crosscheck(source, changed)

    def test_rejects_false_acceptance_and_unverified_source_promotion(self):
        source, candidates = fixtures()
        for key, bad in (
            ("status", "VERIFIED_DONE"),
            ("trading_status", "TRADING_APPROVED"),
            ("source_pdf_bytes_sha256", "0" * 64),
            ("pdf_text_machine_verified", True),
            ("broker_spendable_cash_verified", True),
            ("independent_market_prices_verified", True),
            ("drive_readback_performed", True),
        ):
            changed = copy.deepcopy(source)
            changed[key] = bad
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_annual_crosscheck(changed, candidates)

    def test_nonofficial_host_and_wrong_exchange_ex_date_fail_closed(self):
        source, candidates = fixtures()
        changed = copy.deepcopy(source)
        changed["issuer_annual_report"]["url"] = "https://example.org/annual.pdf"
        with self.assertRaisesRegex(ValueError, "official SSE"):
            validate_annual_crosscheck(changed, candidates)
        changed = copy.deepcopy(source)
        changed["sse_option_ex_date"]["ex_dividend_date"] = "2025-06-19"
        with self.assertRaisesRegex(ValueError, "Ex-date"):
            validate_annual_crosscheck(changed, candidates)


if __name__ == "__main__":
    unittest.main()
