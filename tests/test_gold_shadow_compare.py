"""Offline tests for strict read-only Task01/Gold daily row comparison."""
import unittest
from data_pipeline.gold_shadow_compare import compare_shadow_daily


class ShadowDailyCompareTests(unittest.TestCase):
    def setUp(self):
        self.task = {
            "vendor_symbol": "600031.SH", "instrument_id": "gold-id-1", "trade_date": "2026-10-24",
            "open": 10, "high": 11, "low": 9, "close": 10.5,
            "volume_shares": 12345, "turnover_cny": 67890,
            "is_quality_valid": False,
        }
        self.gold = dict(self.task, is_quality_valid=True)

    def compare(self, task=None, gold=None, **kwargs):
        return compare_shadow_daily(
            [self.task] if task is None else task,
            [self.gold] if gold is None else gold,
            as_of_date=kwargs.get("as_of_date", "2026-10-24"),
            sample_limit=kwargs.get("sample_limit", 20),
        )

    def test_identical_prices_but_unvalidated_shadow_is_not_a_match(self):
        report = self.compare()
        self.assertEqual(report["status"], "SHADOW_UNVALIDATED")
        self.assertEqual(report["common_keys"], 1)
        self.assertFalse(report["production_switch_allowed"])
        self.assertFalse(report["five_trading_days_verified"])
        self.assertEqual(report["shadow_unvalidated_rows"], 1)

    def test_explicitly_validated_shadow_only_matches_ohlcv(self):
        validated = dict(self.task, is_quality_valid=True)
        report = self.compare(task=[validated])
        self.assertEqual(report["status"], "MATCHED_OHLCV_ONLY")
        self.assertEqual(report["shadow_unvalidated_rows"], 0)
        self.assertFalse(report["production_switch_allowed"])
        self.assertFalse(report["point_in_time_membership_verified"])

    def test_price_difference_is_reported(self):
        report = self.compare(gold=[dict(self.gold, close=10.6)])
        self.assertEqual(report["status"], "DIFFERENCES_FOUND")
        self.assertEqual(report["field_difference_count"], 1)
        self.assertEqual(report["field_difference_examples"][0]["field"], "close")

    def test_units_mismatch_is_not_silently_rounded(self):
        report = self.compare(gold=[dict(self.gold, volume_shares=12346, turnover_cny=67891)])
        self.assertEqual(report["field_difference_count"], 2)

    def test_missing_key_both_directions(self):
        other = dict(self.gold, vendor_symbol="600000.SH")
        report = self.compare(gold=[other])
        self.assertEqual(report["missing_in_gold"], 1)
        self.assertEqual(report["missing_in_task01"], 1)
        self.assertEqual(report["common_keys"], 0)
        self.assertEqual(report["status"], "DIFFERENCES_FOUND")

    def test_duplicate_gold_key_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.compare(gold=[self.gold, self.gold])

    def test_duplicate_task_key_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.compare(task=[self.task, self.task])

    def test_nonfinite_price_rejected(self):
        with self.assertRaisesRegex(ValueError, "nonfinite"):
            self.compare(gold=[dict(self.gold, close=float("inf"))])

    def test_invalid_ohlc_rejected(self):
        with self.assertRaisesRegex(ValueError, "inconsistent"):
            self.compare(task=[dict(self.task, high=10)])

    def test_gold_invalid_quality_blocks_match(self):
        report = self.compare(gold=[dict(self.gold, is_quality_valid=False)])
        self.assertEqual(report["status"], "DIFFERENCES_FOUND")
        self.assertEqual(report["gold_invalid_quality_rows"], 1)

    def test_future_row_rejected(self):
        with self.assertRaisesRegex(ValueError, "future"):
            self.compare(gold=[dict(self.gold, trade_date="2026-10-25")])

    def test_missing_required_field_rejected(self):
        gold = dict(self.gold)
        del gold["volume_shares"]
        with self.assertRaisesRegex(ValueError, "missing OHLCV"):
            self.compare(gold=[gold])

    def test_nonboolean_quality_rejected(self):
        with self.assertRaisesRegex(ValueError, "quality flag"):
            self.compare(gold=[dict(self.gold, is_quality_valid=1)])

    def test_example_limit_does_not_hide_total_difference_count(self):
        task = [dict(self.task, vendor_symbol=f"60000{i}.SH", instrument_id=f"gold-id-{i}") for i in range(3)]
        gold = [dict(r, is_quality_valid=True, close=11) for r in task]
        report = self.compare(task=task, gold=gold, sample_limit=1)
        self.assertEqual(report["field_difference_count"], 3)
        self.assertEqual(len(report["field_difference_examples"]), 1)

    def test_identity_drift_is_reported_even_when_prices_equal(self):
        gold = dict(self.gold, instrument_id="wrong-gold-id")
        report = self.compare(gold=[gold])
        self.assertEqual(report["status"], "DIFFERENCES_FOUND")
        self.assertEqual(report["field_difference_count"], 0)
        self.assertEqual(report["instrument_id_difference_count"], 1)
        self.assertEqual(report["instrument_id_difference_examples"][0]["gold_instrument_id"], "wrong-gold-id")
        self.assertFalse(report["production_switch_allowed"])

    def test_missing_instrument_id_rejected(self):
        row = dict(self.gold)
        del row["instrument_id"]
        with self.assertRaisesRegex(ValueError, "instrument_id"):
            self.compare(gold=[row])

    def test_one_symbol_with_two_instrument_ids_rejected(self):
        earlier = dict(self.gold, trade_date="2026-10-23", instrument_id="wrong-gold-id")
        with self.assertRaisesRegex(ValueError, "conflicting instrument_id"):
            self.compare(gold=[self.gold, earlier])

    def test_instrument_id_reused_for_different_symbols_rejected(self):
        other = dict(self.gold, vendor_symbol="600000.SH")
        with self.assertRaisesRegex(ValueError, "reused by different symbols"):
            self.compare(gold=[self.gold, other])

    def test_date_object_normalized(self):
        from datetime import date
        report = self.compare(gold=[dict(self.gold, trade_date=date(2026, 10, 24))])
        self.assertEqual(report["status"], "SHADOW_UNVALIDATED")

    def test_empty_inputs_do_not_claim_match(self):
        report = self.compare(task=[], gold=[])
        self.assertEqual(report["status"], "DIFFERENCES_FOUND")
        self.assertFalse(report["production_switch_allowed"])


if __name__ == "__main__":
    unittest.main()
