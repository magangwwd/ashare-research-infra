"""Pure tests: announced halts cannot be replaced by synthetic ETF bars."""
from __future__ import annotations
import json
import unittest
import pandas as pd

from etf_single_rotation.experiments.audit_suspension_notices import (
    NOTICE_PATH, crosscheck, validate_registry
)


class AnnouncedHaltEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = json.loads(NOTICE_PATH.read_text("utf-8"))
        cls.entries = validate_registry(cls.registry)
        cls.actual_gaps = pd.DataFrame([
            {"symbol": symbol, "date": date, "issue": "MISSING_PRICE_ON_EXCHANGE_OPEN_DAY"}
            for symbol, date in sorted(cls.entries)
        ])

    def test_five_announced_halts_account_for_original_five_gap_keys(self):
        self.assertEqual(len(self.entries), 5)
        empty_prices = pd.DataFrame([{"symbol": "510300.SH", "date": "2024-01-01"}])
        details, summary = crosscheck(self.registry, self.actual_gaps, empty_prices)
        self.assertEqual(summary["documented_announced_halt_rows"], 5)
        self.assertEqual(summary["unexplained_rows"], 0)
        self.assertEqual(summary["status"], "ALL_GAPS_HAVE_ANNOUNCED_SUSPENSION_EVIDENCE")
        self.assertTrue((details.no_synthetic_price == True).all())
        self.assertEqual(summary["price_corporate_action_and_distribution_validation"], "UNVERIFIED")

    def test_extra_absent_session_is_not_silently_cleared(self):
        additional = pd.DataFrame([
            {"symbol": "510300.SH", "date": "2020-01-10",
             "issue": "MISSING_PRICE_ON_EXCHANGE_OPEN_DAY"}
        ])
        detail, report = crosscheck(
            self.registry, pd.concat([self.actual_gaps, additional]),
            pd.DataFrame([{"symbol": "510300.SH", "date": "2025-03-01"}])
        )
        self.assertEqual(report["unexplained_rows"], 1)
        self.assertEqual(report["status"], "REVIEW_REQUIRED_GAP_SOURCE_CONFLICT")
        self.assertEqual(len(detail), 6)

    def test_real_tradable_bar_on_declared_halt_is_a_conflict(self):
        sym, date = next(iter(self.entries))
        prices = pd.DataFrame([{"symbol": sym, "date": date}])
        with self.assertRaisesRegex(ValueError, "Real quote exists"):
            crosscheck(self.registry, self.actual_gaps, prices)

    def test_registry_rejects_duplicate_notice(self):
        corrupted = dict(self.registry)
        corrupted["evidence"] = [*self.registry["evidence"], self.registry["evidence"][0]]
        with self.assertRaisesRegex(ValueError, "Duplicate ETF"):
            validate_registry(corrupted)


if __name__ == "__main__":
    unittest.main()
