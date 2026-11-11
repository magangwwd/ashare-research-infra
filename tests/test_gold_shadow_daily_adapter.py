"""Offline regression tests for the shadow-only daily adapter."""
import unittest
from data_pipeline.gold_shadow_daily_adapter import adapt_daily_shadow


class GoldShadowDailyAdapterTests(unittest.TestCase):
    def setUp(self):
        self.instruments = [{"vendor_symbol": "600031.SH", "instrument_id": "gold-id-1", "as_of_date": "2026-10-24"}]
        self.row = {
            "ts_code": "600031.SH", "security_id": "task01-uuid5-not-gold-id",
            "trade_date": "2026-10-24", "open": 10, "high": 11, "low": 9,
            "close": 10.5, "pre_close": 10, "change": 0.5, "pct_chg": 5,
            "vol": "123.45", "amount": "67.890",
        }

    def adapt(self, rows=None, instruments=None, **kwargs):
        return adapt_daily_shadow(
            [self.row] if rows is None else rows,
            self.instruments if instruments is None else instruments,
            task_as_of=kwargs.get("task_as_of", "2026-10-24"),
            instruments_as_of=kwargs.get("instruments_as_of", "2026-10-24"),
        )

    def test_exact_units_and_explicit_id_mapping(self):
        rows, audit = self.adapt()
        self.assertEqual(rows[0]["instrument_id"], "gold-id-1")
        self.assertEqual(rows[0]["volume_shares"], 12345)
        self.assertEqual(rows[0]["turnover_cny"], 67890)
        self.assertEqual(rows[0]["previous_close"], 10)
        self.assertEqual(rows[0]["return_pct"], 5)
        self.assertFalse(rows[0]["is_quality_valid"])
        self.assertFalse(audit["production_switch_allowed"])
        self.assertFalse(audit["return_pct_scale_verified_against_gold"])

    def test_real_fractional_yuan_is_rounded_only_for_unvalidated_shadow(self):
        rows, audit = self.adapt([dict(self.row, amount="67.8904")])
        self.assertEqual(rows[0]["turnover_cny"], 67890)
        self.assertEqual(audit["turnover_cny_precision_loss_rows"], 1)
        self.assertEqual(audit["turnover_cny_shadow_rounding_rule"], "FLOAT64_RINT_EMULATES_GOLD_PANDAS_SOURCE_UNVERIFIED")
        self.assertFalse(audit["turnover_cny_rounding_contract_verified_against_gold"])
        self.assertFalse(rows[0]["is_quality_valid"])
        self.assertFalse(audit["production_switch_allowed"])

    def test_half_even_turnover_tie_is_explicit_not_silent(self):
        rows, audit = self.adapt([dict(self.row, amount="67.8905")])
        self.assertEqual(rows[0]["turnover_cny"], 67890)
        self.assertEqual(audit["turnover_cny_precision_loss_rows"], 1)
        rows, audit = self.adapt([dict(self.row, amount="67.8915")])
        self.assertEqual(rows[0]["turnover_cny"], 67892)
        self.assertFalse(audit["quality_validation_complete"])

    def test_turnover_matches_observed_gold_pandas_round_recipe(self):
        import pandas as pd
        values = ["67.8904", "67.8905", "67.8915", "1670253.5515", "1743402.5104"]
        expected = (pd.to_numeric(pd.Series(values), errors="coerce") * 1000).round().astype("Int64")
        for src, target in zip(values, expected):
            with self.subTest(src=src):
                rows, audit = self.adapt([dict(self.row, amount=src)])
                self.assertEqual(rows[0]["turnover_cny"], int(target))
                self.assertFalse(audit["production_switch_allowed"])

    def test_float64_rounding_at_int64_boundary_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "overflowing"):
            self.adapt([dict(self.row, amount="9223372036854775.8")])

    def test_negative_turnover_is_rejected_even_in_shadow(self):
        with self.assertRaisesRegex(ValueError, "amount: negative"):
            self.adapt([dict(self.row, amount="-1")])

    def test_turnover_overflow_rejected(self):
        with self.assertRaisesRegex(ValueError, "amount: overflowing"):
            self.adapt([dict(self.row, amount="9223372036854776")])

    def test_exact_integer_turnover_is_not_counted_as_rounded(self):
        rows, audit = self.adapt([dict(self.row, amount="67.890")])
        self.assertEqual(rows[0]["turnover_cny"], 67890)
        self.assertEqual(audit["turnover_cny_precision_loss_rows"], 0)

    def test_future_gold_universe_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "PIT_UNIVERSE_DATE_MISMATCH"):
            self.adapt(instruments_as_of="2026-10-30")

    def test_forged_pit_date_label_cannot_hide_future_gold_rows(self):
        future = [dict(self.instruments[0], as_of_date="2026-10-30")]
        with self.assertRaisesRegex(ValueError, "PIT_UNIVERSE_ROW_DATE_MISMATCH"):
            self.adapt(instruments=future, instruments_as_of="2026-10-24")

    def test_missing_instrument_row_as_of_is_rejected(self):
        without_date = [dict(self.instruments[0])]
        del without_date[0]["as_of_date"]
        with self.assertRaisesRegex(ValueError, "instrument.as_of_date"):
            self.adapt(instruments=without_date)

    def test_per_row_date_object_is_accepted_for_same_cutoff_only(self):
        from datetime import date
        current = [dict(self.instruments[0], as_of_date=date(2026, 10, 24))]
        rows, audit = self.adapt(instruments=current)
        self.assertEqual(len(rows), 1)
        self.assertFalse(audit["point_in_time_membership_verified"])

    def test_nonintegral_units_rejected_instead_of_rounding(self):
        row = dict(self.row, vol="1.001")
        with self.assertRaisesRegex(ValueError, "nonintegral"):
            self.adapt([row])

    def test_negative_volume_rejected(self):
        with self.assertRaisesRegex(ValueError, "negative"):
            self.adapt([dict(self.row, vol=-1)])

    def test_duplicate_primary_key_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate Task01"):
            self.adapt([self.row, self.row])

    def test_duplicate_gold_mapping_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate Gold"):
            self.adapt(instruments=self.instruments * 2)

    def test_nonmainboard_is_explicitly_excluded(self):
        other = dict(self.row, ts_code="300001.SZ", security_id="other")
        rows, audit = self.adapt([self.row, other])
        self.assertEqual(len(rows), 1)
        self.assertEqual(audit["excluded_outside_explicit_gold_universe"], 1)

    def test_invalid_ohlc_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid OHLC"):
            self.adapt([dict(self.row, high=9)])

    def test_future_row_rejected(self):
        with self.assertRaisesRegex(ValueError, "future Task01"):
            self.adapt([dict(self.row, trade_date="2026-10-25")])

    def test_missing_fields_rejected(self):
        row = dict(self.row)
        del row["pre_close"]
        with self.assertRaisesRegex(ValueError, "missing required"):
            self.adapt([row])

    def test_nan_rejected(self):
        with self.assertRaisesRegex(ValueError, "nonfinite"):
            self.adapt([dict(self.row, close=float("nan"))])

    def test_task01_security_id_must_be_stable_across_dates(self):
        other_day = dict(self.row, trade_date="2026-10-23", security_id="different-task-uuid")
        with self.assertRaisesRegex(ValueError, "conflicting Task01 security_id"):
            self.adapt([self.row, other_day])

    def test_task01_security_id_cannot_identify_two_symbols(self):
        outside_gold = dict(self.row, ts_code="300001.SZ", security_id=self.row["security_id"])
        with self.assertRaisesRegex(ValueError, "Task01 security_id reused"):
            self.adapt([self.row, outside_gold])

    def test_consistent_task01_identity_across_dates_remains_shadow_only(self):
        other_day = dict(self.row, trade_date="2026-10-23")
        rows, audit = self.adapt([self.row, other_day])
        self.assertEqual(len(rows), 2)
        self.assertFalse(audit["production_switch_allowed"])
        self.assertFalse(audit["point_in_time_membership_verified"])

    def test_no_matching_rows_rejected(self):
        with self.assertRaisesRegex(ValueError, "no overlapping"):
            self.adapt([dict(self.row, ts_code="300001.SZ")])


if __name__ == "__main__":
    unittest.main()
