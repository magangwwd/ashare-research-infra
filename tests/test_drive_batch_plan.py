import hashlib
import unittest

from data_pipeline.drive_batch_plan import BatchPlanError, plan_batches, reconcile_verified_batches


def item(n):
    return {"path": f"data/{n:04d}.parquet", "bytes": n + 1,
            "sha256": hashlib.sha256(str(n).encode()).hexdigest()}


class BatchPlanTests(unittest.TestCase):
    def test_partition_all_files_once(self):
        result = plan_batches([item(n) for n in range(8172)])
        self.assertEqual(len(result["batches"]), 17)
        self.assertEqual(result["batches"][-1]["file_count"], 172)
        paths = [p for batch in result["batches"] for p in batch["paths"]]
        self.assertEqual(paths, [item(n)["path"] for n in range(8172)])
        self.assertFalse(result["production_switch_allowed"])

    def test_deterministic(self):
        self.assertEqual(plan_batches([item(1), item(2)]), plan_batches([item(1), item(2)]))

    def test_reject_duplicate_and_unsafe(self):
        with self.assertRaises(BatchPlanError):
            plan_batches([item(1), item(1)])
        for bad in ("../secret", "/absolute", "a//b", "a/./b", "a\\b"):
            with self.subTest(path=bad), self.assertRaises(BatchPlanError):
                plan_batches([dict(item(1), path=bad)])

    def test_reject_invalid_batch_size_and_sha(self):
        for size in (0, 501, True, 1.5):
            with self.subTest(size=size), self.assertRaises(BatchPlanError):
                plan_batches([item(1)], batch_size=size)
        with self.assertRaises(BatchPlanError):
            plan_batches([dict(item(1), sha256="bad")])

    def test_interrupted_500_and_resume_missing_only(self):
        inventory = [item(n) for n in range(8172)]
        fingerprint = plan_batches(inventory)["inventory_sha256"]
        receipts = [dict(inventory[n], file_id=f"remote-{n}") for n in range(500)]
        resumed = reconcile_verified_batches(
            inventory, receipts, expected_inventory_sha256=fingerprint)
        self.assertEqual(resumed["verified_file_count"], 500)
        self.assertEqual(resumed["missing_file_count"], 7672)
        self.assertEqual(resumed["batches"][0]["paths"][0], item(500)["path"])
        self.assertFalse(resumed["production_switch_allowed"])

    def test_corruption_and_inventory_change_fail_closed(self):
        inventory = [item(n) for n in range(3)]
        fingerprint = plan_batches(inventory)["inventory_sha256"]
        good = dict(inventory[0], file_id="remote-0")
        for bad in (dict(good, sha256="0" * 64), dict(good, bytes=999),
                    dict(good, file_id=""), dict(good, path="unknown")):
            with self.subTest(bad=bad), self.assertRaises(BatchPlanError):
                reconcile_verified_batches(inventory, [bad],
                                           expected_inventory_sha256=fingerprint)
        with self.assertRaises(BatchPlanError):
            reconcile_verified_batches(inventory, [good, good],
                                       expected_inventory_sha256=fingerprint)
        with self.assertRaises(BatchPlanError):
            reconcile_verified_batches(inventory, [good],
                                       expected_inventory_sha256="0" * 64)


if __name__ == "__main__":
    unittest.main()
