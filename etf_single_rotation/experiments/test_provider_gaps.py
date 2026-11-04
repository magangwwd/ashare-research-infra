"""Provider gap diagnostics must never infer dates from neighboring quotes."""
import unittest
import pandas as pd

from etf_single_rotation.experiments.verify_provider_gaps import rows_on_date


class ProviderGapRecheckTests(unittest.TestCase):
    def test_exact_date_only(self):
        provider = pd.DataFrame({
            "trade_date": ["20210205", "20210209", "20210210"],
            "close": [1.1, 1.2, 1.3]
        })
        self.assertEqual(rows_on_date(provider, "2021-02-08"), 0)
        self.assertEqual(rows_on_date(provider, "2021-02-09"), 1)

    def test_empty_provider_response_is_missing_not_verified_suspension(self):
        self.assertEqual(rows_on_date(None, "2022-09-02"), 0)
        self.assertEqual(rows_on_date(pd.DataFrame(), "2022-09-02"), 0)


if __name__ == "__main__":
    unittest.main()
