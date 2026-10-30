"""Past state eligibility must not change when future disclosures are added."""
import unittest

import pandas as pd


def fixtures():
    recent = pd.DataFrame({"vendor_symbol": ["600000.SH"] * 3,
        "trade_date": pd.to_datetime(["2023-12-01", "2024-02-01", "2024-03-01"])})
    events = pd.DataFrame({
        "vendor_symbol": ["600000.SH", "600000.SH"],
        "ann_date": pd.to_datetime(["2024-01-01", "2024-03-01"]),
        "new_holder_disclosure": [True, True], "new_composition_disclosure": [True, True],
        "holder_report_end": pd.to_datetime(["2023-12-31", "2024-02-29"]),
        "holder_num": [10000, 9000], "holder_num_change_rate": [0.0, -0.1],
        "composition_report_end": pd.to_datetime(["2023-12-31", "2024-02-29"]),
        "top10_float_pct": [20.0, 25.0], "top10_hhi": [0.1, 0.2],
        "top10_float_change_pp": [0.0, 5.0], "composition_quality_valid": [True, True],
        "classification_coverage_pct": [100.0, 100.0], "identified_institution_pct": [20.0, 25.0],
    })
    return recent, events


class DisclosureCountPITTests(unittest.TestCase):
    def test_each_row_counts_only_already_announced_updates(self):
        from state_definition_pool import _attach_disclosures
        recent, events = fixtures()
        out = _attach_disclosures(recent, events)
        self.assertEqual(out.holder_update_count.tolist(), [0, 1, 2])
        self.assertEqual(out.composition_update_count.tolist(), [0, 1, 2])

    def test_appending_future_announcements_never_changes_past_rows(self):
        from state_definition_pool import _attach_disclosures
        recent, events = fixtures()
        recent = recent[recent.trade_date < pd.Timestamp("2024-03-01")]
        before = _attach_disclosures(recent, events.iloc[:1])
        after = _attach_disclosures(recent, events)
        pd.testing.assert_frame_equal(before, after)

    def test_duplicate_same_day_disclosure_is_not_a_second_update(self):
        from state_definition_pool import _attach_disclosures
        recent, events = fixtures()
        out = _attach_disclosures(recent, pd.concat([events, events], ignore_index=True))
        self.assertEqual(out.holder_update_count.tolist(), [0, 1, 2])
        self.assertEqual(out.composition_update_count.tolist(), [0, 1, 2])


if __name__ == "__main__":
    unittest.main()
