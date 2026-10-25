"""Offline regression tests for official SSE 2025 dividend notice source gate."""
import copy
import json
import unittest
from pathlib import Path

from etf_single_rotation.experiments.verify_510300_2025_original_notice import (
    validate_official_notice_link,
)

META = Path(__file__).resolve().parents[1] / "metadata"


def fixtures():
    note = json.loads((META / "510300_2025_original_dividend_notice_link_20261026.json").read_text(encoding="utf-8"))
    candidates = json.loads((META / "510300_dividend_coverage_gap_20261024.json").read_text(encoding="utf-8"))
    return note, candidates


class Official2025NoticeTests(unittest.TestCase):
    def test_matches_official_announcement_without_approval(self):
        note, candidates = fixtures()
        result = validate_official_notice_link(note, candidates)
        self.assertEqual(result["dividend_per_share_cny"], "0.0880")
        self.assertEqual(result["announced_payment_date"], "2025-06-27")
        self.assertEqual(result["status"], "REVIEW_REQUIRED")
        self.assertEqual(result["trading_status"], "NOT_FOR_TRADING")
        self.assertFalse(result["original_pdf_bytes_verified"])
        self.assertFalse(result["broker_cash_verified"])

    def test_rejects_changed_official_dates_or_amount(self):
        note, candidates = fixtures()
        for key, value in (
            ("record_date", "2025-06-16"),
            ("ex_dividend_date", "2025-06-19"),
            ("announced_payment_date", "2025-06-26"),
            ("dividend_per_10_shares_cny", "0.8900"),
        ):
            changed = copy.deepcopy(note)
            changed["announced_distribution"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_official_notice_link(changed, candidates)

    def test_rejects_changed_candidate_dates_or_amount(self):
        note, candidates = fixtures()
        for key, value in (
            ("registration_date", "2025-06-16"),
            ("announced_payment_date", "2025-06-26"),
            ("dividend_per_share_cny", "0.0890"),
        ):
            changed = copy.deepcopy(candidates)
            for row in changed["events"]:
                if row["ex_dividend_date"] == "2025-06-18":
                    row[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_official_notice_link(note, changed)

    def test_rejects_duplicate_candidate(self):
        note, candidates = fixtures()
        changed = copy.deepcopy(candidates)
        row = next(row for row in changed["events"] if row["ex_dividend_date"] == "2025-06-18")
        changed["events"].append(copy.deepcopy(row))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            validate_official_notice_link(note, changed)

    def test_rejects_fake_official_url(self):
        note, candidates = fixtures()
        for url in (
            "http://www.sse.com.cn/disclosure/fund/announcement/c/new/2025-06-11/510300_20250611_ZAU4.pdf",
            "https://www.sse.com.cn.evil.example/disclosure/fund/announcement/c/new/2025-06-11/510300_20250611_ZAU4.pdf",
            "https://www.sse.com.cn/disclosure/fund/announcement/c/new/2025-06-11/510300_20250611_ZAU4.pdf?x=1",
        ):
            changed = copy.deepcopy(note)
            changed["source"]["url"] = url
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_official_notice_link(changed, candidates)

    def test_rejects_unverified_source_promotion(self):
        note, candidates = fixtures()
        for key, value in (
            ("original_pdf_sha256", "0" * 64),
            ("original_pdf_bytes_readback_verified", True),
            ("official_pdf_text_inspected", False),
        ):
            changed = copy.deepcopy(note)
            changed["source"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_official_notice_link(changed, candidates)

    def test_rejects_unsafe_acceptance(self):
        note, candidates = fixtures()
        for key, value in (
            ("status", "VERIFIED_DONE"),
            ("trading_status", "TRADING_APPROVED"),
            ("independent_market_prices_verified", True),
            ("broker_spendable_cash_verified", True),
            ("drive_write_readback_verified", True),
            ("executable_event_registry_updated", True),
        ):
            changed = copy.deepcopy(note)
            changed["acceptance"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_official_notice_link(changed, candidates)


if __name__ == "__main__":
    unittest.main()
