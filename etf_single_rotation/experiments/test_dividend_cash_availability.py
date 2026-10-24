"""Synthetic-only regression tests; no real ETF prices or trading orders."""
import unittest

from etf_single_rotation.experiments.dividend_cash_availability import (
    cash_availability_ledger,
)


def event():
    return {
        "symbol": "510300.SH",
        "type": "CASH_DIVIDEND",
        "event_date": "2025-06-18",
        "registration_date": "2025-06-17",
        "cash_payment_date": "2025-06-27",
        "dividend_per_current_share_cny": 0.088,
        "source_url": "https://example.org/synthetic-fixture",
    }


class CashAvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.registry = {"schema_version": 1, "events": [event()]}
        self.snapshots = {("510300.SH", "2025-06-17"): 100}
        self.days = [
            "2025-06-17", "2025-06-18", "2025-06-26",
            "2025-06-27", "2025-06-30", "2025-07-01",
        ]
        self.availability = {
            ("510300.SH", "2025-06-17"): {
                "available_date": "2025-06-30",
                "evidence_reference": "SYNTHETIC_FIXTURE_NO_REAL_ACCOUNT",
                "evidence_sha256": "a" * 64,
            }
        }

    def run_ledger(self, availability=None, snapshots=None):
        return cash_availability_ledger(
            self.registry,
            self.snapshots if snapshots is None else snapshots,
            self.days,
            self.availability if availability is None else availability,
        )["daily"]

    def test_paid_date_does_not_mean_spendable(self):
        rows = self.run_ledger()
        self.assertAlmostEqual(rows[3]["cumulative_cash_paid_cny"], 8.8)
        self.assertEqual(rows[3]["cumulative_verified_available_cash_cny"], 0)
        self.assertAlmostEqual(rows[3]["paid_but_not_verified_available_cny"], 8.8)
        self.assertAlmostEqual(rows[4]["cumulative_verified_available_cash_cny"], 8.8)
        self.assertEqual(rows[4]["paid_but_not_verified_available_cny"], 0)

    def test_missing_availability_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "Missing verified cash availability"):
            self.run_ledger(availability={})

    def test_early_availability_fails_closed(self):
        evidence = dict(self.availability[("510300.SH", "2025-06-17")])
        evidence["available_date"] = "2025-06-26"
        with self.assertRaisesRegex(ValueError, "cannot precede issuer payment"):
            self.run_ledger(availability={("510300.SH", "2025-06-17"): evidence})

    def test_bad_date_fails_closed(self):
        evidence = dict(self.availability[("510300.SH", "2025-06-17")])
        evidence["available_date"] = "2025-06-99"
        with self.assertRaisesRegex(ValueError, "Invalid availability date"):
            self.run_ledger(availability={("510300.SH", "2025-06-17"): evidence})

    def test_bare_date_cannot_be_mislabelled_verified(self):
        with self.assertRaisesRegex(ValueError, "dated evidence metadata"):
            self.run_ledger(availability={
                ("510300.SH", "2025-06-17"): "2025-06-30"
            })

    def test_missing_reference_fails_closed(self):
        evidence = dict(self.availability[("510300.SH", "2025-06-17")])
        evidence["evidence_reference"] = " "
        with self.assertRaisesRegex(ValueError, "reference is missing"):
            self.run_ledger(availability={
                ("510300.SH", "2025-06-17"): evidence
            })

    def test_malformed_evidence_sha_fails_closed(self):
        evidence = dict(self.availability[("510300.SH", "2025-06-17")])
        evidence["evidence_sha256"] = "not-a-sha"
        with self.assertRaisesRegex(ValueError, "SHA256"):
            self.run_ledger(availability={
                ("510300.SH", "2025-06-17"): evidence
            })

    def test_zero_entitlement_does_not_require_availability(self):
        rows = self.run_ledger(availability={}, snapshots={("510300.SH", "2025-06-17"): 0})
        self.assertTrue(all(row["cumulative_verified_available_cash_cny"] == 0 for row in rows))

    def test_no_available_cash_before_payment(self):
        rows = self.run_ledger()
        self.assertEqual([row["cumulative_verified_available_cash_cny"] for row in rows[:4]], [0] * 4)


if __name__ == "__main__":
    unittest.main()
