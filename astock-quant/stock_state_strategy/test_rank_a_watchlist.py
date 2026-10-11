"""Offline checks for the paper-only research ranker."""
import csv
import tempfile
import unittest
from pathlib import Path

from rank_a_watchlist import score_row, load_ranked


def sample(code="002107.SZ", age=80, conf=88, stable=5, turnover=200000000):
    return {
        "vendor_symbol": code, "name_zh": "测试", "trade_date": "2026-10-09",
        "pool_status": "A_STRATEGY_CANDIDATE", "state_cn": "拉升",
        "confidence": str(conf), "stable_days_5": str(stable),
        "state_margin": "18", "state_score": "80",
        "average_turnover_cny_20": str(turnover),
        "holder_disclosure_age_days": str(age),
        "composition_disclosure_age_days": str(age),
    }


class PaperResearchRanking(unittest.TestCase):
    def test_p1_needs_both_sources_recent(self):
        row = sample(age=120)
        self.assertTrue(score_row(row)["priority_tier"].startswith("P1"))
        row["composition_disclosure_age_days"] = "121"
        self.assertTrue(score_row(row)["priority_tier"].startswith("P2"))

    def test_quality_score_monotonic(self):
        lo = score_row(sample(conf=80, stable=3, turnover=100000000))
        hi = score_row(sample(conf=90, stable=5, turnover=2000000000))
        self.assertGreater(hi["research_priority_score"], lo["research_priority_score"])

    def test_non_A_fails(self):
        row = sample()
        row["pool_status"] = "B_MONITOR_ONLY"
        with self.assertRaises(ValueError):
            score_row(row)

    def test_wrong_date_and_duplicates_fail(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "input.csv"
            keys = list(sample())
            with file.open("w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=keys)
                w.writeheader()
                w.writerow(sample())
            self.assertEqual(len(load_ranked(file, "2026-10-09")), 1)
            with self.assertRaises(ValueError):
                load_ranked(file, "2026-10-08")
            with file.open("a", encoding="utf-8", newline="") as f:
                csv.DictWriter(f, fieldnames=keys).writerow(sample())
            with self.assertRaises(ValueError):
                load_ranked(file, "2026-10-09")


if __name__ == "__main__":
    unittest.main()
