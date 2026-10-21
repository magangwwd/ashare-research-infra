"""No-network tests for project handoff, stage hashing and research gate."""
from __future__ import annotations
import json
from pathlib import Path
import sys
import tempfile
import unittest

import pandas as pd

from etf_single_rotation.research_pipeline import Stage, run_plan
from etf_single_rotation.research_gate import evaluate, REQUIRED_REPORTS, ROOT


class PipelineCheckpointTests(unittest.TestCase):
    def stage(self, file: str, body: str):
        return Stage("write_" + file, (sys.executable, "-c",
                     "from pathlib import Path; Path(" + repr(file) +
                     ").write_text(" + repr(body) + ")"), (file,))

    def test_save_and_resume_skips_matching_completed_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint = root / "state.json"
            stages = (self.stage("one.txt", "first"), self.stage("two.txt", "second"))
            result = run_plan(stages, root, checkpoint, "frozen-sha", resume=False)
            self.assertEqual(result["pipeline_status"], "RESEARCH_RUN_COMPLETED_REVIEW_REQUIRED")
            self.assertEqual(len(result["stages"]), 2)
            state2 = run_plan(stages, root, checkpoint, "frozen-sha", resume=True)
            self.assertEqual(state2["stages"]["write_one.txt"]["total_attempts"], 1)
            self.assertEqual((root / "one.txt").read_text(), "first")

    def test_corrupted_prior_result_blocks_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint = root / "state.json"
            stages = (self.stage("one.txt", "correct"),)
            run_plan(stages, root, checkpoint, "sha1")
            (root / "one.txt").write_text("corrupted")
            with self.assertRaisesRegex(ValueError, "mismatch"):
                run_plan(stages, root, checkpoint, "sha1", resume=True)

    def test_different_code_commit_cannot_reuse_old_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint = root / "state.json"
            stages = (self.stage("one.txt", "v1"),)
            run_plan(stages, root, checkpoint, "old")
            with self.assertRaisesRegex(ValueError, "another project/commit"):
                run_plan(stages, root, checkpoint, "new", resume=True)

    def test_failed_job_writes_recoverable_stage_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint = root / "state.json"
            bad = Stage("error", (sys.executable, "-c", "raise SystemExit(9)"), ())
            with self.assertRaisesRegex(RuntimeError, "failed after"):
                run_plan((bad,), root, checkpoint, "sha", resume=False)
            record = json.loads(checkpoint.read_text())
            self.assertEqual(record["stages"]["error"]["status"], "FAILED")
            self.assertEqual(record["pipeline_status"], "INTERRUPTED_REVIEW_OR_RESUME")
            self.assertEqual(record["stages"]["error"]["exit_code"], 9)

    def test_resume_only_retries_remaining_after_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint = root / "state.json"
            first = self.stage("one.txt", "ok")
            error = Stage("unavailable", (sys.executable, "-c", "raise SystemExit(3)"), ())
            with self.assertRaises(RuntimeError):
                run_plan((first, error), root, checkpoint, "sha")
            recovered = self.stage("two.txt", "later")
            # The previously failed task gets corrected under the same logical task ID.
            fixed = Stage("unavailable", recovered.argv, recovered.outputs)
            result = run_plan((first, fixed), root, checkpoint, "sha", resume=True)
            self.assertEqual(result["stages"]["write_one.txt"]["total_attempts"], 1)
            self.assertEqual(result["stages"]["unavailable"]["status"], "SUCCESS")


class ResearchGateTests(unittest.TestCase):
    def test_rejects_missing_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "evidence missing"):
                evaluate(Path(tmp))

    def test_no_dividend_or_prospective_evidence_never_certifies_trading(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for p in REQUIRED_REPORTS:
                dest = root / p.relative_to(ROOT)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text("{}")
            quality = {
                "symbols": 8, "expected_symbols": [
                    "510300.SH", "510500.SH", "159915.SZ", "510050.SH",
                    "510880.SH", "518880.SH", "512100.SH", "512690.SH"
                ], "missing_symbols": [], "missing_etf_session_records": 5,
                "last_date": "2026-10-21", "acceptance": "REVIEW_REQUIRED"
            }
            (root / "results/data_quality_summary.json").write_text(json.dumps(quality))
            (root / "results/documented_suspension_summary.json").write_text(
                json.dumps({
                    "status": "ALL_GAPS_HAVE_ANNOUNCED_SUSPENSION_EVIDENCE",
                    "observed_provider_missing_exchange_session_rows": 5,
                    "documented_announced_halt_rows": 5,
                    "unexplained_rows": 0,
                })
            )
            pd.DataFrame([
                {"fee_slippage_per_side": f, "cagr": 0.05,
                 "max_drawdown": -0.21}
                for f in [0.001, 0.003, 0.005]
            ]).to_csv(root / "results/self_financing/cost_stress_executable_walk_forward.csv", index=False)
            (root / "results/self_financing/benchmark_self_financing_manifest.json").write_text(
                json.dumps({"status": "BENCHMARK_EXPLORATORY_NOT_FOR_TRADING"})
            )
            pd.DataFrame([
                {"benchmark": "cash_zero_interest"},
                {"benchmark": "etf_buy_hold_510300.SH"},
                {"benchmark": "etf_buy_hold_518880.SH"},
            ]).to_csv(
                root / "results/self_financing/benchmark_self_financing_comparison.csv",
                index=False,
            )
            result = evaluate(root)
            self.assertFalse(result["full_acceptance"])
            self.assertEqual(result["status"], "NOT_FOR_TRADING")
            self.assertIn("ANNOUNCED_ETF_SPLIT_HALT_PRICE_ADJUSTMENT_NOT_INDEPENDENTLY_AUDITED", result["open_blockers"])
            self.assertEqual(len(result["cost_stress"]), 3)



class DriveUploadRecoveryTests(unittest.TestCase):
    def test_retries_transient_500_and_skips_confirmed_uploads_on_rerun(self):
        from unittest.mock import patch
        from etf_single_rotation import upload_drive as uploader

        class Response:
            def __init__(self, status, file_id="file-123"):
                self.status_code = status
                self._id = file_id

            def json(self):
                return {"id": self._id, "name": "sample.csv"}

        class FakeSession:
            calls = 0
            def post(self, *args, **kwargs):
                self.calls += 1
                return Response(500 if self.calls == 1 else 200)

        with tempfile.TemporaryDirectory() as tmp:
            sample = Path(tmp) / "sample.csv"
            sample.write_text("hello")
            checkpoint = Path(tmp) / "upload_checkpoint.json"
            fake = FakeSession()
            with patch.object(uploader, "FILES", [sample]), patch.object(
                uploader, "PROGRESS", checkpoint
            ), patch.object(
                uploader, "_oauth_session", return_value=fake
            ), patch.object(uploader.time, "sleep", return_value=None):
                uploader.main()
                self.assertEqual(fake.calls, 2)
                progress = json.loads(checkpoint.read_text())
                self.assertEqual(progress["files"][sample.as_posix()]["drive_id"], "file-123")
                uploader.main()
                self.assertEqual(fake.calls, 2, "Already uploaded file must not be uploaded again")

    def test_partial_upload_failure_can_continue_from_per_file_checkpoint(self):
        from unittest.mock import patch
        from etf_single_rotation import upload_drive as uploader

        class Response:
            def __init__(self, code, drive_id="next"):
                self.status_code = code
                self._id = drive_id

            def json(self):
                return {"id": self._id, "name": "ok"}

        class Session:
            def __init__(self, statuses):
                self.statuses = list(statuses)
                self.calls = 0

            def post(self, *args, **kwargs):
                self.calls += 1
                return Response(self.statuses.pop(0))

        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first.csv"
            second = Path(tmp) / "second.csv"
            first.write_text("first")
            second.write_text("second")
            progress_path = Path(tmp) / "upload_checkpoint.json"
            prior = Session([200, 500, 500, 500, 500])
            recovered = Session([200])
            with patch.object(uploader, "FILES", [first, second]), patch.object(
                uploader, "PROGRESS", progress_path
            ), patch.object(uploader.time, "sleep", return_value=None):
                with patch.object(uploader, "_oauth_session", return_value=prior):
                    with self.assertRaises(SystemExit):
                        uploader.main()
                self.assertEqual(prior.calls, 5)
                with patch.object(uploader, "_oauth_session", return_value=recovered):
                    uploader.main()
                self.assertEqual(recovered.calls, 1)
                state = json.loads(progress_path.read_text())
                self.assertEqual(len(state["files"]), 2)


if __name__ == "__main__":
    unittest.main()
