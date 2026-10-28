"""Synthetic-only tests: ambiguous same-day unit events must not invent ETF shares.

No market data, dividend certification, backtest, or live trading.
"""
import unittest

from etf_single_rotation.experiments.dividend_receivable_ledger import (
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
        "source_url": "https://example.org/synthetic-only",
    }


def unit_change(symbol, kind, ratio):
    return {
        "symbol": symbol,
        "type": kind,
        "event_date": "2021-05-14",
        "new_units_per_old_unit": ratio,
        "source_url": "https://example.org/synthetic-only",
    }


class SameDayUnitEventGuardTests(unittest.TestCase):
    def snapshots(self, events):
        return record_date_snapshots_from_fills(
            {"schema_version": 1, "events": events},
            "2021-05-12",
            {"512690.SH": 100},
            [],
        )

    def test_two_different_unit_changes_same_symbol_and_day_fail_closed(self):
        events = [
            unit_change("512690.SH", "UNIT_SPLIT", 2),
            unit_change("512690.SH", "UNIT_CONSOLIDATION", 0.5),
            dividend(),
        ]
        with self.assertRaisesRegex(ValueError, "Ambiguous multiple unit events"):
            self.snapshots(events)

    def test_same_day_unit_events_for_different_symbols_remain_valid(self):
        events = [
            unit_change("512690.SH", "UNIT_SPLIT", 2),
            unit_change("510300.SH", "UNIT_CONSOLIDATION", 0.5),
            dividend(),
        ]
        snapshots = self.snapshots(events)
        self.assertEqual(snapshots[("512690.SH", "2021-12-30")], 200)


    def test_same_symbol_unit_changes_on_separate_days_remain_valid(self):
        # Separate issuer dates have a deterministic chronological order.
        first = unit_change("512690.SH", "UNIT_SPLIT", 2)
        second = {
            **unit_change("512690.SH", "UNIT_SPLIT", 3),
            "event_date": "2021-06-01",
        }
        snapshots = self.snapshots([first, second, dividend()])
        self.assertEqual(snapshots[("512690.SH", "2021-12-30")], 600)

    def test_duplicate_same_kind_same_day_also_fails_closed(self):
        # Duplicate rows are just as ambiguous as two different event types.
        duplicate = unit_change("512690.SH", "UNIT_SPLIT", 2)
        # Identical (symbol, date, type) rows are rejected earlier by
        # validate_events, before the cross-type same-day ambiguity guard.
        with self.assertRaisesRegex(ValueError, "Duplicate corporate-action event"):
            self.snapshots([duplicate, dict(duplicate), dividend()])


if __name__ == "__main__":
    unittest.main()
