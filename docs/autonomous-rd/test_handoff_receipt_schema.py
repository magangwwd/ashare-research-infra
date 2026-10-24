"""Regression checks for the GitHub-event portion of AUTO-RD-SOP-001 Receipt Schema.

No external dependencies; this test does not certify investment research.
"""
import json
from pathlib import Path
import unittest

SCHEMA = json.loads((Path(__file__).parent / "handoff_receipt.schema.json").read_text(encoding="utf-8"))


class HandoffReceiptEventSchemaTests(unittest.TestCase):
    def test_real_pr_trigger_is_accepted(self):
        self.assertIn("pull_request", SCHEMA["properties"]["github_event"]["enum"])

    def test_existing_event_values_are_preserved(self):
        supported = set(SCHEMA["properties"]["github_event"]["enum"])
        old = {"push", "schedule", "workflow_dispatch", "workflow_run",
               "repository_dispatch", "manual_external"}
        self.assertTrue(old.issubset(supported))

    def test_unknown_event_is_not_silently_accepted(self):
        self.assertNotIn("made_up_event", SCHEMA["properties"]["github_event"]["enum"])
        self.assertEqual(len(SCHEMA["properties"]["github_event"]["enum"]),
                         len(set(SCHEMA["properties"]["github_event"]["enum"])))

    def test_existing_receipts_keep_schema_version(self):
        self.assertEqual(SCHEMA["properties"]["schema_version"]["const"], "1.0.0")
        self.assertIn("REVIEW_REQUIRED", SCHEMA["properties"]["status"]["enum"])
        self.assertIn("NOT_FOR_TRADING", (Path(__file__).parent / "AUTONOMOUS_RD_SOP.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
