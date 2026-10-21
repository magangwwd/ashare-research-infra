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

    def test_other_branch_cannot_mask_missing_main_runs(self):
        d=decide([run("2026-10-21T11:40:00Z",branch="feature")],NOW)
        self.assertTrue(d["should_dispatch"])

if __name__=="__main__":
    unittest.main()
