"""Regression checks for missing, future and conflicting name/ST disclosures."""
import unittest

import numpy as np
import pandas as pd

from pit_name_states import historical_name_states


class PITNameStateTests(unittest.TestCase):
    def panel(self):
        return pd.DataFrame({"instrument_id": ["A", "A", "B"],
            "trade_date": pd.to_datetime(["2024-01-03", "2024-01-10", "2024-01-03"])}, index=[7, 3, 9])

    def history(self, flag=False, announcement="2024-01-01"):
        return pd.DataFrame({"instrument_id": ["A"], "effective_date": ["2024-01-01"],
            "end_date": [None], "announcement_date": [announcement], "is_st": [flag]})

    def test_normal_and_missing_symbol(self):
        out = historical_name_states(self.panel(), self.history())
        self.assertEqual(out.index.tolist(), [7, 3, 9])
        self.assertEqual(out.historical_state_known.tolist(), [True, True, False])
        self.assertEqual(out.historical_is_st.tolist(), [False, False, False])

    def test_null_st_is_unknown(self):
        out = historical_name_states(self.panel(), self.history(pd.NA))
        self.assertFalse(out.historical_state_known.any())

    def test_future_announcement_becomes_visible_only_later(self):
        out = historical_name_states(self.panel(), self.history(False, "2024-01-10"))
        self.assertEqual(out.historical_state_known.tolist(), [False, True, False])

    def test_unknown_announcement_is_unknown(self):
        self.assertFalse(historical_name_states(self.panel(), self.history(False, None)).historical_state_known.any())

    def test_st_record_and_interval_end(self):
        history = self.history(True)
        history["end_date"] = "2024-01-05"
        out = historical_name_states(self.panel(), history)
        self.assertEqual(out.historical_state_known.tolist(), [True, False, False])
        self.assertEqual(out.historical_is_st.tolist(), [True, False, False])

    def test_conflicting_equal_effective_dates_are_unknown(self):
        history = pd.concat([self.history(False), self.history(True)], ignore_index=True)
        for shuffled in (history, history.iloc[::-1]):
            self.assertFalse(historical_name_states(self.panel(), shuffled).historical_state_known.any())

    def test_missing_equal_date_flag_cannot_be_overridden(self):
        history = pd.concat([self.history(False), self.history(pd.NA)], ignore_index=True)
        for shuffled in (history, history.iloc[::-1]):
            self.assertFalse(historical_name_states(self.panel(), shuffled).historical_state_known.any())

    def test_latest_effective_unknown_does_not_fall_back_to_normal(self):
        second = self.history(pd.NA)
        second["effective_date"] = "2024-01-05"
        second["announcement_date"] = "2024-01-05"
        history = pd.concat([self.history(False), second], ignore_index=True)
        out = historical_name_states(self.panel(), history)
        self.assertEqual(out.historical_state_known.tolist(), [True, False, False])

    def test_append_future_records_preserves_past_result(self):
        before = historical_name_states(self.panel(), self.history(False))
        future = self.history(True, "2024-02-01")
        future["effective_date"] = "2024-02-01"
        after = historical_name_states(self.panel(), pd.concat([self.history(False), future], ignore_index=True))
        pd.testing.assert_frame_equal(before, after)

    def test_missing_announcement_column_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "announcement_date"):
            historical_name_states(self.panel(), self.history().drop(columns="announcement_date"))


if __name__ == "__main__":
    unittest.main()
