"""Offline safety checks for risk-first research replay; never a trading signal."""
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from research_mainboard_daily_pit_v0 import run_portfolio
from research_risk_first_gate_scenarios import EXPERIMENTS, asharereadiness


class RiskGateInvariants(unittest.TestCase):
    def test_default_remains_existing_frozen_research_baseline(self):
        self.assertEqual(EXPERIMENTS["BASELINE_UNCHANGED"], {})
        self.assertEqual(EXPERIMENTS["ATR_STOP_LE_5PCT"]["max_stop_pct"], 0.05)
        self.assertLess(
            EXPERIMENTS["ATR5_GAP1_RISK025_WEIGHT10"]["risk_budget"], 0.005
        )

    def test_invalid_execution_gates_fail_closed(self):
        for kw in [
            {"max_stop_pct": 0.12},
            {"max_stop_pct": -0.01},
            {"max_gap_up": 0.05},
            {"min_gap_down": -0.05},
            {"risk_budget": 0.01},
            {"max_weight": 0.20},
            {"max_positions": 10},
        ]:
            with self.subTest(kw=kw):
                with self.assertRaises(ValueError):
                    run_portfolio([], {}, {}, **kw)

    def test_ashare_quant_incomplete_never_authorizes_historical_join(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            (p/"current.json").write_text(json.dumps({
                "validated": True,"version":"test","snapshot_as_of":"2026-10-06",
                "critical_history_complete":False,
                "full_requested_core_range_complete":False
            }),encoding="utf-8")
            (p/"progress.json").write_text(json.dumps({
                "datasets":{"daily":{"status":"STORED_HISTORY_INCOMPLETE",
                                      "layers":{"clean":{"rows":123,"files":1}}}}
            }),encoding="utf-8")
            result=asharereadiness(p/"current.json",p/"progress.json")
            self.assertFalse(result["eligible_for_full_2024_2026_pit_replay"])
            self.assertFalse(result["critical_history_complete"])
            self.assertEqual(result["datasets"]["daily"]["clean_rows"],123)

    def test_unvalidated_ashare_quant_snapshot_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            (p/"current.json").write_text('{"validated":false}',encoding="utf-8")
            (p/"progress.json").write_text('{"datasets":{}}',encoding="utf-8")
            with self.assertRaises(RuntimeError):
                asharereadiness(p/"current.json",p/"progress.json")


if __name__ == "__main__":
    unittest.main()
