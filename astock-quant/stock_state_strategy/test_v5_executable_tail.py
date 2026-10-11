from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from v5_executable_tail import (
    add_executable_tail_labels,
    reconcile_labels,
    scalar_tail_outcome,
)


BUY_COST = 0.0013
SELL_COST = 0.0018


def make_stock(instrument_id: str, rows: list[dict]) -> pd.DataFrame:
    output = []
    for item in rows:
        value = {
            "instrument_id": instrument_id,
            "trade_idx": item["trade_idx"],
            "trade_date": pd.Timestamp("2026-01-01")
            + pd.Timedelta(days=item["trade_idx"]),
            "open": item.get("open", 100.0),
            "high": item.get("high", item.get("open", 100.0)),
            "low": item.get("low", item.get("open", 100.0)),
            "close": item.get("close", item.get("open", 100.0)),
            "limit_status": item.get("limit_status", 0),
            "historical_state_known": item.get("historical_state_known", True),
            "historical_is_st": item.get("historical_is_st", False),
            "atr20_pct": item.get("atr20_pct", 0.04),
        }
        for field in ("open", "high", "low", "close"):
            value[f"adj_{field}"] = value[field]
        output.append(value)
    return pd.DataFrame(output)


class V5ExecutableTailTests(unittest.TestCase):
    def setUp(self) -> None:
        self.panel = pd.concat(
            [
                make_stock(
                    "TIME",
                    [
                        {"trade_idx": 0, "close": 100, "high": 101, "low": 99},
                        {"trade_idx": 1, "open": 100, "high": 103, "low": 99, "close": 101},
                        {"trade_idx": 2, "open": 101, "high": 103, "low": 98, "close": 102},
                    ],
                ),
                make_stock(
                    "DELAY",
                    [
                        {"trade_idx": 0, "close": 100, "high": 101, "low": 99},
                        {"trade_idx": 1, "open": 100, "high": 101, "low": 95, "close": 96},
                        {"trade_idx": 2, "open": 90, "high": 90, "low": 90, "close": 90, "limit_status": 6},
                        {"trade_idx": 3, "open": 80, "high": 80, "low": 80, "close": 80, "limit_status": 6},
                        {"trade_idx": 4, "open": 75, "high": 78, "low": 74, "close": 76},
                    ],
                ),
                make_stock(
                    "MISSING",
                    [
                        {"trade_idx": 0, "close": 100, "high": 101, "low": 99},
                        {"trade_idx": 1, "open": 100, "high": 101, "low": 98, "close": 99},
                        {"trade_idx": 3, "open": 97, "high": 99, "low": 96, "close": 98},
                    ],
                ),
                make_stock(
                    "GAP_STOP",
                    [
                        {"trade_idx": 0, "close": 100, "high": 101, "low": 99},
                        {"trade_idx": 1, "open": 100, "high": 101, "low": 99, "close": 100},
                        {"trade_idx": 2, "open": 90, "high": 92, "low": 89, "close": 91},
                    ],
                ),
                make_stock(
                    "INTRADAY_STOP",
                    [
                        {"trade_idx": 0, "close": 100, "high": 101, "low": 99},
                        {"trade_idx": 1, "open": 100, "high": 101, "low": 99, "close": 100},
                        {"trade_idx": 2, "open": 100, "high": 101, "low": 90, "close": 95},
                    ],
                ),
                make_stock(
                    "UNBUYABLE",
                    [
                        {"trade_idx": 0, "close": 100, "high": 101, "low": 99},
                        {"trade_idx": 1, "open": 110, "high": 110, "low": 110, "close": 110, "limit_status": 3},
                        {"trade_idx": 2, "open": 110, "high": 112, "low": 108, "close": 111},
                    ],
                ),
            ],
            ignore_index=True,
        )

    def label_row(self, instrument_id: str) -> pd.Series:
        return self.labels.loc[
            (self.labels["instrument_id"] == instrument_id)
            & (self.labels["trade_idx"] == 0)
        ].iloc[0]

    def test_exit_modes_and_tail_extension(self) -> None:
        self.labels, audit = add_executable_tail_labels(
            self.panel, 1, BUY_COST, SELL_COST
        )
        time = self.label_row("TIME")
        self.assertTrue(time["v5_label_valid"])
        self.assertEqual(int(time["v5_exit_reason_code"]), 3)
        self.assertEqual(int(time["v5_exit_idx"]), 2)

        delayed = self.label_row("DELAY")
        self.assertEqual(int(delayed["v5_exit_reason_code"]), 4)
        self.assertEqual(int(delayed["v5_exit_idx"]), 4)
        self.assertEqual(int(delayed["v5_delay_sessions"]), 2)
        self.assertGreater(float(delayed["v5_tail_loss_pct"]), 0.25)

        missing = self.label_row("MISSING")
        self.assertEqual(int(missing["v5_exit_reason_code"]), 4)
        self.assertEqual(int(missing["v5_exit_idx"]), 3)
        self.assertEqual(int(missing["v5_missing_held_sessions"]), 1)

        gap = self.label_row("GAP_STOP")
        self.assertEqual(int(gap["v5_exit_reason_code"]), 1)
        self.assertAlmostEqual(float(gap["v5_exit_scale"]), 90.0, places=5)

        intraday = self.label_row("INTRADAY_STOP")
        self.assertEqual(int(intraday["v5_exit_reason_code"]), 2)
        self.assertAlmostEqual(float(intraday["v5_exit_scale"]), 94.0, places=5)

        unbuyable = self.label_row("UNBUYABLE")
        self.assertFalse(unbuyable["v5_entry_executable"])
        self.assertFalse(unbuyable["v5_label_valid"])
        self.assertGreaterEqual(audit["delayed_time_exits"], 2)
        self.assertEqual(
            audit["delayed_time_exits"],
            audit["exit_reason_counts"]["TIME_EXIT_DELAYED"],
        )

    def test_scalar_and_vectorized_reconciliation(self) -> None:
        self.labels, _ = add_executable_tail_labels(
            self.panel, 1, BUY_COST, SELL_COST
        )
        rows, audit = reconcile_labels(
            self.labels,
            sample_size=100,
            horizon=1,
            buy_cost_rate=BUY_COST,
            sell_cost_rate=SELL_COST,
            seed=7,
        )
        self.assertGreater(len(rows), 0)
        self.assertTrue(audit["passed"], audit)

    def test_entry_day_stop_waits_for_sellable_open(self) -> None:
        stock = make_stock(
            "ENTRY_STOP",
            [
                {"trade_idx": 0, "close": 100, "high": 101, "low": 99},
                {"trade_idx": 1, "open": 100, "high": 101, "low": 90, "close": 94},
                {"trade_idx": 2, "open": 88, "high": 88, "low": 88, "close": 88, "limit_status": 6},
                {"trade_idx": 3, "open": 82, "high": 84, "low": 81, "close": 83},
            ],
        )
        outcome, status = scalar_tail_outcome(
            stock, 0, 0.04, 2, BUY_COST, SELL_COST
        )
        self.assertEqual(status, "FILLED")
        self.assertIsNotNone(outcome)
        assert outcome is not None
        self.assertEqual(outcome["v5_exit_reason_code"], 0)
        self.assertEqual(outcome["v5_exit_idx"], 3)
        self.assertAlmostEqual(outcome["v5_exit_scale"], 82.0)
        self.assertGreater(outcome["v5_tail_loss_pct"], 0.18)

    def test_historical_st_signal_is_not_executable(self) -> None:
        stock = make_stock(
            "HISTORICAL_ST",
            [
                {
                    "trade_idx": 0,
                    "close": 100,
                    "high": 101,
                    "low": 99,
                    "historical_is_st": True,
                },
                {"trade_idx": 1, "open": 100, "high": 101, "low": 99},
                {"trade_idx": 2, "open": 101, "high": 102, "low": 100},
            ],
        )
        labels, audit = add_executable_tail_labels(
            stock, 1, BUY_COST, SELL_COST
        )
        signal = labels.loc[labels["trade_idx"] == 0].iloc[0]
        self.assertFalse(signal["v5_entry_executable"])
        self.assertFalse(signal["v5_label_valid"])
        self.assertEqual(audit["signal_historical_st"], 1)
        outcome, status = scalar_tail_outcome(
            stock, 0, 0.04, 1, BUY_COST, SELL_COST
        )
        self.assertIsNone(outcome)
        self.assertEqual(status, "HISTORICAL_ST")


if __name__ == "__main__":
    unittest.main()
