"""Regression tests for the independent ETF implicit-rebalance audit."""
import unittest
import pandas as pd

from etf_single_rotation.experiments.audit_execution_model import (
    compute_hidden_rebalancing,
)


class ExecutionAuditTests(unittest.TestCase):
    def test_fractional_etf_exposure_requires_hidden_rebalance(self):
        dates = pd.bdate_range("2020-01-01", periods=4)
        prices = pd.DataFrame({"510300.SH": [100, 110, 110, 110]}, index=dates)
        weights = pd.DataFrame({"510300.SH": [0.5, 0.5, 0.5, 0.5]}, index=dates)
        gaps = compute_hidden_rebalancing(prices, weights)
        self.assertEqual(len(gaps), 1)
        # After a 10% move, a 50%-invested ETF position drifts to 52.38%.
        self.assertAlmostEqual(
            float(gaps.uncharged_turnover_notional.iloc[0]),
            abs(0.5 - (0.5 * 1.10 / 1.05)), places=10,
        )

    def test_single_fully_invested_etf_does_not_drift(self):
        dates = pd.bdate_range("2020-01-01", periods=4)
        prices = pd.DataFrame({"510300.SH": [100, 110, 108, 112]}, index=dates)
        weights = pd.DataFrame({"510300.SH": [1.0] * 4}, index=dates)
        gaps = compute_hidden_rebalancing(prices, weights)
        self.assertTrue(gaps.empty)

    def test_invalid_weights_rejected(self):
        dates = pd.bdate_range("2020-01-01", periods=3)
        prices = pd.DataFrame({"510300.SH": [100, 110, 105]}, index=dates)
        weights = pd.DataFrame({"510300.SH": [1.2] * 3}, index=dates)
        with self.assertRaises(ValueError):
            compute_hidden_rebalancing(prices, weights)


if __name__ == "__main__":
    unittest.main()
