"""Unit tests for optional shadow inference. All calls are injected, never billed."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_research.five_stock_llm_shadow import (
    FINAL_FILE, PARTIAL_FILE, PARTIAL_MODE, main as shadow_main, run, text_from_response)
from ai_research.manual_ai_review_pack import SYMBOLS, AUTHORIZATION


def fake_report():
    return {
        "as_of": "2026-10-16", "authorization": AUTHORIZATION,
        "mode": "DETERMINISTIC_FIXED_FIVE_NO_LLM_NO_TRADE",
        "stocks": [{"symbol": s, "r1_candidate": False, "r1_status": "BREADTH_NOT_PASSED",
                    "close_raw_cny": 9.9, "finance": {"data_gaps_and_risks": []},
                    "announcements": [], "research_status": "WATCH",
                    "waiting_for": ["market breadth"]} for s in SYMBOLS],
    }


class TestShadow(unittest.TestCase):
    def test_must_explicitly_budget_all_25_requests(self):
        with self.assertRaisesRegex(ValueError, "exactly 25"):
            run(fake_report(), "example-model", "dummy", 0,
                call=lambda *_: self.fail("No network allowed"))

    def test_requires_key_model(self):
        with self.assertRaisesRegex(ValueError, "key"):
            run(fake_report(), "", "", 25,
                call=lambda *_: self.fail("No network allowed"))

    def test_fake_25_real_role_rounds_with_no_network(self):
        seen = []
        def fake_call(body, api_key):
            self.assertEqual(api_key, "DUMMY_TEST_KEY")
            seen.append(body)
            return {"id": "resp-test-" + str(len(seen)), "status": "completed",
                    "output": [{"type": "message", "content": [
                        {"type": "output_text", "text": "仅为单元测试，不能认定可交易"}]}],
                    "usage": {"input_tokens": 20, "output_tokens": 8}}
        out = run(fake_report(), "test-model", "DUMMY_TEST_KEY", 25, call=fake_call)
        self.assertEqual(len(seen), 25)
        self.assertEqual(out["calls"], 25)
        self.assertEqual(out["usage_reported"]["output_tokens"], 200)
        self.assertFalse(out["affects_r1"])
        self.assertEqual(len(out["stocks"][0]["roles"]), 5)
        self.assertEqual(out["stocks"][0]["verification_status"],
                         "UNVALIDATED_LLM_SHADOW_NO_ENTRY_AUTHORIZATION")

    def test_checkpoint_fires_after_every_call_and_survives_a_late_failure(self):
        snapshots = []
        calls = []

        def flaky_call(body, api_key):
            calls.append(body)
            if len(calls) == 7:
                raise RuntimeError("simulated API outage")
            return {"id": "resp-" + str(len(calls)), "status": "completed",
                    "output": [{"type": "message", "content": [
                        {"type": "output_text", "text": "单元测试文本"}]}],
                    "usage": {"input_tokens": 1, "output_tokens": 1}}

        with self.assertRaisesRegex(RuntimeError, "outage"):
            run(fake_report(), "test-model", "DUMMY_TEST_KEY", 25, call=flaky_call,
                on_checkpoint=lambda snapshot: snapshots.append(copy.deepcopy(snapshot)))
        self.assertEqual(len(snapshots), 6)
        last = snapshots[-1]
        self.assertEqual(last["calls_completed"], 6)
        self.assertEqual(last["mode"], PARTIAL_MODE)
        self.assertEqual(len(last["stocks"]), 2)            # one finished stock + one in progress
        self.assertEqual(len(last["stocks"][0]["roles"]), 5)
        self.assertEqual(len(last["stocks"][1]["roles"]), 1)

    def test_existing_output_dir_fails_before_any_api_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "report.json"
            report_path.write_text(json.dumps(fake_report()), encoding="utf-8")
            output_dir = Path(tmp) / "out"
            output_dir.mkdir()
            argv = ["prog", "--report-json", str(report_path), "--output-dir", str(output_dir),
                    "--enable-paid-api", "--model", "test-model", "--max-requests", "25"]
            with patch("sys.argv", argv), \
                    patch.dict(os.environ, {"OPENAI_API_KEY": "DUMMY_TEST_KEY"}), \
                    patch("ai_research.five_stock_llm_shadow.run",
                          side_effect=AssertionError("must not call the API")) as fake_run:
                with self.assertRaisesRegex(SystemExit, "already exists"):
                    shadow_main()
            fake_run.assert_not_called()

    def test_failed_run_keeps_partial_file_and_success_removes_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "report.json"
            report_path.write_text(json.dumps(fake_report()), encoding="utf-8")
            argv = ["prog", "--report-json", str(report_path), "--enable-paid-api",
                    "--model", "test-model", "--max-requests", "25"]

            def failing_run(report, model, key, limit, on_checkpoint=None):
                on_checkpoint({"mode": PARTIAL_MODE, "calls_completed": 3})
                raise RuntimeError("simulated API outage")

            failed_dir = Path(tmp) / "failed"
            with patch("sys.argv", argv + ["--output-dir", str(failed_dir)]), \
                    patch.dict(os.environ, {"OPENAI_API_KEY": "DUMMY_TEST_KEY"}), \
                    patch("ai_research.five_stock_llm_shadow.run", side_effect=failing_run):
                with self.assertRaisesRegex(RuntimeError, "outage"):
                    shadow_main()
            partial = json.loads((failed_dir / PARTIAL_FILE).read_text(encoding="utf-8"))
            self.assertEqual(partial["calls_completed"], 3)
            self.assertFalse((failed_dir / FINAL_FILE).exists())

            def good_run(report, model, key, limit, on_checkpoint=None):
                on_checkpoint({"mode": PARTIAL_MODE, "calls_completed": 25})
                return {"calls": 25, "as_of": report["as_of"]}

            ok_dir = Path(tmp) / "ok"
            with patch("sys.argv", argv + ["--output-dir", str(ok_dir)]), \
                    patch.dict(os.environ, {"OPENAI_API_KEY": "DUMMY_TEST_KEY"}), \
                    patch("ai_research.five_stock_llm_shadow.run", side_effect=good_run):
                shadow_main()
            self.assertTrue((ok_dir / FINAL_FILE).exists())
            self.assertFalse((ok_dir / PARTIAL_FILE).exists())

    def test_response_must_contain_text(self):
        with self.assertRaisesRegex(ValueError, "not completed"):
            text_from_response({"status": "incomplete"})
        with self.assertRaisesRegex(ValueError, "no text"):
            text_from_response({"status": "completed", "output": []})


if __name__ == "__main__":
    unittest.main()
