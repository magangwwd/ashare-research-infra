"""Synthetic-only regression tests for non-trading ETF cash entitlement accounting."""
import unittest

from etf_single_rotation.experiments.dividend_receivable_ledger import (
    apply_unit_change,
    cash_entitlement_ledger,
)


def dividend():
    return {
        "symbol": "512690.SH",
        "type": "CASH_DIVIDEND",
        "event_date": "2021-12-31",
        "registration_date": "2021-12-30",
        "cash_payment_date": "2022-01-06",
        "dividend_per_current_share_cny": 0.35,
        "source_url": "https://example.org/synthetic-fixture",
    }


def unit_event(kind, ratio):
    return {
        "symbol": "512690.SH",
        "event_date": "2021-05-14",
        "type": kind,
        "new_units_per_old_unit": ratio,
        "source_url": "https://example.org/synthetic-fixture",
    }


class CashEntitlementTests(unittest.TestCase):
    def setUp(self):
        self.registry = {"schema_version": 1, "events": [dividend()]}
        self.snapshots = {("512690.SH", "2021-12-30"): 10.0}
        self.days = [
            "2021-12-29", "2021-12-30", "2021-12-31",
            "2022-01-05", "2022-01-06", "2022-01-07",
        ]

    def test_record_date_accrual_then_payment_without_double_count(self):
        rows = cash_entitlement_ledger(self.registry, self.snapshots, self.days)["daily"]
        self.assertEqual([x["record_date_locked_entitlement_cny"] for x in rows], [0, 3.5, 3.5, 3.5, 0, 0])
        self.assertEqual([x["cash_receivable_cny"] for x in rows], [0, 0, 3.5, 3.5, 0, 0])
        self.assertEqual([x["cumulative_cash_paid_cny"] for x in rows], [0, 0, 0, 0, 3.5, 3.5])

    def test_unadjusted_price_nav_has_no_record_date_double_count(self):
        rows = cash_entitlement_ledger(self.registry, self.snapshots, self.days)["daily"]
        raw_prices = [2, 2, 1.65, 1.65, 1.65, 1.65]  # synthetic, not market data
        nav = [10 * p + row["cash_receivable_cny"] + row["cumulative_cash_paid_cny"]
               for p, row in zip(raw_prices, rows)]
        self.assertEqual(nav, [20] * len(self.days))

    def test_same_day_registration_and_ex_date(self):
        event = dividend()
        event["registration_date"] = event["event_date"]
        snapshots = {("512690.SH", "2021-12-31"): 10}
        rows = cash_entitlement_ledger({"schema_version": 1, "events": [event]}, snapshots, self.days)["daily"]
        self.assertEqual([r["cash_receivable_cny"] for r in rows], [0, 0, 3.5, 3.5, 0, 0])

    def test_zero_record_date_holding_is_explicitly_allowed(self):
        rows = cash_entitlement_ledger(
            self.registry, {("512690.SH", "2021-12-30"): 0}, self.days
        )["daily"]
        self.assertTrue(
            all(x["cash_receivable_cny"] == 0 and x["cumulative_cash_paid_cny"] == 0 for x in rows)
        )

    def test_missing_snapshot_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "Missing record-date"):
            cash_entitlement_ledger(self.registry, {}, self.days)

    def test_negative_or_nonfinite_shares_fails_closed(self):
        for bad in (-1, float("nan"), float("inf")):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "Invalid record-date"):
                cash_entitlement_ledger(
                    self.registry, {("512690.SH", "2021-12-30"): bad}, self.days
                )

    def test_duplicate_or_unsorted_dates_fails_closed(self):
        for days in (["2021-12-31", "2021-12-30"], ["2021-12-30", "2021-12-30"], []):
            with self.subTest(days=days), self.assertRaisesRegex(ValueError, "Observation dates"):
                cash_entitlement_ledger(self.registry, self.snapshots, days)

    def test_duplicate_cash_event_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            cash_entitlement_ledger(
                {"schema_version": 1, "events": [dividend(), dividend()]},
                self.snapshots,
                self.days,
            )

    def test_split_and_consolidation_preserve_units_without_cash(self):
        self.assertEqual(apply_unit_change(10, unit_event("UNIT_SPLIT", 2)), 20)
        self.assertAlmostEqual(
            apply_unit_change(10, unit_event("UNIT_CONSOLIDATION", 0.36555)), 3.6555
        )

    def test_bad_unit_event_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "contradicts"):
            apply_unit_change(10, unit_event("UNIT_SPLIT", 0.5))
        with self.assertRaisesRegex(ValueError, "Expected a unit-changing"):
            apply_unit_change(10, dividend())


if __name__ == "__main__":
    unittest.main()
