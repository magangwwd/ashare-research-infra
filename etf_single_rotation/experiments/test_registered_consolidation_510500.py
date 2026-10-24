"""Synthetic-only issuer-rule regression tests. NOT_FOR_TRADING."""
import unittest

from etf_single_rotation.experiments.registered_consolidation_510500 import (
    registered_consolidation_510500 as convert,
)


class RegisteredConsolidation510500Tests(unittest.TestCase):
    def test_hundred_units_round_down(self):
        result = convert(100)
        self.assertEqual(result["theoretical_post_registered_units"], 28)
        self.assertEqual(result["fractional_units_at_published_ratio"], "28.03248300")
        self.assertEqual(result["theoretical_discarded_fractional_units"], "0.03248300")

    def test_zero_and_one_unit(self):
        self.assertEqual(convert(0)["theoretical_post_registered_units"], 0)
        self.assertEqual(convert(1)["theoretical_post_registered_units"], 0)

    def test_exact_multiple_no_discard(self):
        result = convert(100000000)
        self.assertEqual(result["theoretical_post_registered_units"], 28032483)
        self.assertEqual(result["theoretical_discarded_fractional_units"], "0.00000000")

    def test_rounding_is_per_holder_not_aggregate(self):
        separate = sum(convert(2)["theoretical_post_registered_units"] for _ in range(2))
        aggregate = convert(4)["theoretical_post_registered_units"]
        self.assertEqual((separate, aggregate), (0, 1))

    def test_rejects_non_integer_negative_and_bool(self):
        for shares in (-1, 0.5, "100", True, None):
            with self.subTest(shares=shares), self.assertRaises(ValueError):
                convert(shares)

    def test_rejects_other_event_or_rounding_rule(self):
        for change in (
            {"symbol": "512100.SH"},
            {"event_date": "2015-04-13"},
            {"published_ratio": "0.28032484"},
            {"rounding_rule": "CEILING_TO_WHOLE_SHARE_MANAGER_FUNDED"},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                convert(100, **change)

    def test_no_cash_or_return_certification(self):
        result = convert(100)
        self.assertIsNone(result["cash_compensation_cny"])
        self.assertEqual(result["status"], "REVIEW_REQUIRED_NOT_FOR_TRADING")

    def test_large_integer_arithmetic_exact(self):
        result = convert(10**18)
        self.assertEqual(result["theoretical_post_registered_units"], 280324830000000000)
        self.assertEqual(result["theoretical_discarded_fractional_units"], "0.00000000")


if __name__ == "__main__":
    unittest.main()
