"""Synthetic causality tests, not 600031 return or live-execution validation."""
import copy
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / "research" / "price_action_v2_core.py"
SPEC = importlib.util.spec_from_file_location("price_action_v2_core", PATH)
core = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(core)


def bars(highs=(11, 12, 15, 12, 11), lows=(9, 9, 9, 9, 9)):
    return [dict(bar_end=f"2026-01-{i+5:02d}T15:00:00+08:00",
                 open=10, high=h, low=l, close=10)
            for i, (h, l) in enumerate(zip(highs, lows))]


class CausalityTests(unittest.TestCase):
    def test_confirmation_waits_for_two_completed_right_bars(self):
        rows = bars()
        self.assertEqual(core.confirmed_swings(rows[:4]), [])
        event, = core.confirmed_swings(rows)
        self.assertEqual(event["event_time"], rows[2]["bar_end"])
        self.assertEqual(event["confirmed_at"], rows[4]["bar_end"])
        self.assertIsNone(event["earliest_execution_at"])
        self.assertEqual(event["execution_not_before"], rows[4]["bar_end"])

    def test_low_swing_is_confirmed_symmetrically(self):
        event, = core.confirmed_swings(bars((11,)*5, (9, 8, 5, 8, 9)))
        self.assertEqual((event["side"], event["value"]), ("low", 5))

    def test_appended_future_does_not_rewrite_confirmed_prefix(self):
        prefix = bars()
        extension = bars((11,12,15,12,11,999,1000,998),
                         (9,9,9,9,9,1,2,3))
        past = [e for e in core.confirmed_swings(extension)
                if e["confirmed_at"] <= prefix[-1]["bar_end"]]
        self.assertEqual(past, core.confirmed_swings(prefix))

    def test_future_price_mutation_preserves_past_events(self):
        rows = bars((11,12,15,12,11,13,14,12), (9,)*8)
        changed = copy.deepcopy(rows)
        changed[5]["high"], changed[5]["low"] = 10000, .01
        before = [e for e in core.confirmed_swings(rows)
                  if e["confirmed_at"] <= rows[4]["bar_end"]]
        after = [e for e in core.confirmed_swings(changed)
                 if e["confirmed_at"] <= rows[4]["bar_end"]]
        self.assertEqual(before, after)

    def test_tied_extrema_are_not_arbitrarily_promoted(self):
        self.assertEqual(core.confirmed_swings(bars((11,12,15,15,11))), [])

    def test_zero_range_and_normal_candle_ratios(self):
        row = bars((10,), (10,))[0]
        values = core.candle_features(row)
        self.assertTrue(values["zero_range"])
        self.assertIsNone(values["close_position"])
        row.update(open=10, high=14, low=8, close=13)
        values = core.candle_features(row)
        self.assertAlmostEqual(values["body_ratio"], .5)
        self.assertAlmostEqual(values["close_position"], 5/6)
        self.assertAlmostEqual(values["upper_wick_ratio"], 1/6)
        self.assertAlmostEqual(values["lower_wick_ratio"], 2/6)

    def test_invalid_prices_and_timestamp_order_fail_closed(self):
        for updates in (dict(high=float("nan")), dict(low=-1),
                        dict(open=20), dict(close=True),
                        dict(bar_end="2026-01-05T15:00:00")):
            with self.subTest(updates=updates):
                row = bars((11,), (9,))[0]
                row.update(updates)
                with self.assertRaises(ValueError):
                    core.validate_bars([row])
        with self.assertRaises(ValueError):
            core.validate_bars([bars()[0], bars()[0]])
        with self.assertRaises(ValueError):
            core.validate_bars(list(reversed(bars())))

    def test_training_labels_must_finish_before_test_boundary(self):
        times = [r["bar_end"] for r in bars()]
        self.assertTrue(core.training_label_allowed(times[0], times[1], times[2]))
        self.assertFalse(core.training_label_allowed(times[0], times[2], times[2]))
        self.assertFalse(core.training_label_allowed(times[0], times[3], times[2]))
        with self.assertRaises(ValueError):
            core.training_label_allowed(times[1], times[0], times[2])

    def test_rule_hash_is_order_invariant_but_change_sensitive(self):
        first = core.rule_fingerprint(dict(left=2, right=2))
        self.assertEqual(first, core.rule_fingerprint(dict(right=2, left=2)))
        self.assertNotEqual(first, core.rule_fingerprint(dict(left=2, right=3)))
        with self.assertRaises(ValueError):
            core.rule_fingerprint(dict(threshold=float("nan")))

    def test_invalid_windows_and_insufficient_history(self):
        for left, right in ((0,2), (2,-1), (True,2), (2,1.5)):
            with self.assertRaises(ValueError):
                core.confirmed_swings(bars(), left=left, right=right)
        self.assertEqual(core.confirmed_swings([]), [])
        self.assertEqual(core.confirmed_swings(bars()[:3]), [])


if __name__ == "__main__":
    unittest.main()
