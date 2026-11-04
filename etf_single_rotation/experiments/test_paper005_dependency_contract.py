"""Offline fail-closed checks for the ETF PAPER-005 research backlog.

Research-only: this test never downloads prices, submits orders or unlocks a task.
"""
import json
import unittest
from pathlib import Path


BACKLOG = Path(__file__).resolve().parents[1] / "research_backlog.json"
ACTIVE = {"READY", "CLAIMED", "RUNNING", "VERIFYING", "VERIFIED_DONE"}
REQUIRED_PAPER_DEPENDENCIES = {"ETF-DATA-003", "ETF-DIV-004"}


def _unique_tasks(payload):
    """Reject ambiguous task IDs before looking up dependency status."""
    tasks = {}
    for task in payload["tasks"]:
        task_id = task["id"]
        if task_id in tasks:
            raise AssertionError(f"Duplicate task ID: {task_id}")
        tasks[task_id] = task
    return tasks


def validate_paper005_dependency_gate(payload):
    tasks = _unique_tasks(payload)
    paper = tasks["ETF-PAPER-005"]
    dependencies = paper.get("depends_on")
    if (not isinstance(dependencies, list)
            or len(dependencies) != len(REQUIRED_PAPER_DEPENDENCIES)
            or set(dependencies) != REQUIRED_PAPER_DEPENDENCIES):
        raise AssertionError("PAPER-005 must retain both DATA-003 and DIV-004 dependencies")
    unmet = [task_id for task_id in dependencies
             if tasks.get(task_id, {}).get("status") != "VERIFIED_DONE"]
    if unmet and paper["status"] != "BLOCKED":
        raise AssertionError(
            "PAPER-005 must be BLOCKED until every dependency is VERIFIED_DONE: "
            + ", ".join(unmet)
        )
    return unmet


def validate_all_active_dependencies(payload):
    """No active task may rely on an unaccepted or missing prerequisite."""
    # The general gate must also reject removal of a required PAPER-005 edge.
    validate_paper005_dependency_gate(payload)
    tasks = _unique_tasks(payload)
    for task in payload["tasks"]:
        if task["status"] not in ACTIVE:
            continue
        unmet = [dependency for dependency in task.get("depends_on", [])
                 if tasks.get(dependency, {}).get("status") != "VERIFIED_DONE"]
        if unmet:
            raise AssertionError(
                f'{task["id"]} is active with unmet dependencies: {", ".join(unmet)}'
            )


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

    def test_dividend_acceptance_alone_cannot_unlock_unaccepted_data(self):
        """Both prerequisites need VERIFIED_DONE, not only DIV-004."""
        import copy
        scenario = copy.deepcopy(self.backlog)
        tasks = {task["id"]: task for task in scenario["tasks"]}
        tasks["ETF-DIV-004"]["status"] = "VERIFIED_DONE"
        self.assertEqual(tasks["ETF-DATA-003"]["status"], "VERIFIED_RESEARCH_COMPLETE")
        unmet = validate_paper005_dependency_gate(scenario)
        self.assertEqual(unmet, ["ETF-DATA-003"])
        tasks["ETF-PAPER-005"]["status"] = "READY"
        with self.assertRaises(AssertionError):
            validate_paper005_dependency_gate(scenario)
        with self.assertRaises(AssertionError):
            validate_all_active_dependencies(scenario)

    def test_no_active_task_has_unverified_dependencies(self):
        validate_all_active_dependencies(self.backlog)

    def test_any_active_task_with_unverified_or_missing_dependency_fails(self):
        import copy
        for dependency in ("ETF-DIV-004", "ETF-MISSING-999"):
            with self.subTest(dependency=dependency):
                bad = copy.deepcopy(self.backlog)
                next(t for t in bad["tasks"] if t["id"] == "ETF-PAPER-005")["status"] = "READY"
                next(t for t in bad["tasks"] if t["id"] == "ETF-PAPER-005")["depends_on"] = [dependency]
                with self.assertRaises(AssertionError):
                    validate_all_active_dependencies(bad)

    def test_duplicate_ids_cannot_fake_dependency_acceptance(self):
        """A later VERIFIED_DONE duplicate must not shadow REVIEW_REQUIRED."""
        import copy
        for duplicate_id in ("ETF-DIV-004", "ETF-PAPER-005"):
            with self.subTest(duplicate_id=duplicate_id):
                bad = copy.deepcopy(self.backlog)
                forged = copy.deepcopy(next(
                    t for t in bad["tasks"] if t["id"] == duplicate_id
                ))
                forged["status"] = "VERIFIED_DONE"
                bad["tasks"].append(forged)
                next(t for t in bad["tasks"] if t["id"] == "ETF-PAPER-005")["status"] = "READY"
                with self.assertRaisesRegex(AssertionError, "Duplicate task ID"):
                    validate_paper005_dependency_gate(bad)
                with self.assertRaisesRegex(AssertionError, "Duplicate task ID"):
                    validate_all_active_dependencies(bad)

    def test_required_dependencies_cannot_be_removed_or_duplicated(self):
        """A forged VERIFIED_DONE on a remaining edge cannot erase another edge."""
        import copy
        for dependencies in (
            ["ETF-DATA-003"],
            ["ETF-DIV-004"],
            ["ETF-DATA-003", "ETF-DATA-003"],
            [],
        ):
            with self.subTest(dependencies=dependencies):
                bad = copy.deepcopy(self.backlog)
                tasks = {task["id"]: task for task in bad["tasks"]}
                tasks["ETF-DATA-003"]["status"] = "VERIFIED_DONE"
                tasks["ETF-DIV-004"]["status"] = "VERIFIED_DONE"
                tasks["ETF-PAPER-005"]["depends_on"] = dependencies
                tasks["ETF-PAPER-005"]["status"] = "READY"
                with self.assertRaisesRegex(AssertionError, "must retain both"):
                    validate_paper005_dependency_gate(bad)
                with self.assertRaisesRegex(AssertionError, "must retain both"):
                    validate_all_active_dependencies(bad)

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
