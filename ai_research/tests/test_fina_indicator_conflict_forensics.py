"""No-network source-duplicate evidence classifier regression."""
import unittest
from datetime import datetime, timezone

from ai_research.fina_indicator_conflict_forensics import analyze_frame
import pandas as pd


class IndicatorForensicTests(unittest.TestCase):
    def base(self):
        return dict(ts_code="600641.SH", end_date="20260331", ann_date="20260430",
                    f_ann_date="20260430", update_flag="1", roe=2.5, eps=.25, 
                    dt_eps=.21, profit_dedt=10000, fcff=800, debt_to_assets=35,
                    current_ratio=2.1)

    def analyze(self, variants):
        return analyze_frame(pd.DataFrame(variants), "600641.SH", "20260331",
                             "2026-10-12T08:00:00+00:00")

    def test_irrelevant_fcf_conflict_does_not_become_clean_conflict(self):
        a=self.base()
        b=dict(a, fcff=999)
        r=self.analyze([a,b])
        self.assertEqual(r["status"],"OUT_OF_SCHEMA_CONFLICT_ONLY")
        self.assertIn("fcff",r["conflicts_all_source_fields"])
        self.assertFalse(r["conflicts_in_clean_projection"])
        self.assertFalse(r["release_to_clean"])

    def test_eps_conflict_is_material_and_never_promoted(self):
        a=self.base()
        b=dict(a, eps=.36, profit_dedt=12000)
        r=self.analyze([a,b])
        self.assertEqual(r["status"],"MATERIAL_CLEAN_FIELD_CONFLICT")
        self.assertIn("eps",r["conflicts_in_clean_projection"])
        self.assertIn("profit_dedt",r["conflicts_in_clean_projection"])
        self.assertFalse(r["release_to_clean"])

    def test_revision_dates_are_not_co_mingled(self):
        a=self.base()
        b=dict(a, ann_date="20260502", update_flag="2", eps=.35)
        r=self.analyze([a,b])
        self.assertEqual(r["status"],"LATEST_REVISION_SINGLE_ROW")
        self.assertEqual(r["same_key_rows"],1)

    def test_invalid_future_dated_record_is_not_used(self):
        a=self.base()
        b=dict(a, ann_date="20261013", eps=.45)
        r=self.analyze([a,b])
        self.assertEqual(r["status"],"LATEST_REVISION_SINGLE_ROW")
        self.assertEqual(r["raw_rows"],1)


if __name__ == "__main__":
    unittest.main()
