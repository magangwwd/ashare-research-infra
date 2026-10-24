"""Offline fail-closed checks for the ETF PAPER-005 research backlog.

Research-only: this test never downloads prices, submits orders or unlocks a task.
"""
import json
import unittest
from pathlib import Path


BACKLOG = Path(__file__).resolve().parents[1] / "research_backlog.json"
ACTIVE = {"READY", "CLAIMED", "RUNNING", "VERIFYING", "VERIFIED_DONE"}


def validate_paper005_dependency_gate(payload):
    tasks = {task["id"]: task for task in payload["tasks"]}
    paper = tasks["ETF-PAPER-005"]
    dependencies = paper["depends_on"]
    if not dependencies:
        raise AssertionError("PAPER-005 must retain explicit dependencies")
    unmet = [task_id for task_id in dependencies
             if tasks.get(task_id, {}).get("status") != "VERIFIED_DONE"]
    if unmet and paper["status"] != "BLOCKED":
        raise AssertionError(
            "PAPER-005 must be BLOCKED until every dependency is VERIFIED_DONE: "
            + ", ".join(unmet)
        )
    return unmet


class Paper005DependencyContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backlog = json.loads(BACKLOG.read_text(encoding="utf-8"))

    def test_authoritative_queue_fail_closed(self):
        unmet = validate_paper005_dependency_gate(self.backlog)
        self.assertIn("ETF-DIV-004", unmet)
        tasks = {task["id"]: task for task in self.backlog["tasks"]}
        self.assertEqual(tasks["ETF-PAPER-005"]["status"], "BLOCKED")

    def test_ready_with_unverified_dividend_is_rejected(self):
        import copy
        bad = copy.deepcopy(self.backlog)
        next(t for t in bad["tasks"] if t["id"] == "ETF-PAPER-005")["status"] = "READY"
        with self.assertRaises(AssertionError):
            validate_paper005_dependency_gate(bad)

    def test_research_complete_is_not_accepted_as_verified_done(self):
        import copy
        bad = copy.deepcopy(self.backlog)
        for task in bad["tasks"]:
            if task["id"] == "ETF-DIV-004":
                task["status"] = "VERIFIED_RESEARCH_COMPLETE"
            if task["id"] == "ETF-PAPER-005":
                task["status"] = "READY"
        with self.assertRaises(AssertionError):
            validate_paper005_dependency_gate(bad)

    def test_mandate_remains_frozen_and_nontrading(self):
        m = self.backlog["mandate"]
        self.assertEqual(m["symbol_count"], 8)
        self.assertEqual(m["max_simultaneous_etf_positions"], 1)
        self.assertIs(m["may_hold_cash"], True)
        self.assertEqual(m["historical_max_drawdown_target"], 0.25)
        self.assertIs(m["real_trading"], False)
        self.assertIs(m["github_hourly_cron"], False)
        self.assertEqual(m["gpt_daily_review_beijing"], "08:00")


if __name__ == "__main__":
    unittest.main()
