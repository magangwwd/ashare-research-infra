"""Deterministic tests for hourly/deep BTC research safety gates."""
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops"))
from research_loop import check_latest, deep_summary, finite_number


class LoopTests(unittest.TestCase):
    def test_monitor_not_equivalent_to_successful_strategy(self):
        now = dt.datetime(2026, 10, 21, 4, tzinfo=dt.timezone.utc)
        def run(at, conclusion="success", status="completed"):
            return {"workflow_runs": [{
                "created_at": at, "conclusion": conclusion,
                "status": status, "html_url": "https://github.com/test/run/1",
                "id": 1,
            }]}
        self.assertEqual(check_latest(run("2026-10-21T03:00:00Z"), now, 12)["status"], "OK")
        self.assertEqual(check_latest(run("2026-10-19T03:00:00Z"), now, 12)["status"], "STALE")
        self.assertEqual(check_latest(run("2026-10-21T03:00:00Z","failure"), now, 12)["status"], "FAILED")
        self.assertEqual(check_latest(run("2026-10-21T03:00:00Z",None,"in_progress"), now, 12)["status"], "IN_PROGRESS")
        self.assertEqual(check_latest({"workflow_runs": []}, now, 12)["status"], "UNKNOWN_NO_RUNS")

    def test_reject_nan_price_or_dd(self):
        for invalid in (float("nan"),float("inf"),"0.05",True,None):
            with self.assertRaises(ValueError):
                finite_number(invalid)

    def test_deep_receipt_must_share_exact_closed_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)
            fixtures={
                "regime/latest_regime.json": {
                    "data_end":"2026-10-19",
                    "latest_state":{"confirmed_regime":"RANGE"},
                    "latest_recommendation":{"strategy":"HMG_RESEARCH"}
                },
                "stage1/stage1_summary.json": {
                    "data_end_utc":"2026-10-19","input_sha256":"fake_for_unit_test",
                    "results": {k:{"total_return":.12,"max_drawdown":.08,"trades":3,"folds":2}
                        for k in ("r1_1x","r1_2x","v3_static_1x","v3_static_2x","cash_static_1x")},
                },
                "hmg_1x/hmg_summary.json":{"total_return":.05,"max_drawdown":.02,"trades":2},
                "hmg_2x/hmg_summary.json":{"total_return":.01,"max_drawdown":.03,"trades":2},
                "v3/summary.json":{
                    "data_end_utc":"2026-10-19",
                    "strategies": {k:{"total_return":.18,"max_drawdown":.07,"trades":4}
                        for k in ("v3_guarded_1x_cost","v3_guarded_2x_cost")}},
                "data/data_provenance.json":{"end":"2026-10-19"},
                "source_availability.json":{
                    "last_verified_available_utc_day":"2026-10-19",
                    "source_lag_days":1,"data_health":"STALE_SOURCE_ARCHIVE"},
            }
            for name,blob in fixtures.items():
                path=base/name
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text(json.dumps(blob))
            out=deep_summary(base)
            self.assertEqual(out["observed_utc_end"],"2026-10-19")
            self.assertEqual(out["source_health"],"STALE_SOURCE_ARCHIVE")
            self.assertEqual(out["promotion_decision"],"RESEARCH_ONLY_NO_LIVE_OR_AUTOMATIC_PROMOTION")
            fixtures["v3/summary.json"]["data_end_utc"]="2026-10-18"
            (base/"v3/summary.json").write_text(json.dumps(fixtures["v3/summary.json"]))
            with self.assertRaisesRegex(ValueError,"inconsistent UTC cutoff"):
                deep_summary(base)


if __name__ == "__main__":
    unittest.main()
