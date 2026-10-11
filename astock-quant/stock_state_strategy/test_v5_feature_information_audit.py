from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from ml_cprr_v3 import FEATURES
from v5_feature_information_audit import (
    aggregate_yearly,
    audit_daily_cross_sections,
    benjamini_hochberg,
    build_stability,
    newey_west_mean_test,
    recent_observation_counts,
)


class V5FeatureInformationAuditTests(unittest.TestCase):
    def test_benjamini_hochberg_is_monotone_in_rank(self) -> None:
        result = benjamini_hochberg(pd.Series([0.01, 0.02, 0.20]))
        np.testing.assert_allclose(result.to_numpy(), [0.03, 0.03, 0.20])

    def test_newey_west_detects_constant_positive_mean(self) -> None:
        mean, statistic, p_value = newey_west_mean_test(np.full(50, 0.02))
        self.assertAlmostEqual(mean, 0.02)
        self.assertTrue(np.isposinf(statistic))
        self.assertEqual(p_value, 0.0)

    def test_cross_section_rank_ic_and_decile_lift(self) -> None:
        rows = []
        for day in range(3):
            for rank in range(100):
                value = float(rank)
                row = {
                    "signal_date": pd.Timestamp("2023-01-02")
                    + pd.Timedelta(days=day),
                    "instrument_id": f"S{rank:03d}",
                    "v5_net_r": value,
                    "v5_tail_loss_r": 100.0 - value,
                }
                row.update({feature: value for feature in FEATURES})
                rows.append(row)
        daily = audit_daily_cross_sections(pd.DataFrame(rows))
        self.assertEqual(len(daily), 3 * len(FEATURES) * 2)
        self.assertTrue(np.allclose(daily["rank_ic"], 1.0))
        self.assertTrue((daily["high_decile_score_lift"] > 0).all())
        self.assertTrue((daily["low_decile_score_lift"] < 0).all())

    def test_validation_pass_does_not_depend_on_holdout_sign(self) -> None:
        records = []
        for feature in ("good", "bad"):
            for year in (2022, 2023, 2024, 2025, 2026):
                for day in range(30):
                    good_ic = 0.02 if year <= 2024 else -0.02
                    bad_ic = 0.02 if year in (2022, 2023) else -0.02
                    records.append(
                        {
                            "signal_date": pd.Timestamp(year=year, month=1, day=1)
                            + pd.Timedelta(days=day),
                            "year": year,
                            "target": "net_r",
                            "feature": feature,
                            "rows": 100,
                            "rank_ic": good_ic if feature == "good" else bad_ic,
                            "high_decile_score_lift": 0.10,
                            "high_decile_median_lift": 0.08,
                            "low_decile_score_lift": -0.10,
                            "low_decile_median_lift": -0.08,
                        }
                    )
        daily = pd.DataFrame(records)
        yearly = aggregate_yearly(daily)
        stability = build_stability(daily, yearly).set_index("feature")
        self.assertTrue(bool(stability.loc["good", "validation_pass"]))
        self.assertFalse(bool(stability.loc["good", "holdout_consistent"]))
        self.assertFalse(bool(stability.loc["bad", "validation_pass"]))

    def test_binary_feature_uses_observed_categories_for_extremes(self) -> None:
        rows = []
        for rank in range(100):
            row = {
                "signal_date": pd.Timestamp("2023-02-01"),
                "instrument_id": f"B{rank:03d}",
                "v5_net_r": float(rank),
                "v5_tail_loss_r": float(100 - rank),
            }
            row.update({feature: float(rank) for feature in FEATURES})
            row["near_high20_flag"] = int(rank >= 80)
            rows.append(row)
        daily = audit_daily_cross_sections(pd.DataFrame(rows))
        binary = daily.loc[daily["feature"] == "near_high20_flag"]
        self.assertFalse(binary["high_decile_score_mean"].isna().any())
        self.assertFalse(binary["low_decile_score_mean"].isna().any())
        self.assertTrue((binary["high_decile_rows"] == 20).all())
        self.assertTrue((binary["low_decile_rows"] == 80).all())

    def test_recent_observation_count_respects_calendar_gaps(self) -> None:
        panel = pd.DataFrame(
            {
                "instrument_id": ["A"] * 5 + ["B"] * 3,
                "trade_idx": [0, 1, 2, 10, 11, 5, 6, 7],
            }
        )
        counts = recent_observation_counts(panel, window=3)
        self.assertEqual(counts[:5].tolist(), [1, 2, 3, 1, 2])
        self.assertEqual(counts[5:].tolist(), [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
