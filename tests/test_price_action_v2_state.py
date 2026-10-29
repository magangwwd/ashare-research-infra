"""Synthetic tests for the frozen PA-v2 state machine; not real-data evidence."""
import copy
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / "research" / "price_action_v2_state.py"
SPEC = importlib.util.spec_from_file_location("price_action_v2_state", PATH)
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def bar(i, **updates):
    row = dict(bar_end=f"2026-01-{i+1:02d}T15:00:00+08:00",
               open=10., high=11., low=9., close=10., factor=1.,
               data_verified=True, session_ok=True, features_verified=True)
    row["features"] = dict(asof=row["bar_end"], atr20=2., h20=100.,
                           l20=1., h10=13., sma5=10., sma20=10.,
                           sma20_prev5=10., prev_close=10.)
    features = updates.pop("features", {})
    row.update(updates)
    row["features"].update(features)
    return row


def breakout(i):
    return bar(i, open=12., high=14., low=12., close=13.,
               features=dict(h20=12.))


def breakdown(i):
    return bar(i, open=8., high=9., low=6., close=7.,
               features=dict(l20=8.))


def pullback(i):
    return bar(i, open=12., high=12., low=10., close=11.,
               features=dict(sma5=11., sma20=10., sma20_prev5=9.,
                             prev_close=12., h10=13.))


class PA2StateTests(unittest.TestCase):
    def test_a_confirmed_next_close_no_assumed_execution(self):
        a = breakout(0)
        follow = bar(1, open=13., high=14., low=12., close=13.)
        result = m.scan([a, follow])
        event, = result["events"]
        self.assertEqual((event["kind"], event["status"]), ("PA2_A", "confirmed"))
        self.assertEqual(event["event_time"], a["bar_end"])
        self.assertEqual(event["confirmed_at"], follow["bar_end"])
        self.assertIsNone(event["earliest_execution_at"])
        self.assertEqual(event["sessions_waited"], 1)

    def test_a_failure_precedes_confirmation(self):
        result = m.scan([breakout(0), bar(1, open=10., high=11., low=9., close=10.)])
        self.assertEqual(result["events"][0]["status"], "failed")
        self.assertEqual(result["events"][0]["reason"], "invalidation")

    def test_a_timeout_includes_fifth_session(self):
        rows = [breakout(0)] + [bar(i) for i in range(1, 6)]
        self.assertEqual(m.scan(rows)["events"][0]["status"], "timeout")
        self.assertEqual(m.scan(rows[:-1])["events"][0]["status"], "pending")

    def test_b_confirmed_with_asof_sma5_and_prior_high(self):
        rows = [pullback(0), bar(1, open=11., high=14., low=10., close=13.,
                                  features=dict(sma5=11.))]
        event, = m.scan(rows)["events"]
        self.assertEqual((event["kind"], event["status"]), ("PA2_B", "confirmed"))

    def test_b_ambiguous_same_bar_fails_conservatively(self):
        rows = [pullback(0), bar(1, open=12., high=14., low=8., close=13.,
                                  features=dict(sma5=11.))]
        result = m.scan(rows)
        self.assertEqual(result["events"][0]["status"], "failed")
        self.assertEqual(result["events"][0]["reason"], "ambiguous_same_bar")
        self.assertEqual(result["counts"]["ambiguous_same_bar"], 1)

    def test_b_missing_asof_sma5_blocks(self):
        rows = [pullback(0), bar(1, open=11., high=14., low=10., close=13.,
                                  features_verified=False)]
        self.assertEqual(m.scan(rows)["events"][0]["status"], "data_blocked")

    def test_c_confirmed_with_future_adjustment_factor(self):
        rows = [breakdown(0), bar(1, open=4., high=5., low=3., close=4.5,
                                  factor=2.)]
        event, = m.scan(rows)["events"]
        self.assertEqual((event["kind"], event["status"]), ("PA2_C", "confirmed"))

    def test_c_failure_then_no_retroactive_recovery(self):
        rows = [breakdown(0), bar(1, open=5., high=6., low=4., close=4.5),
                bar(2, open=9., high=10., low=8., close=9.)]
        event, = m.scan(rows)["events"]
        self.assertEqual(event["status"], "failed")
        self.assertEqual(event["terminal_at"], rows[1]["bar_end"])

    def test_missing_session_is_blocked_not_timeout(self):
        rows = [breakout(0), bar(1, data_verified=False)]
        result = m.scan(rows)
        self.assertEqual(result["events"][0]["status"], "data_blocked")
        self.assertEqual(result["invalid_days"], 1)

    def test_five_full_cooldown_sessions_and_exclusions(self):
        rows = [breakout(i) for i in range(9)]
        result = m.scan(rows)
        self.assertEqual(result["events"][0]["status"], "confirmed")
        self.assertEqual(result["events"][1]["event_time"], rows[7]["bar_end"])
        self.assertEqual(result["counts"]["excluded_cooldown"], 6)
        self.assertEqual(result["counts"]["candidates"], 2)

    def test_future_extension_does_not_rewrite_past_terminal(self):
        prefix = [breakout(0), bar(1, open=13., high=14., low=12., close=13.)]
        old = m.scan(prefix)["events"][0]
        extended = prefix + [bar(i) for i in range(2, 9)]
        self.assertEqual(old, m.scan(extended)["events"][0])

    def test_future_mutation_does_not_change_past_candidate(self):
        rows = [breakout(0), bar(1)]
        changed = copy.deepcopy(rows)
        changed[1]["high"] = 10000.
        self.assertEqual(m.detect(rows[0]), m.detect(changed[0]))
        self.assertEqual(m.scan(rows)["events"][0]["event_time"],
                         m.scan(changed)["events"][0]["event_time"])

    def test_invalid_and_non_asof_features_cannot_trigger(self):
        row = breakout(0)
        row["features"]["asof"] = "2026-01-03T15:00:00+08:00"
        self.assertEqual(m.detect(row), [])
        row = breakout(0)
        row["features"]["atr20"] = float("nan")
        self.assertEqual(m.detect(row), [])
        row = breakout(0)
        row["factor"] = None
        self.assertEqual(m.detect(row), [])

    def test_unsorted_or_naive_times_fail(self):
        with self.assertRaises(ValueError):
            m.scan([bar(1), bar(0)])
        row = bar(0)
        row["bar_end"] = "2026-01-01T15:00:00"
        with self.assertRaises(ValueError):
            m.scan([row])

    def test_same_day_close_never_backfills_confirmed_at(self):
        rows = [breakout(0)]
        event, = m.scan(rows)["events"]
        self.assertEqual(event["status"], "pending")
        self.assertIsNone(event["confirmed_at"])


if __name__ == "__main__":
    unittest.main()
