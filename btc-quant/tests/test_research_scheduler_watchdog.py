"""Missing hourly scheduled run must be independently detected, safely deduped."""
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))
from research_scheduler_watchdog import decide

NOW=datetime(2026,10,21,12,0,tzinfo=timezone.utc)
def run(created,event="schedule",branch="main"):
    return {"id":1,"head_branch":branch,"event":event,"created_at":created}

class SchedulerWatchdogTests(unittest.TestCase):
    def test_fresh_schedule_never_dispatches_duplicate(self):
        d=decide([run("2026-10-21T11:20:00Z")],NOW)
        self.assertFalse(d["should_dispatch"])
        self.assertTrue(d["scheduled_run_seen"])

    def test_missing_cron_after_two_hours_triggers_fallback(self):
        d=decide([run("2026-10-21T08:10:00Z","push")],NOW)
        self.assertEqual(d["status"],"MISSING_RECENT_CRON_AND_FALLBACK")
        self.assertTrue(d["should_dispatch"])

    def test_recent_main_push_prevents_race(self):
        d=decide([run("2026-10-21T11:20:00Z","push")],NOW)
        self.assertFalse(d["should_dispatch"])

    def test_recent_dispatch_deduplicates_fallback(self):
        d=decide([run("2026-10-21T11:20:00Z","workflow_dispatch")],NOW)
        self.assertFalse(d["should_dispatch"])
        self.assertEqual(d["status"],"HEALTHY_RECENT_CRON_OR_FALLBACK")
        self.assertFalse(d["scheduled_run_seen"])

    def test_recovery_trigger_sources_and_original_cron_are_explicit(self):
        """Guard must wake on independent BTC completions without adding cron."""
        workflow = (Path(__file__).resolve().parents[2] /
                    ".github/workflows/btc-research-scheduler-guard.yml").read_text()
        trigger = workflow.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
        workflow_run = trigger.split("  workflow_run:\n", 1)[1].split("  schedule:\n", 1)[0]
        for source in (
            "BTC V3 progress watchdog",
            "BTC Regime Router - daily shadow research",
            "BTC V3 frozen daily forward shadow paper (NO REAL ORDERS)",
        ):
            self.assertIn(source, workflow_run)
        self.assertIn("types: [completed]", workflow_run)
        self.assertIn("branches: [main]", workflow_run)
        self.assertEqual(trigger.count("cron:"), 1)
        self.assertIn('cron: "07 */2 * * *"', trigger)

    def test_branch_ci_cannot_dispatch_or_use_write_token(self):
        """Feature-branch CI must not execute privileged recovery."""
        workflow = (Path(__file__).resolve().parents[2] /
                    ".github/workflows/btc-research-scheduler-guard.yml").read_text()
        jobs = workflow.split("\njobs:\n", 1)[1]
        branch_job = jobs.split("  validate-branch:\n", 1)[1].split("  detect-and-recover:\n", 1)[0]
        main_job = jobs.split("  detect-and-recover:\n", 1)[1]
        self.assertIn("if: github.ref != 'refs/heads/main'", branch_job)
        self.assertIn("contents: read", branch_job)
        self.assertNotIn("actions: write", branch_job)
        self.assertNotIn("issues: write", branch_job)
        self.assertNotIn("research_scheduler_watchdog.py\n", branch_job)
        self.assertIn("if: github.ref == 'refs/heads/main'", main_job)
        self.assertIn("actions: write", main_job)
        self.assertIn("issues: write", main_job)
        self.assertIn("python btc-quant/ops/research_scheduler_watchdog.py", main_job)

    def test_other_branch_cannot_mask_missing_main_runs(self):
        d=decide([run("2026-10-21T11:40:00Z",branch="feature")],NOW)
        self.assertTrue(d["should_dispatch"])

if __name__=="__main__":
    unittest.main()
