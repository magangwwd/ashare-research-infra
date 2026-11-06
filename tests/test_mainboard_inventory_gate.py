"""Offline tests for the non-destructive Drive inventory gate."""
import unittest

from tools.mainboard_inventory_gate import audit, inspect


def item(name, folder=False):
    return {
        "title": name,
        "mime_type": "application/vnd.google-apps.folder" if folder else "application/json",
        "file_or_folder": "folder" if folder else "file",
    }


class InventoryGateTests(unittest.TestCase):
    def setUp(self):
        self.task01 = [item("current.json"), item("versions", True), item("staging", True)]
        self.gold = [item("manifest.json"), item("instruments.parquet"), item("daily_snapshot.parquet")]

    def test_complete_inventory_still_cannot_authorize_switch(self):
        result = audit(self.task01, self.gold)
        self.assertEqual(result["status"], "READY_FOR_CONTENT_VERIFICATION")
        self.assertFalse(result["production_switch_allowed"])
        self.assertFalse(result["deletion_allowed"])

    def test_missing_current_pointer_blocks(self):
        result = audit(self.task01[1:], self.gold)
        self.assertEqual(result["status"], "BLOCKED_MISSING_OR_INVALID_PRODUCTION_FILES")
        self.assertIn("current.json", result["roots"][0]["missing"])

    def test_missing_gold_manifest_blocks(self):
        result = audit(self.task01, self.gold[1:])
        self.assertIn("manifest.json", result["roots"][1]["missing"])

    def test_duplicate_pointer_blocks(self):
        result = inspect("task01", self.task01 + [item("current.json")])
        self.assertEqual(result["duplicate"], ["current.json"])
        self.assertEqual(result["metadata_gate"], "BLOCKED")

    def test_staging_cannot_replace_pointer(self):
        result = audit([item("versions", True), item("staging", True)], self.gold)
        self.assertFalse(result["production_switch_allowed"])

    def test_wrong_type_blocks(self):
        result = inspect("task01", [item("current.json", True), item("versions", True), item("staging", True)])
        self.assertIn("current.json", result["type_mismatch"])

    def test_invalid_listing_rejected(self):
        with self.assertRaises(ValueError):
            audit({"results": {"not": "a list"}}, self.gold)


if __name__ == "__main__":
    unittest.main()
