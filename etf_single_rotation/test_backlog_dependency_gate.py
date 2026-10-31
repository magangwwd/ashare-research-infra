"""Offline, no-network regression tests for the ETF dependency gate."""
import copy
import json
import unittest

from etf_single_rotation.backlog_guard import QUEUE, validate_dependencies


class ETFBacklogDependencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backlog = json.loads(QUEUE.read_text(encoding="utf-8"))

    def test_current_queue_is_fail_closed(self):
        self.assertTrue(validate_dependencies(self.backlog))
        by_id = {task["id"]: task for task in self.backlog["tasks"]}
        self.assertEqual(by_id["ETF-DIV-004"]["status"], "REVIEW_REQUIRED")
        self.assertEqual(by_id["ETF-PAPER-005"]["status"], "BLOCKED")

    def test_ready_paper_rejected_while_dividend_unverified(self):
        data = copy.deepcopy(self.backlog)
        by_id = {task["id"]: task for task in data["tasks"]}
        by_id["ETF-BENCH-002"]["status"] = "VERIFIED_DONE"
        by_id["ETF-DATA-003"]["status"] = "VERIFIED_DONE"
        by_id["ETF-PAPER-005"]["status"] = "READY"
        with self.assertRaisesRegex(ValueError, "ETF-DIV-004"):
            validate_dependencies(data)

    def test_legacy_research_complete_does_not_unlock(self):
        data = copy.deepcopy(self.backlog)
        by_id = {task["id"]: task for task in data["tasks"]}
        by_id["ETF-BENCH-002"]["status"] = "VERIFIED_DONE"
        by_id["ETF-DATA-003"]["status"] = "VERIFIED_DONE"
        by_id["ETF-PAPER-005"]["status"] = "READY"
        by_id["ETF-DIV-004"]["status"] = "VERIFIED_RESEARCH_COMPLETE"
        with self.assertRaisesRegex(ValueError, "not VERIFIED_DONE"):
            validate_dependencies(data)

    def test_ready_requires_all_formally_verified(self):
        data = copy.deepcopy(self.backlog)
        by_id = {task["id"]: task for task in data["tasks"]}
        by_id["ETF-BENCH-002"]["status"] = "VERIFIED_DONE"
        by_id["ETF-BENCH-002"]["status"] = "VERIFIED_DONE"
        by_id["ETF-DATA-003"]["status"] = "VERIFIED_DONE"
        by_id["ETF-DIV-004"]["status"] = "VERIFIED_DONE"
        by_id["ETF-PAPER-005"]["status"] = "READY"
        self.assertTrue(validate_dependencies(data))

    def test_missing_dependency_rejected(self):
        data = copy.deepcopy(self.backlog)
        by_id = {task["id"]: task for task in data["tasks"]}
        by_id["ETF-PAPER-005"]["status"] = "READY"
        by_id["ETF-PAPER-005"]["depends_on"] = ["ETF-UNKNOWN-999"]
        with self.assertRaisesRegex(ValueError, "missing dependency"):
            validate_dependencies(data)

    def test_fixed_mandate_unchanged(self):
        mandate = self.backlog["mandate"]
        self.assertEqual(mandate["symbol_count"], 8)
        self.assertEqual(mandate["max_simultaneous_etf_positions"], 1)
        self.assertTrue(mandate["may_hold_cash"])
        self.assertEqual(mandate["historical_max_drawdown_target"], 0.25)
        self.assertFalse(mandate["real_trading"])
        self.assertFalse(mandate["github_hourly_cron"])


if __name__ == "__main__":
    unittest.main()
