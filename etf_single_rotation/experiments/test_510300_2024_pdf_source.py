"""Seven offline regression checks; synthetic mutations are NOT market data."""
import copy
import json
import unittest
from pathlib import Path

from etf_single_rotation.experiments.verify_510300_2024_pdf_source import validate_2024_source

ROOT = Path(__file__).resolve().parents[1] / "metadata"


def fixtures():
    meta = json.loads((ROOT / "510300_20240111_dividend_primary_sse_crosscheck.json").read_text(encoding="utf-8"))
    candidates = json.loads((ROOT / "510300_dividend_coverage_gap_20261024.json").read_text(encoding="utf-8"))
    return meta, candidates


class Primary2024SourceTests(unittest.TestCase):
    def test_official_source_matches_candidate_but_not_cash_or_prices(self):
        result = validate_2024_source(*fixtures())
        self.assertEqual(result["amount_cny_per_share"], "0.0690")
        self.assertEqual(result["status"], "REVIEW_REQUIRED")
        self.assertEqual(result["trading_status"], "NOT_FOR_TRADING")
        self.assertFalse(result["source_pdf_bytes_verified"])
        self.assertFalse(result["investor_cash_verified"])

    def test_changed_issuer_dates_fail_closed(self):
        for key, bad in (("registration_date", "2024-01-16"),
                         ("ex_dividend_date", "2024-01-19"),
                         ("announced_payment_date", "2024-01-24")):
            meta, candidates = fixtures()
            meta["event"][key] = bad
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_2024_source(meta, candidates)

    def test_changed_amount_or_unit_fails_closed(self):
        for key, bad in (("dividend_per_10_shares_cny", "0.6901"),
                         ("dividend_per_share_cny", "0.6900"),
                         ("dividend_per_share_cny", "NaN")):
            meta, candidates = fixtures()
            meta["event"][key] = bad
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_2024_source(meta, candidates)

    def test_source_host_or_pdf_byte_claim_fails_closed(self):
        for field, bad in (("url", "https://www.sse.com.cn.evil.example/notice.pdf"),
                           ("pdf_downloaded", True), ("pdf_sha256", "0" * 64)):
            meta, candidates = fixtures()
            meta["source"][field] = bad
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_2024_source(meta, candidates)

    def test_candidate_duplicate_and_amount_conflict_fail_closed(self):
        meta, candidates = fixtures()
        row = next(x for x in candidates["events"] if x["ex_dividend_date"] == "2024-01-18")
        candidates["events"].append(copy.deepcopy(row))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            validate_2024_source(meta, candidates)
        meta, candidates = fixtures()
        row = next(x for x in candidates["events"] if x["ex_dividend_date"] == "2024-01-18")
        row["dividend_per_share_cny"] = "0.0691"
        with self.assertRaises(ValueError):
            validate_2024_source(meta, candidates)

    def test_unverified_investor_cash_or_factor_promotion_fails_closed(self):
        meta, candidates = fixtures()
        meta["event"]["investor_cash_available_date"] = "2024-01-23"
        with self.assertRaises(ValueError):
            validate_2024_source(meta, candidates)
        meta, candidates = fixtures()
        meta["crosscheck"]["factor_difference_is_not_independent_price_verification"] = False
        with self.assertRaises(ValueError):
            validate_2024_source(meta, candidates)

    def test_trading_or_final_acceptance_fails_closed(self):
        for key, value in (("status", "VERIFIED_DONE"),
                           ("trading_status", "TRADING_APPROVED"),
                           ("research_only", False)):
            meta, candidates = fixtures()
            meta[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_2024_source(meta, candidates)


if __name__ == "__main__":
    unittest.main()
