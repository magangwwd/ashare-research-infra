"""Deterministic regression tests for past-only walk-forward reporting."""
import tempfile
import unittest
from pathlib import Path
import numpy as np
import pandas as pd
from etf_single_rotation.experiments.walk_forward import (
    select_on_history, walk_forward,
)


class WalkForwardTests(unittest.TestCase):
    def make_equities(self):
        idx = pd.bdate_range("2015-01-01", "2026-10-01")
        t = np.arange(len(idx))
        return idx, {
            "A": pd.Series(1.001 ** t, index=idx),
            "B": pd.Series(1.0002 ** t, index=idx),
        }

    def test_future_cannot_change_training_selection(self):
        dates, eq = self.make_equities()
        choice, _ = select_on_history(eq, "2018-12-31", candidates=("A", "B"))
        self.assertEqual(choice, "A")
        modified = {key: series.copy() for key, series in eq.items()}
        modified["A"].loc["2019-01-01":] *= 0.01
        modified["B"].loc["2019-01-01":] *= 100.0
        altered_choice, _ = select_on_history(modified, "2018-12-31", candidates=("A", "B"))
        self.assertEqual(altered_choice, choice)

    def test_reject_bad_training_candidates_into_cash(self):
        idx = pd.bdate_range("2015-01-01", periods=1200)
        dropping = pd.Series(0.999 ** np.arange(len(idx)), index=idx)
        choice, evidence = select_on_history({"declining": dropping}, "2018-12-31", ("declining",))
        self.assertEqual(choice, "CASH")
        self.assertFalse(evidence[0]["eligible"])

    def test_nonoverlap_and_stitched_holdout(self):
        _, eq = self.make_equities()
        with tempfile.TemporaryDirectory() as temp:
            report = walk_forward(eq, Path(temp), candidates=("A", "B"))
            self.assertEqual(report["fold_count"], 3)
            self.assertTrue(all(x["selected_strategy"] == "A" for x in report["folds"]))
            curve = pd.read_csv(Path(temp) / "walk_forward_portfolio_equity.csv")
            self.assertFalse(curve.date.duplicated().any())
            self.assertGreater(float(curve.net_equity.iloc[-1]), 1.0)
            self.assertAlmostEqual(
                float(curve.net_equity.iloc[-1]) - 1,
                report["stitched_net_holdout"]["total_return"],
                places=10,
            )
            self.assertEqual(report["acceptance"], "RETROSPECTIVE_EXPLORATORY_NOT_FOR_TRADING")


if __name__ == "__main__":
    unittest.main()
