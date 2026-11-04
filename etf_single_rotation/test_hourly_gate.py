"""Deterministic scheduler gate checks; Python stdlib only, safe for hourly ticks."""
import unittest
from datetime import datetime, timezone

from etf_single_rotation.hourly_research_gate import decide


class ETFHourlyGateTests(unittest.TestCase):
    def utc(self, when):
        return datetime.fromisoformat(when.replace("Z", "+00:00"))

    def test_close_time_eligible_weekday(self):
        d = decide("schedule", "full", self.utc("2026-10-21T09:13:00Z"))
        self.assertTrue(d["eligible_full_research"])
        self.assertEqual(d["reason"], "CLOSED_A_SHARE_WEEKDAY")
        self.assertEqual(d["beijing_date"], "2026-10-21")

    def test_do_not_full_backtest_before_close(self):
        d = decide("schedule", "full", self.utc("2026-10-21T08:13:00Z"))
        self.assertFalse(d["eligible_full_research"])
        self.assertEqual(d["reason"], "WAIT_FOR_MARKET_CLOSE")

    def test_do_not_full_backtest_weekend(self):
        d = decide("schedule", "full", self.utc("2026-10-24T09:13:00Z"))
        self.assertFalse(d["eligible_full_research"])
        self.assertEqual(d["reason"], "WEEKEND")

    def test_do_not_full_backtest_on_workflow_push(self):
        d = decide("push", "full", self.utc("2026-10-21T09:13:00Z"))
        self.assertFalse(d["eligible_full_research"])
        self.assertEqual(d["reason"], "WORKFLOW_DEFINITION_SMOKE_TEST")

    def test_manual_full_can_run_earlier_for_validation(self):
        d = decide("workflow_dispatch", "full", self.utc("2026-10-24T03:00:00Z"))
        self.assertTrue(d["eligible_full_research"])
        self.assertEqual(d["reason"], "MANUAL_FULL")

    def test_manual_quick_does_not_run_full(self):
        d = decide("workflow_dispatch", "quick", self.utc("2026-10-21T09:13:00Z"))
        self.assertFalse(d["eligible_full_research"])

    def test_reject_naive_timestamp(self):
        with self.assertRaises(ValueError):
            decide("schedule", "full", datetime(2026, 10, 21, 9, 13))


if __name__ == "__main__":
    unittest.main()
