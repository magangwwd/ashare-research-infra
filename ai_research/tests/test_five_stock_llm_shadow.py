"""Unit tests for optional shadow inference. All calls are injected, never billed."""
import copy
import unittest
from unittest.mock import patch

from ai_research.five_stock_llm_shadow import run, text_from_response
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

    def test_response_must_contain_text(self):
        with self.assertRaisesRegex(ValueError, "not completed"):
            text_from_response({"status": "incomplete"})
        with self.assertRaisesRegex(ValueError, "no text"):
            text_from_response({"status": "completed", "output": []})


if __name__ == "__main__":
    unittest.main()
