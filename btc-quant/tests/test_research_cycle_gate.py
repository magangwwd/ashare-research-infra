"""Regression tests: no duplicate deep historical runs for same official data/code."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))
from research_cycle_gate import plan, code_digest, CODE_FILES


class ResearchGateTests(unittest.TestCase):
    def setUp(self):
        self.good = {"last_verified_available_utc_day":"2026-10-20",
                     "data_health":"READY"}
        self.now = datetime(2026, 10, 21, tzinfo=timezone.utc)
        self.digest = "a"*64
        self.old = {"mode":"deep", "run_id":"101",
                    "study":{"observed_utc_end":"2026-10-20"},
                    "research_fingerprint":"2026-10-20:"+self.digest}

    def test_identical_previous_deep_must_skip(self):
        result=plan("deep",self.good,self.old,self.digest,self.now)
        self.assertEqual(result["action"],"SKIP_DUPLICATE_DEEP")
        self.assertEqual(result["effective_mode"],"quick")
        self.assertTrue(result["no_repeated_historical_backtest_without_change"])

    def test_day_increment_and_code_change_each_force_new_deep(self):
        today={**self.good,"last_verified_available_utc_day":"2026-10-21"}
        self.assertEqual(plan("deep",today,self.old,self.digest)["action"],"RUN_NEW_DEEP_EXPERIMENT")
        self.assertEqual(plan("deep",self.good,self.old,"b"*64)["action"],"RUN_NEW_DEEP_EXPERIMENT")

    def test_failed_or_missing_prior_always_retries(self):
        self.assertEqual(plan("deep",self.good,None,self.digest)["action"],"RUN_NEW_DEEP_EXPERIMENT")
        bad={**self.old}
        bad.pop("study")
        self.assertEqual(plan("deep",self.good,bad,self.digest)["action"],"RUN_NEW_DEEP_EXPERIMENT")

    def test_quick_never_runs_backtest(self):
        self.assertEqual(plan("quick",self.good,None,self.digest)["effective_mode"],"quick")

    def test_stale_source_not_misrepresented_as_latest(self):
        stale={**self.good,"data_health":"STALE_SOURCE_ARCHIVE"}
        self.assertEqual(plan("deep",stale,None,self.digest)["source_health"],"STALE_SOURCE_ARCHIVE")
        with self.assertRaises(ValueError):
            plan("deep",{**self.good,"data_health":"FAKE_READY"},None,self.digest)

    def test_fingerprint_changes_with_research_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in CODE_FILES:
                dest=root/name
                dest.parent.mkdir(parents=True,exist_ok=True)
                dest.write_text("v1")
            old=code_digest(root)
            (root/CODE_FILES[0]).write_text("v2")
            self.assertNotEqual(old,code_digest(root))


if __name__ == "__main__":
    unittest.main()
