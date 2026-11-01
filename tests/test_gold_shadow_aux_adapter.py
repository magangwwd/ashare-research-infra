"""Offline regression tests for Task01 auxiliary Gold shadow candidates."""
import unittest
from datetime import date

from data_pipeline.gold_shadow_aux_adapter import (
    adapt_daily_metrics_shadow, adapt_adj_factor_shadow,
)


class AuxiliaryShadowAdapterTests(unittest.TestCase):
    def setUp(self):
        self.instruments = [
            {"vendor_symbol": "600031.SH", "instrument_id": "gold-id-600031", "as_of_date": "2026-10-24"}
        ]
        self.base = {"ts_code": "600031.SH", "security_id": "task-uuid-600031", "trade_date": "2026-10-24"}
        self.metrics = dict(
            self.base, close=12.5, turnover_rate=1.2, turnover_rate_f=1.0,
            volume_ratio=None, pe=15.2, pe_ttm=14.8, pb=1.9, ps=2.5,
            ps_ttm=2.3, dv_ratio=1.5, dv_ttm=None,
            total_share="123.4", float_share="120.01", free_share="100.0",
            total_mv="1542.5", circ_mv="1500.125",
        )
        self.adjust = dict(self.base, adj_factor="1.02345")

    def metrics_adapt(self, rows=None, instruments=None, **kwargs):
        return adapt_daily_metrics_shadow(
            [self.metrics] if rows is None else rows,
            self.instruments if instruments is None else instruments,
            task_as_of=kwargs.get("task_as_of", "2026-10-24"),
            instruments_as_of=kwargs.get("instruments_as_of", "2026-10-24"),
        )

    def adj_adapt(self, rows=None, instruments=None, **kwargs):
        return adapt_adj_factor_shadow(
            [self.adjust] if rows is None else rows,
            self.instruments if instruments is None else instruments,
            task_as_of=kwargs.get("task_as_of", "2026-10-24"),
            instruments_as_of=kwargs.get("instruments_as_of", "2026-10-24"),
        )

    def test_metrics_exact_shares_and_market_cap_units(self):
        rows, audit = self.metrics_adapt()
        self.assertEqual(rows[0]["total_shares"], 1234000)
        self.assertEqual(rows[0]["float_shares"], 1200100)
        self.assertEqual(rows[0]["market_cap_cny"], 15425000)
        self.assertEqual(rows[0]["float_market_cap_cny"], 15001250)
        self.assertIsNone(rows[0]["volume_ratio"])
        self.assertIsNone(rows[0]["dividend_yield_ttm_pct"])
        self.assertFalse(audit["production_switch_allowed"])

    def test_metrics_limit_status_not_fabricated(self):
        rows, audit = self.metrics_adapt()
        self.assertIsNone(rows[0]["limit_status"])
        self.assertTrue(audit["requires_daily_limits_join"])
        self.assertFalse(audit["quality_validation_complete"])

    def test_metrics_nonintegral_share_units_rejected(self):
        row = dict(self.metrics, total_share="1.00001")
        with self.assertRaisesRegex(ValueError, "nonintegral"):
            self.metrics_adapt([row])

    def test_metrics_negative_share_units_rejected(self):
        with self.assertRaisesRegex(ValueError, "negative"):
            self.metrics_adapt([dict(self.metrics, total_mv=-2)])

    def test_metrics_missing_required_value_key_rejected(self):
        row = dict(self.metrics)
        del row["pe"]
        with self.assertRaisesRegex(ValueError, "missing required"):
            self.metrics_adapt([row])

    def test_metrics_future_gold_instruments_rejected_despite_label(self):
        future = [dict(self.instruments[0], as_of_date="2026-10-30")]
        with self.assertRaisesRegex(ValueError, "PIT_UNIVERSE_ROW_DATE_MISMATCH"):
            self.metrics_adapt(instruments=future)

    def test_metrics_id_collision_rejected(self):
        extra = dict(self.metrics, ts_code="600032.SH")
        with self.assertRaisesRegex(ValueError, "security_id reused"):
            self.metrics_adapt([self.metrics, extra])

    def test_metrics_duplicate_primary_key_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate Task01"):
            self.metrics_adapt([self.metrics, self.metrics])

    def test_metrics_excluded_universe_audited(self):
        extra = dict(self.metrics, ts_code="300001.SZ", security_id="uuid-300001")
        rows, audit = self.metrics_adapt([self.metrics, extra])
        self.assertEqual(len(rows), 1)
        self.assertEqual(audit["excluded_outside_explicit_gold_universe"], 1)

    def test_factor_conversion_requires_positive_finite(self):
        rows, audit = self.adj_adapt()
        self.assertEqual(rows[0]["adjustment_factor"], 1.02345)
        self.assertFalse(audit["corporate_actions_verified"])
        for value in (-1, 0, float("nan"), float("inf")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.adj_adapt([dict(self.adjust, adj_factor=value)])

    def test_factor_future_date_rejected(self):
        with self.assertRaisesRegex(ValueError, "future Task01"):
            self.adj_adapt([dict(self.adjust, trade_date="2026-10-30")])

    def test_factor_current_gold_universe_must_match_snapshot(self):
        future = [dict(self.instruments[0], as_of_date=date(2026, 10, 30))]
        with self.assertRaisesRegex(ValueError, "PIT_UNIVERSE_ROW_DATE_MISMATCH"):
            self.adj_adapt(instruments=future)

    def test_factor_no_overlap_rejected(self):
        with self.assertRaisesRegex(ValueError, "no overlapping"):
            self.adj_adapt([dict(self.adjust, ts_code="300001.SZ")])

    def test_factor_genuine_date_object_allowed(self):
        row = dict(self.adjust, trade_date=date(2026, 10, 24))
        inst = [dict(self.instruments[0], as_of_date=date(2026, 10, 24))]
        rows, audit = self.adj_adapt([row], inst)
        self.assertEqual(rows[0]["trade_date"], "2026-10-24")
        self.assertFalse(audit["point_in_time_membership_verified"])


if __name__ == "__main__":
    unittest.main()
