"""Offline tests; no prices are fabricated to satisfy a four-hour gap."""
import datetime as dt
import importlib.util
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "data"))
from audit_short_interval_gap import aggregate_short, gaps_in_source, inspect_short

UTC = dt.timezone.utc
START = dt.datetime(2018, 2, 8, 4, 0, tzinfo=UTC)
BAR = (100.0, 102.0, 99.0, 101.0, 2.0)


class ShortPilotTests(unittest.TestCase):
    def test_exact_240_one_minute_bars_rebuild_one_four_hour_bar(self):
        bars = {START + dt.timedelta(minutes=i): BAR for i in range(240)}
        derived, missing = aggregate_short(bars, START, "1m")
        self.assertEqual(missing, [])
        self.assertEqual(derived, (100.0, 102.0, 99.0, 101.0, 480.0))

    def test_one_missing_bar_blocks_rebuild_instead_of_filling(self):
        bars = {START + dt.timedelta(minutes=i): BAR for i in range(240)}
        lost = START + dt.timedelta(minutes=73)
        del bars[lost]
        derived, missing = aggregate_short(bars, START, "1m")
        self.assertIsNone(derived)
        self.assertEqual(missing, [lost])

    def test_five_minute_aggregate_requires_48_complete_bars(self):
        bars = {START + dt.timedelta(minutes=5*i): BAR for i in range(47)}
        derived, missing = aggregate_short(bars, START, "5m")
        self.assertIsNone(derived)
        self.assertEqual(len(missing), 1)
        bars[START + dt.timedelta(minutes=235)] = BAR
        self.assertIsNotNone(aggregate_short(bars, START, "5m")[0])

    def test_discontinuity_preserved_for_a_market_outage(self):
        bars = {
            START: BAR,
            START + dt.timedelta(minutes=1): BAR,
            START + dt.timedelta(minutes=60): BAR,
        }
        gaps = gaps_in_source(bars, "1m")
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["missing_interval_count"], 58)

    def test_no_fake_repair_on_empty_source(self):
        result = inspect_short("1m", {}, [], {})
        self.assertEqual(result["missing_bars_in_original_seven_4h_windows"], 7*240)
        self.assertFalse(result["can_rebuild_all_seven_missing_4h"])


if __name__ == "__main__":
    unittest.main()
