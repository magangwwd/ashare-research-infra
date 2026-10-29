"""Static fail-closed BTC Actions ref-boundary regression tests (no secrets)."""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows"
JOBS = {
    "btc-v3-watchdog.yml": "monitor",
    "btc-hourly-research-cycle.yml": "autonomous-research",
    "btc-okx-freshness-watchdog.yml": "check",
    "btc-okx-market-archive.yml": "capture",
    "btc-okx-l2-schema-audit.yml": "audit",
    "btc-okx-official-catalog.yml": "probe",
    "btc-okx-official-l2-pilot.yml": "archive-one-day",
    "btc-regime-router.yml": "daily-shadow",
}


class BtcWorkflowRefSafety(unittest.TestCase):
    def test_private_or_privileged_jobs_are_main_only(self):
        for name, job in JOBS.items():
            with self.subTest(workflow=name):
                text = (ROOT / name).read_text(encoding="utf-8")
                self.assertIn("jobs:\n", text)
                job_block = text.split("\n  " + job + ":\n", 1)[1]
                job_head = job_block.split("\n    steps:", 1)[0]
                self.assertIn("github.ref == 'refs/heads/main'", job_head)

    def test_no_feature_branch_push_for_private_workflows(self):
        for name in JOBS:
            with self.subTest(workflow=name):
                text = (ROOT / name).read_text(encoding="utf-8")
                trigger = text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
                if "\n  push:\n" in trigger:
                    match = re.search(r"(?ms)^  push:\\n(.*?)(?=^  [a-z_]+:|\\Z)", trigger)
                    self.assertIsNotNone(match)
                    push = match.group(1)
                    self.assertIn("branches: [main]", push)
                    self.assertNotIn("feature/", push)
                self.assertNotIn("pull_request_target:", trigger)

    def test_regime_router_pr_runs_only_offline_tests(self):
        text = (ROOT / "btc-regime-router.yml").read_text(encoding="utf-8")
        unit = text.split("\n  unit-tests:\n", 1)[1].split("\n  daily-shadow:\n", 1)[0]
        self.assertNotIn("secrets.", unit)
        self.assertNotIn("rclone", unit)
        self.assertIn("github.event_name != 'pull_request'", text)


if __name__ == "__main__":
    unittest.main()
