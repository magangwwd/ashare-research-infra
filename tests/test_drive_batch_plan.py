import hashlib
import unittest

from data_pipeline.drive_batch_plan import BatchPlanError, plan_batches


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


if __name__ == "__main__":
    unittest.main()
