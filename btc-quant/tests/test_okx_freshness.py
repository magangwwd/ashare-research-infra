"""No-network watchdog logic regression tests."""
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / "ops" / "okx_freshness.py"
spec = importlib.util.spec_from_file_location("okx_freshness_module", path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
UTC = dt.timezone.utc


class FreshnessTests(unittest.TestCase):
    def test_selects_only_completed_matching_branch(self):
        runs = [
            {"id": 1, "conclusion": "success", "status": "completed",
             "head_branch": "other", "created_at": "2026-10-20T01:00:00Z"},
            {"id": 2, "conclusion": "failure", "status": "completed",
             "head_branch": "main", "created_at": "2026-10-20T02:00:00Z"},
            {"id": 3, "conclusion": "success", "status": "completed",
             "head_branch": "main", "created_at": "2026-10-20T00:00:00Z"},
        ]
        self.assertEqual(mod.latest_success(runs, "main")["id"], 3)
        self.assertIsNone(mod.latest_success(runs, "feature",))

    def test_byte_hash_count_and_age_must_match(self):
        now = dt.datetime(2026, 10, 20, 2, 0, tzinfo=UTC)
        payload = b'{"data":1}\n{"data":2}\n'
        manifest = {
            "kind": "SPARSE_REST_NOT_CONTINUOUS_L2",
            "created_utc": "20261020T010000Z",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "rows": 2, "errors": []
        }
        self.assertEqual(mod.verify_snapshot(manifest, payload, now=now), 1)
        with self.assertRaises(ValueError):
            mod.verify_snapshot(manifest, payload + b"corruption", now=now)
        with self.assertRaises(ValueError):
            mod.verify_snapshot({**manifest, "rows": 3}, payload, now=now)
        with self.assertRaises(ValueError):
            mod.verify_snapshot(manifest, payload, now=now + dt.timedelta(hours=7))
        with self.assertRaises(ValueError):
            mod.verify_snapshot({**manifest, "errors": ["api blocked"]}, payload, now=now)


if __name__ == "__main__":
    unittest.main()
