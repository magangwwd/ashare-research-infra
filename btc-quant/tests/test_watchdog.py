"""Unit tests for conservative watchdog retry rules (no network calls)."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("btc_watchdog", ROOT / "ops" / "watchdog.py")
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)


class WatchdogRules(unittest.TestCase):
    def test_data_gap_never_retries(self):
        self.assertFalse(watchdog.retry_decision(
            "failure", ["Strict UTC data validation and daily aggregation"], 1)[0])

    def test_network_download_first_second_retry(self):
        steps = ["Download official complete-month BTCUSDT 4h archives"]
        self.assertTrue(watchdog.retry_decision("failure", steps, 1)[0])
        self.assertTrue(watchdog.retry_decision("failure", steps, 2)[0])
        self.assertFalse(watchdog.retry_decision("failure", steps, 3)[0])

    def test_cancel_never_retries(self):
        self.assertFalse(watchdog.retry_decision("cancelled", [], 1)[0])

    def test_test_or_code_failure_never_retries(self):
        self.assertFalse(watchdog.retry_decision("failure", ["Run synthetic unit tests first"], 1)[0])
        self.assertFalse(watchdog.retry_decision("failure", ["Rebuild V1 V2 V3 backtests with costs and guards"], 1)[0])

    def test_drive_write_failure_never_retries(self):
        self.assertFalse(watchdog.retry_decision("failure", ["Write append-only research outputs to existing Drive folder"], 1)[0])

    def test_timeout_retries_with_limit(self):
        self.assertTrue(watchdog.retry_decision("timed_out", [], 2)[0])
        self.assertFalse(watchdog.retry_decision("timed_out", [], 3)[0])


if __name__ == "__main__":
    unittest.main()
