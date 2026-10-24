"""Synthetic-only regression checks for issuer-specific registered unit conversion.

No market prices, portfolio returns, cash subsidy estimates, or trading decisions.
NOT_FOR_TRADING. The published rounded ratio is not a CSDC account statement.
"""
import unittest
from decimal import Decimal

from etf_single_rotation.experiments.registered_consolidation_512100 import (
    registered_consolidation_512100 as convert,
)


class RegisteredConsolidation512100Tests(unittest.TestCase):
    def test_100_units_round_up(self):
        result = convert(100)
        self.assertEqual(result["theoretical_post_registered_units"], 37)
        self.assertEqual(result["fractional_units_at_published_ratio"], "36.55500")
        self.assertEqual(result["theoretical_rounding_increment_units"], "0.44500")

    def test_zero_units(self):
        self.assertEqual(convert(0)["theoretical_post_registered_units"], 0)

    def test_one_unit(self):
        self.assertEqual(convert(1)["theoretical_post_registered_units"], 1)

    def test_exact_multiple(self):
        self.assertEqual(convert(100000)["theoretical_post_registered_units"], 36555)

    def test_bad_registered_share_inputs_fail_closed(self):
        for shares in (-1, 0.5, True, "100"):
            with self.subTest(shares=shares), self.assertRaises(ValueError):
                convert(shares)

    def test_unverified_event_identity_fails_closed(self):
        for kwargs in (
            {"symbol": "510500.SH"},
            {"event_date": "2022-08-03"},
            {"rounding_rule": "ROUND_DOWN"},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                convert(100, **kwargs)

    def test_invalid_ratio_fails_closed(self):
        for ratio in ("0.36554", "NaN", "Infinity", "invalid", None):
            with self.subTest(ratio=ratio), self.assertRaises(ValueError):
                convert(100, published_ratio=ratio)

    def test_no_cash_or_trading_certification(self):
        result = convert(100)
        self.assertIsNone(result["cash_subsidy_cny"])
        self.assertEqual(result["status"], "REVIEW_REQUIRED_NOT_FOR_TRADING")

    def test_large_integer_decimal_exact(self):
        self.assertEqual(convert(10**18)["theoretical_post_registered_units"], 365550000000000000)

    def test_one_share_rounding_increment(self):
        self.assertEqual(Decimal(convert(1)["theoretical_rounding_increment_units"]), Decimal("0.63445"))

    def test_thousand_shares_ceiling(self):
        self.assertEqual(convert(1000)["theoretical_post_registered_units"], 366)

    def test_exact_multiple_zero_increment(self):
        self.assertEqual(Decimal(convert(100000)["theoretical_rounding_increment_units"]), 0)

    def test_published_ratio_precision_must_not_change(self):
        with self.assertRaisesRegex(ValueError, "Unverified published"):
            convert(100, published_ratio="0.36555001")

    def test_per_holder_rounding_must_not_be_aggregated(self):
        individual = sum(convert(1)["theoretical_post_registered_units"] for _ in range(2))
        aggregated = convert(2)["theoretical_post_registered_units"]
        self.assertEqual((individual, aggregated), (2, 1))


if __name__ == "__main__":
    unittest.main()
