"""Causal forward-paper checks, with no future session or connected service."""
import unittest
import pandas as pd
from paper_forward_r0 import verified_as_of,snapshot
from gold_pit_risk_guards import GoldPITRisk
from collections import defaultdict

class ForwardPaperTests(unittest.TestCase):
    def test_cannot_backdate_pre_inspection_prices(self):
        with self.assertRaises(RuntimeError):
            verified_as_of({"as_of_date":"2026-10-09","validation":{"primary_keys_unique":True}},
                           pd.Timestamp("2026-10-09"))
    def test_cannot_use_stale_or_future_manifests(self):
        for source in ("2026-10-09","2026-10-13"):
            with self.subTest(source=source),self.assertRaises(RuntimeError):
                verified_as_of({"as_of_date":source,"validation":{"primary_keys_unique":True}},
                    pd.Timestamp("2026-10-12"))
    def test_requires_manifest_integrity(self):
        with self.assertRaises(RuntimeError):
            verified_as_of({"as_of_date":"2026-10-12","validation":{}},
                pd.Timestamp("2026-10-12"))
    def test_today_date_is_necessary_but_not_sufficient(self):
        verified_as_of({"as_of_date":"2026-10-12","validation":{"primary_keys_unique":True}},
            pd.Timestamp("2026-10-12"))

if __name__=="__main__": unittest.main()
