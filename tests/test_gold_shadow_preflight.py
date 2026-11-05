"""Offline tests for the fail-closed Task01 -> Gold metadata preflight."""
import copy
import hashlib
import json
import unittest

from data_pipeline.gold_shadow_preflight import evaluate

SHA = "a" * 64
CATEGORIES = ("daily", "daily_basic", "adj_factor", "suspend_d", "namechange", "trade_cal")


def fixture():
    files = [
        {"path": f"data/clean/{name}/year=2026/month=10/part-000.parquet",
         "bytes": 10, "sha256": SHA, "file_id": f"file_{i}"}
        for i, name in enumerate(CATEGORIES)
    ]
    task = {
        "version": "v-2026-10-30", "validated": True,
        "snapshot_as_of": "2026-10-30", "source_run_id": "12345678",
        "file_count": len(files), "total_bytes": 60, "files": files,
        "critical_history_complete": True,
        "full_requested_core_range_complete": True,
    }
    pointer = {k: task[k] for k in (
        "version", "validated", "snapshot_as_of", "source_run_id",
        "file_count", "total_bytes", "critical_history_complete",
        "full_requested_core_range_complete",
    )}
    pointer.update({"version_folder_id": "version-id", "manifest_file_id": "manifest-id"})
    gold = {
        "as_of_date": "2026-10-30",
        "validation": {"primary_keys_unique": True, "ohlc_valid": True},
        "files": {
            "instruments.parquet": {"rows": 3486, "sha256": SHA},
            "daily_snapshot.parquet": {"rows": 3190, "sha256": SHA},
        },
        "unit_conversions": {
            "vol_hand_to_volume_shares": 100,
            "amount_thousand_cny_to_turnover_cny": 1000,
            "shares_ten_thousand_to_shares": 10000,
            "market_cap_ten_thousand_cny_to_cny": 10000,
        },
    }
    return pointer, task, gold


def run(pointer, task, gold):
    raw = json.dumps(task, sort_keys=True).encode()
    pointer["manifest_sha256"] = hashlib.sha256(raw).hexdigest()
    return evaluate(pointer, raw, gold)


class GoldShadowPreflightTests(unittest.TestCase):
    def test_valid_metadata_still_cannot_switch_production(self):
        p, t, g = fixture()
        r = run(p, t, g)
        self.assertEqual(r["status"], "PASS_METADATA_ONLY")
        self.assertFalse(r["production_switch_allowed"])
        self.assertFalse(r["parquet_rows_verified"])
        self.assertFalse(r["strategy_equivalence_verified"])

    def test_actual_2026_10_24_vs_10_30_blocks(self):
        p, t, g = fixture()
        p["snapshot_as_of"] = t["snapshot_as_of"] = "2026-10-24"
        r = run(p, t, g)
        self.assertIn("AS_OF_DATE_MISMATCH", r["reasons"])

    def test_actual_incomplete_history_blocks_even_if_dates_align(self):
        p, t, g = fixture()
        p["critical_history_complete"] = t["critical_history_complete"] = False
        p["full_requested_core_range_complete"] = t["full_requested_core_range_complete"] = False
        r = run(p, t, g)
        self.assertIn("TASK_CRITICAL_HISTORY_INCOMPLETE", r["reasons"])
        self.assertIn("TASK_CORE_RANGE_INCOMPLETE", r["reasons"])

    def test_manifest_hash_mismatch(self):
        p, t, g = fixture()
        raw = json.dumps(t).encode()
        p["manifest_sha256"] = "0" * 64
        self.assertIn("TASK_MANIFEST_SHA_MISMATCH", evaluate(p, raw, g)["reasons"])

    def test_pointer_manifest_mismatch(self):
        p, t, g = fixture()
        p["file_count"] += 1
        self.assertIn("TASK_POINTER_MANIFEST_MISMATCH", run(p, t, g)["reasons"])

    def test_file_duplicate_and_invalid_digest(self):
        p, t, g = fixture()
        t["files"][1]["path"] = t["files"][0]["path"]
        t["files"][1]["sha256"] = "bad"
        self.assertIn("TASK_FILE_ENTRY_INVALID", run(p, t, g)["reasons"])

    def test_file_total_mismatch(self):
        p, t, g = fixture()
        t["files"][0]["bytes"] += 1
        self.assertIn("TASK_FILE_TOTAL_MISMATCH", run(p, t, g)["reasons"])

    def test_required_clean_category_missing(self):
        p, t, g = fixture()
        t["files"][-1]["path"] = "data/raw/trade_cal/year=2026/month=10/part-000.parquet"
        r = run(p, t, g)
        self.assertIn("TASK_REQUIRED_CLEAN_DATA_MISSING", r["reasons"])
        self.assertEqual(r["missing_clean_categories"], ["trade_cal"])

    def test_gold_validation_failure(self):
        p, t, g = fixture()
        g["validation"]["ohlc_valid"] = False
        self.assertIn("GOLD_NOT_VALIDATED", run(p, t, g)["reasons"])

    def test_gold_missing_core_and_units(self):
        p, t, g = fixture()
        del g["files"]["daily_snapshot.parquet"]
        g["unit_conversions"]["vol_hand_to_volume_shares"] = 1
        r = run(p, t, g)
        self.assertIn("GOLD_CORE_MANIFEST_INVALID", r["reasons"])
        self.assertIn("GOLD_UNIT_CONTRACT_MISMATCH", r["reasons"])

    def test_invalid_date_blocks(self):
        p, t, g = fixture()
        g["as_of_date"] = "2026-02-30"
        self.assertIn("INVALID_AS_OF_DATE", run(p, t, g)["reasons"])

    def test_invalid_manifest_json_blocks(self):
        p, t, g = fixture()
        p["manifest_sha256"] = hashlib.sha256(b"{").hexdigest()
        r = evaluate(p, b"{", g)
        self.assertIn("TASK_MANIFEST_INVALID_JSON", r["reasons"])
        self.assertFalse(r["production_switch_allowed"])

    def test_manifest_document_type_blocks(self):
        p, t, g = fixture()
        p["manifest_sha256"] = hashlib.sha256(b"[]").hexdigest()
        r = evaluate(p, b"[]", g)
        self.assertEqual(r["reasons"], ["INVALID_DOCUMENT_TYPE"])


if __name__ == "__main__":
    unittest.main()
