"""Synthetic-only regression tests for non-trading ETF cash entitlement accounting."""
import unittest

from etf_single_rotation.experiments.dividend_receivable_ledger import (
    apply_unit_change,
    cash_entitlement_ledger,
    record_date_snapshots_from_fills,
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



class RecordDateFillSnapshotTests(unittest.TestCase):
    """Synthetic fills, not actual strategy executions or account settlement."""

    def setUp(self):
        self.registry = {"schema_version": 1, "events": [dividend()]}
        self.start = "2021-12-29"

    def snapshots(self, opening=None, fills=None):
        return record_date_snapshots_from_fills(
            self.registry, self.start, opening or {}, fills or []
        )

    def test_record_date_buy_is_entitled(self):
        snap = self.snapshots(fills=[
            {"date": "2021-12-30", "symbol": "512690.SH", "side": "BUY", "shares": 10}
        ])
        self.assertEqual(snap[("512690.SH", "2021-12-30")], 10)
        rows = cash_entitlement_ledger(
            self.registry, snap, ["2021-12-30", "2021-12-31", "2022-01-06"]
        )["daily"]
        self.assertEqual(rows[1]["cash_receivable_cny"], 3.5)

    def test_record_date_sell_is_not_entitled(self):
        snap = self.snapshots(opening={"512690.SH": 10}, fills=[
            {"date": "2021-12-30", "symbol": "512690.SH", "side": "SELL", "shares": 10}
        ])
        self.assertEqual(snap[("512690.SH", "2021-12-30")], 0)

    def test_buy_after_record_date_does_not_backdate_entitlement(self):
        snap = self.snapshots(fills=[
            {"date": "2021-12-31", "symbol": "512690.SH", "side": "BUY", "shares": 10}
        ])
        self.assertEqual(snap[("512690.SH", "2021-12-30")], 0)

    def test_same_day_switch_to_dividend_fund(self):
        snap = self.snapshots(opening={"510300.SH": 10}, fills=[
            {"date": "2021-12-30", "symbol": "510300.SH", "side": "SELL", "shares": 10},
            {"date": "2021-12-30", "symbol": "512690.SH", "side": "BUY", "shares": 20},
        ])
        self.assertEqual(snap[("512690.SH", "2021-12-30")], 20)

    def test_intraday_overlap_cannot_be_hidden_by_later_same_day_sale(self):
        # Synthetic order matters: BUY before SELL briefly holds two ETFs.
        # The end-of-day portfolio alone would misleadingly appear valid.
        with self.assertRaisesRegex(ValueError, "More than one ETF"):
            self.snapshots(opening={"510300.SH": 10}, fills=[
                {"date": "2021-12-30", "symbol": "512690.SH",
                 "side": "BUY", "shares": 20},
                {"date": "2021-12-30", "symbol": "510300.SH",
                 "side": "SELL", "shares": 10},
            ])

    def test_sell_more_than_held_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "Sell exceeds"):
            self.snapshots(fills=[
                {"date": "2021-12-30", "symbol": "512690.SH", "side": "SELL", "shares": 1}
            ])

    def test_dual_end_of_day_holdings_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "More than one ETF"):
            self.snapshots(opening={"510300.SH": 10}, fills=[
                {"date": "2021-12-30", "symbol": "512690.SH", "side": "BUY", "shares": 1}
            ])

    def test_bad_fill_order_or_fractional_units_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "chronological"):
            self.snapshots(fills=[
                {"date": "2021-12-30", "symbol": "512690.SH", "side": "BUY", "shares": 1},
                {"date": "2021-12-29", "symbol": "512690.SH", "side": "SELL", "shares": 1},
            ])
        with self.assertRaisesRegex(ValueError, "whole units"):
            self.snapshots(fills=[
                {"date": "2021-12-30", "symbol": "512690.SH", "side": "BUY", "shares": 1.5}
            ])

    def test_missing_pre_record_opening_baseline_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "Opening date"):
            record_date_snapshots_from_fills(
                self.registry, "2021-12-30", {}, []
            )



class RecordDateCorporateActionTests(unittest.TestCase):
    """Synthetic unit changes must precede later dividend registration."""

    def setUp(self):
        self.registry = {
            "schema_version": 1,
            "events": [unit_event("UNIT_SPLIT", 2), dividend()],
        }
        self.key = ("512690.SH", "2021-12-30")

    def snapshots(self, opening_date="2021-05-12", opening=None, fills=None, registry=None):
        return record_date_snapshots_from_fills(
            registry or self.registry, opening_date,
            opening if opening is not None else {"512690.SH": 100},
            fills or [],
        )

    def test_split_before_record_date_doubles_cash_entitlement(self):
        snapshots = self.snapshots()
        self.assertEqual(snapshots[self.key], 200)
        detail = cash_entitlement_ledger(
            self.registry, snapshots, ["2021-12-31", "2022-01-06"]
        )
        self.assertEqual(detail["events"][0]["cash_entitlement_cny"], 70.0)

    def test_post_split_purchase_is_not_doubled(self):
        snap = self.snapshots(opening={}, fills=[
            {"date": "2021-05-17", "symbol": "512690.SH",
             "side": "BUY", "shares": 10},
        ])
        self.assertEqual(snap[self.key], 10)

    def test_sale_after_split_uses_registered_new_units(self):
        snap = self.snapshots(fills=[
            {"date": "2021-05-17", "symbol": "512690.SH",
             "side": "SELL", "shares": 100},
        ])
        self.assertEqual(snap[self.key], 100)

    def test_split_before_opening_date_not_applied_twice(self):
        self.assertEqual(
            self.snapshots(opening_date="2021-06-01",
                           opening={"512690.SH": 10})[self.key], 10
        )

    def test_same_day_fill_and_split_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "same-day corporate event"):
            self.snapshots(opening={}, fills=[
                {"date": "2021-05-14", "symbol": "512690.SH",
                 "side": "BUY", "shares": 10},
            ])

    def test_fractional_split_requires_issuer_rounding(self):
        registry = {"schema_version": 1,
                    "events": [unit_event("UNIT_SPLIT", 1.5), dividend()]}
        with self.assertRaisesRegex(ValueError, "fractional registered"):
            self.snapshots(opening={"512690.SH": 1}, registry=registry)

    def test_held_consolidation_requires_account_rounding(self):
        registry = {"schema_version": 1,
                    "events": [unit_event("UNIT_CONSOLIDATION", 0.36555), dividend()]}
        with self.assertRaisesRegex(ValueError, "issuer-specific"):
            self.snapshots(registry=registry)

    def test_zero_holding_consolidation_does_not_create_units(self):
        registry = {"schema_version": 1,
                    "events": [unit_event("UNIT_CONSOLIDATION", 0.36555), dividend()]}
        self.assertEqual(self.snapshots(opening={}, registry=registry)[self.key], 0)


if __name__ == "__main__":
    unittest.main()
