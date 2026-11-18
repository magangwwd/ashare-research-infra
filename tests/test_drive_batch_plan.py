import hashlib
import unittest

from data_pipeline.drive_batch_plan import build_verified_receipt_ledger, BatchPlanError, plan_batches, reconcile_verified_batches, validate_persisted_receipt_ledger, validate_resume_checkpoint


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
        receipts = [dict(inventory[n], file_id=f"remote-{n}", stage_id="stage-1", source_run_id="run-1") for n in range(500)]
        resumed = reconcile_verified_batches(
            inventory, receipts, expected_inventory_sha256=fingerprint, expected_stage_id="stage-1", expected_source_run_id="run-1")
        self.assertEqual(resumed["verified_file_count"], 500)
        self.assertEqual(resumed["missing_file_count"], 7672)
        self.assertEqual(resumed["batches"][0]["paths"][0], item(500)["path"])
        self.assertFalse(resumed["production_switch_allowed"])

    def test_corruption_and_inventory_change_fail_closed(self):
        inventory = [item(n) for n in range(3)]
        fingerprint = plan_batches(inventory)["inventory_sha256"]
        good = dict(inventory[0], file_id="remote-0", stage_id="stage-1", source_run_id="run-1")
        for bad in (dict(good, sha256="0" * 64), dict(good, bytes=999),
                    dict(good, file_id=""), dict(good, path="unknown")):
            with self.subTest(bad=bad), self.assertRaises(BatchPlanError):
                reconcile_verified_batches(inventory, [bad],
                                           expected_inventory_sha256=fingerprint, expected_stage_id="stage-1", expected_source_run_id="run-1")
        with self.assertRaises(BatchPlanError):
            reconcile_verified_batches(inventory, [good, good],
                                       expected_inventory_sha256=fingerprint, expected_stage_id="stage-1", expected_source_run_id="run-1")
        with self.assertRaises(BatchPlanError):
            reconcile_verified_batches(inventory, [good],
                                       expected_inventory_sha256="0" * 64, expected_stage_id="stage-1", expected_source_run_id="run-1")

    def test_reused_remote_file_id_fails_closed(self):
        inventory = [item(1), item(2)]
        receipts = [dict(row, file_id="same-drive-id",
                         stage_id="stage-1", source_run_id="run-1")
                    for row in inventory]
        with self.assertRaises(BatchPlanError):
            reconcile_verified_batches(
                inventory, receipts,
                expected_inventory_sha256=plan_batches(inventory)["inventory_sha256"],
                expected_stage_id="stage-1", expected_source_run_id="run-1")
        ledger = {"schema_version": 1,
                  "inventory_sha256": plan_batches(inventory)["inventory_sha256"],
                  "pointer_sha256": "a" * 64, "stage_id": "stage-1",
                  "source_run_id": "run-1", "production_switch_allowed": False,
                  "receipts": receipts}
        with self.assertRaises(BatchPlanError):
            validate_persisted_receipt_ledger(
                ledger, inventory_sha256=ledger["inventory_sha256"],
                pointer_sha256="a" * 64, stage_id="stage-1",
                source_run_id="run-1")

    def test_cross_stage_and_cross_run_receipts_rejected(self):
        inventory = [item(1)]
        fingerprint = plan_batches(inventory)["inventory_sha256"]
        receipt = dict(inventory[0], file_id="remote", stage_id="stage-1", source_run_id="run-1")
        for changed in (dict(receipt, stage_id="stage-2"),
                        dict(receipt, source_run_id="run-2")):
            with self.assertRaises(BatchPlanError):
                reconcile_verified_batches(inventory, [changed],
                    expected_inventory_sha256=fingerprint,
                    expected_stage_id="stage-1", expected_source_run_id="run-1")

    def test_checkpoint_expiry_identity_and_pointer_gate(self):
        checkpoint = {"schema_version": 1, "inventory_sha256": "a" * 64,
                      "stage_id": "stage-1", "source_run_id": "run-1",
                      "expires_at_epoch": 200, "production_switch_allowed": False}
        kwargs = dict(inventory_sha256="a" * 64, stage_id="stage-1",
                      source_run_id="run-1", now_epoch=100)
        result = validate_resume_checkpoint(checkpoint, **kwargs)
        self.assertTrue(result["remote_sha_revalidation_required"])
        self.assertFalse(result["production_switch_allowed"])
        for changed in (dict(checkpoint, stage_id="wrong"),
                        dict(checkpoint, source_run_id="wrong"),
                        dict(checkpoint, inventory_sha256="b" * 64),
                        dict(checkpoint, expires_at_epoch=100),
                        dict(checkpoint, production_switch_allowed=True)):
            with self.subTest(changed=changed), self.assertRaises(BatchPlanError):
                validate_resume_checkpoint(changed, **kwargs)
        with self.assertRaises(BatchPlanError):
            validate_resume_checkpoint(checkpoint, **dict(kwargs, now_epoch=200))

    def test_persisted_ledger_requires_exact_pointer_and_remote_revalidation(self):
        ledger = {"schema_version": 1, "inventory_sha256": "a" * 64,
                  "pointer_sha256": "b" * 64, "stage_id": "stage-1",
                  "source_run_id": "run-1", "production_switch_allowed": False,
                  "receipts": [{"path": "data/1", "stage_id": "stage-1",
                                "source_run_id": "run-1", "file_id": "drive-1"}]}
        kwargs = dict(inventory_sha256="a" * 64, pointer_sha256="b" * 64,
                      stage_id="stage-1", source_run_id="run-1")
        result = validate_persisted_receipt_ledger(ledger, **kwargs)
        self.assertTrue(result["remote_sha_revalidation_required"])
        self.assertFalse(result["production_switch_allowed"])
        for changed in (dict(ledger, pointer_sha256="c" * 64),
                        dict(ledger, source_run_id="run-2"),
                        dict(ledger, production_switch_allowed=True),
                        dict(ledger, receipts=ledger["receipts"] * 2),
                        dict(ledger, receipts=[dict(ledger["receipts"][0],
                                                     stage_id="stage-2")])):
            with self.subTest(changed=changed), self.assertRaises(BatchPlanError):
                validate_persisted_receipt_ledger(changed, **kwargs)

    def test_portable_ledger_snapshot_requires_verified_identity(self):
        inventory = [item(n) for n in range(3)]
        receipts = [dict(inventory[0], stage_id="stage-1",
                         source_run_id="run-1", file_id="drive-1")]
        ledger = build_verified_receipt_ledger(
            inventory, receipts, stage_id="stage-1",
            source_run_id="run-1", pointer_sha256="a" * 64)
        self.assertEqual(ledger["inventory_sha256"],
                         plan_batches(inventory)["inventory_sha256"])
        self.assertEqual(len(ledger["receipts"]), 1)
        self.assertFalse(ledger["production_switch_allowed"])
        receipts[0]["file_id"] = "mutated"
        self.assertEqual(ledger["receipts"][0]["file_id"], "drive-1")
        for bad in (dict(ledger["receipts"][0], sha256="0" * 64),
                    dict(ledger["receipts"][0], stage_id="wrong")):
            with self.assertRaises(BatchPlanError):
                build_verified_receipt_ledger(
                    inventory, [bad], stage_id="stage-1",
                    source_run_id="run-1", pointer_sha256="a" * 64)
        with self.assertRaises(BatchPlanError):
            build_verified_receipt_ledger(
                inventory, ledger["receipts"], stage_id="stage-1",
                source_run_id="run-1", pointer_sha256="invalid")


if __name__ == "__main__":
    unittest.main()
