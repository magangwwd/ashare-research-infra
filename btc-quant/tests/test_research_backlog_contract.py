"""Fail-closed contract checks for the isolated BTC autonomous research backlog.

This is research governance only; it neither dispatches Actions nor trades.
"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKLOG = ROOT / "btc-quant" / "ops" / "research_backlog.json"
SCHEMA = ROOT / "docs" / "autonomous-rd" / "handoff_receipt.schema.json"
VALID = {
    "NEW", "READY", "CLAIMED", "RUNNING", "VERIFYING",
    "REVIEW_REQUIRED", "VERIFIED_DONE", "RETRYABLE_FAILED",
    "BLOCKED", "CANCELLED",
}
FROZEN = "d60c667b3654dbfb00efea9bc72241cca3bb8d5c"


class BTCBacklogContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backlog = json.loads(BACKLOG.read_text(encoding="utf-8"))
        cls.receipt_schema = json.loads(SCHEMA.read_text(encoding="utf-8"))

    def test_frozen_research_only_mandate(self):
        b = self.backlog
        self.assertEqual(b["schema_version"], "1.0.0")
        self.assertEqual(b["standard_id"], "AUTO-RD-SOP-001")
        m = b["mandate"]
        self.assertIs(m["real_trading"], False)
        self.assertIs(m["live_orders_prohibited"], True)
        self.assertIs(m["source_data_immutable"], True)
        self.assertEqual(m["frozen_v3_commit"], FROZEN)
        self.assertEqual(m["max_auto_risk"], "LOW")
        self.assertEqual(b["governance"]["max_in_flight_tasks"], 1)
        for action in ("LIVE_TRADING", "OVERRIDE_FROZEN_RULES",
                       "WEAKEN_RISK_CONTROL", "DELETE_SOURCE_DATA"):
            self.assertIn(action, b["governance"]["approval_required"])

    def test_unique_ids_valid_statuses_and_reviewable_acceptance(self):
        tasks = self.backlog["tasks"]
        self.assertGreater(len(tasks), 0)
        ids = [t["id"] for t in tasks]
        self.assertEqual(len(ids), len(set(ids)), "duplicate task ID")
        for t in tasks:
            with self.subTest(task=t["id"]):
                self.assertRegex(t["id"], r"^BTC-[A-Z0-9-]+$")
                self.assertIn(t["status"], VALID)
                self.assertEqual(t["risk_tier"], "LOW")
                self.assertIsInstance(t["priority"], int)
                self.assertGreaterEqual(t["priority"], 0)
                self.assertTrue(t["acceptance"])
                self.assertTrue(t["next_action"].strip())
                self.assertGreaterEqual(t["version"], 1)
                self.assertTrue(all(e.startswith("https://") for e in t["evidence"]))

    def test_dependencies_exist_acyclic_and_done_only_unlocks(self):
        tasks = {t["id"]: t for t in self.backlog["tasks"]}
        for task in tasks.values():
            self.assertNotIn(task["id"], task["depends_on"])
            self.assertEqual(len(task["depends_on"]), len(set(task["depends_on"])))
            for dependency in task["depends_on"]:
                self.assertIn(dependency, tasks)
            if task["status"] in ("READY", "CLAIMED", "RUNNING",
                                  "VERIFYING", "REVIEW_REQUIRED", "VERIFIED_DONE"):
                for dependency in task["depends_on"]:
                    self.assertEqual(tasks[dependency]["status"], "VERIFIED_DONE",
                                     "unfinished dependency cannot unlock a task")
        seen, visiting = set(), set()
        def visit(task_id):
            if task_id in seen:
                return
            if task_id in visiting:
                raise AssertionError("dependency cycle at " + task_id)
            visiting.add(task_id)
            for dep in tasks[task_id]["depends_on"]:
                visit(dep)
            visiting.remove(task_id)
            seen.add(task_id)
        for task_id in tasks:
            visit(task_id)

    def test_no_unverified_completion_claims(self):
        for t in self.backlog["tasks"]:
            if t["status"] == "VERIFIED_DONE":
                self.fail("Require independent receipt validator before VERIFIED_DONE: " + t["id"])
        self.assertEqual(self.receipt_schema["properties"]["risk_approval"]["enum"][0],
                         "RESEARCH_ONLY")
        self.assertIn("run_attempt", self.receipt_schema["required"])
        self.assertIn("drive_readback", self.receipt_schema["required"])
        self.assertIn("head_sha", self.receipt_schema["required"])


if __name__ == "__main__":
    unittest.main()
