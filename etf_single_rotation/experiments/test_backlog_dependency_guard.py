"""Synthetic regression tests for fail-closed ETF dependency and mandate gate."""
import copy
import json
import unittest
from pathlib import Path

from etf_single_rotation.experiments.backlog_dependency_guard import check_backlog


BACKLOG = Path(__file__).resolve().parents[1] / "research_backlog.json"


class BacklogDependencyGuardTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(BACKLOG.read_text(encoding="utf-8"))

    def task(self, task_id):
        return next(t for t in self.data["tasks"] if t["id"] == task_id)

    def test_pr_queue_remains_safe_and_nontrading(self):
        self.assertEqual(check_backlog(self.data), [])
        self.assertEqual(self.task("ETF-PAPER-005")["status"], "BLOCKED")

    def test_stale_ready_paper_is_rejected(self):
        self.task("ETF-PAPER-005")["status"] = "READY"
        self.assertTrue(any("ETF-DIV-004" in e for e in check_backlog(self.data)))

    def test_claimed_paper_also_rejected(self):
        self.task("ETF-PAPER-005")["status"] = "CLAIMED"
        self.assertTrue(any("ETF-DIV-004" in e for e in check_backlog(self.data)))

    def test_research_complete_is_not_verified_done(self):
        self.task("ETF-DIV-004")["status"] = "VERIFIED_RESEARCH_COMPLETE"
        self.task("ETF-PAPER-005")["status"] = "READY"
        self.assertTrue(any("ETF-DIV-004" in e for e in check_backlog(self.data)))

    def test_only_all_verified_done_dependencies_unlock(self):
        self.task("ETF-DATA-003")["status"] = "VERIFIED_DONE"
        self.task("ETF-DIV-004")["status"] = "VERIFIED_DONE"
        self.task("ETF-PAPER-005")["status"] = "READY"
        self.assertEqual(check_backlog(self.data), [])

    def test_unknown_dependency_is_rejected(self):
        self.task("ETF-PAPER-005")["depends_on"].append("UNKNOWN-999")
        self.assertTrue(any("UNKNOWN-999" in e for e in check_backlog(self.data)))

    def test_duplicate_task_id_is_rejected(self):
        self.data["tasks"].append(copy.deepcopy(self.task("ETF-DIV-004")))
        self.assertTrue(any("Duplicate" in e for e in check_backlog(self.data)))

    def test_mandate_changes_fail_closed(self):
        self.data["mandate"]["symbol_count"] = 9
        self.data["mandate"]["real_trading"] = True
        self.data["mandate"]["github_hourly_cron"] = True
        issues = check_backlog(self.data)
        self.assertTrue(any("symbol_count" in e for e in issues))
        self.assertTrue(any("real_trading" in e for e in issues))
        self.assertTrue(any("github_hourly_cron" in e for e in issues))

    def test_trading_status_cannot_be_relaxed(self):
        self.data["last_known_research_status"] = "READY_FOR_TRADING"
        self.assertTrue(any("NOT_FOR_TRADING" in e for e in check_backlog(self.data)))


if __name__ == "__main__":
    unittest.main()
