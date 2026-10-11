import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import pandas as pd

from generate_quality_h20_report import (
    parse_schedule_flag,
    rank_fraction,
    rank_score_from_rank,
    select_diversified_top,
)
from generate_latest_report import build_daily_commands
from lightgbm_return_top3.feature_engine import SIGNAL_ELIGIBILITY_COLUMN, TARGET_COLUMN
from lightgbm_return_top3.model_backtest import _build_training_mask
from update_daily import (
    append_to_csv,
    enforce_intraday_guard,
    is_intraday_guard_window,
    validate_ohlcv,
)


class DailyDataGuardTests(unittest.TestCase):
    def test_validate_ohlcv_rejects_open_above_high(self) -> None:
        frame = pd.DataFrame(
            {
                "Close": [10.0],
                "High": [10.2],
                "Low": [9.8],
                "Open": [10.3],
                "Volume": [1000],
            },
            index=pd.to_datetime(["2026-05-29"]),
        )

        with self.assertRaisesRegex(ValueError, "2026-05-29"):
            validate_ohlcv(frame, "test")

    def test_overlap_refresh_preserves_turnover_rate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "000001.SZ.csv"
            existing = pd.DataFrame(
                {
                    "Date": ["2026-04-02", "2026-05-29"],
                    "Close": [11.2, 10.0],
                    "High": [11.3, 10.2],
                    "Low": [11.1, 9.8],
                    "Open": [10.9, 10.1],
                    "Volume": [900, 1000],
                    "TurnoverRate": [0.40, 0.47],
                }
            )
            existing.to_csv(path, index=False)
            refreshed = pd.DataFrame(
                {
                    "Close": [10.0],
                    "High": [10.2],
                    "Low": [9.8],
                    "Open": [9.9],
                    "Volume": [1200],
                },
                index=pd.to_datetime(["2026-05-29"]),
            )

            append_to_csv("000001.SZ", refreshed, temp_dir, "2026-05-29")

            result = pd.read_csv(path)
            refreshed_row = result[result["Date"] == "2026-05-29"].iloc[0]
            self.assertEqual(refreshed_row["Open"], 9.9)
            self.assertEqual(refreshed_row["Volume"], 1200)
            self.assertEqual(refreshed_row["TurnoverRate"], 0.47)

    def test_intraday_guard_blocks_weekday_session_without_force(self) -> None:
        intraday = datetime(2026, 7, 3, 10, 0)

        self.assertTrue(is_intraday_guard_window(intraday))
        with self.assertRaisesRegex(RuntimeError, "--force"):
            enforce_intraday_guard(force=False, now=intraday)
        enforce_intraday_guard(force=True, now=intraday)

    def test_intraday_guard_allows_after_close(self) -> None:
        self.assertFalse(is_intraday_guard_window(datetime(2026, 7, 3, 15, 6)))


class ReportGuardTests(unittest.TestCase):
    def test_parse_schedule_flag_handles_false_string(self) -> None:
        self.assertFalse(parse_schedule_flag(pd.Series(["False", "False"])))

    def test_diversified_top_limits_industry(self) -> None:
        candidates = pd.DataFrame(
            [
                {"ts_code": "A", "industry": "半导体", "confidence": 90, "kronos_score": 90, "lgbm_score": 90},
                {"ts_code": "B", "industry": "半导体", "confidence": 89, "kronos_score": 89, "lgbm_score": 89},
                {"ts_code": "C", "industry": "通信设备", "confidence": 88, "kronos_score": 88, "lgbm_score": 88},
            ]
        )

        result = select_diversified_top(candidates, max_rows=2, max_per_industry=1)

        self.assertEqual(result["ts_code"].tolist(), ["A", "C"])

    def test_rank_score_handles_single_name_universe(self) -> None:
        ranks = pd.Series([1.0])

        self.assertEqual(rank_fraction(ranks, 1).iloc[0], 1.0)
        self.assertEqual(rank_score_from_rank(ranks, 1).iloc[0], 100.0)
        self.assertTrue(pd.isna(rank_fraction(ranks, 0).iloc[0]))
        self.assertTrue(pd.isna(rank_score_from_rank(ranks, 0).iloc[0]))


class LightGBMTrainingGuardTests(unittest.TestCase):
    def test_training_mask_purges_tail_label_overlap_window(self) -> None:
        dates = pd.bdate_range("2024-01-01", periods=30)
        index = pd.MultiIndex.from_product(
            [dates, ["000001.SZ"]],
            names=["Date", "ts_code"],
        )
        panel = pd.DataFrame(
            {
                SIGNAL_ELIGIBILITY_COLUMN: True,
                TARGET_COLUMN: 0.01,
            },
            index=index,
        )

        mask = _build_training_mask(panel, train_end=dates[-1], train_start=dates[0])
        kept_dates = panel.index.get_level_values("Date")[mask]

        self.assertEqual(kept_dates.max(), dates[-21])
        self.assertNotIn(dates[-20], kept_dates)


class LatestReportWorkflowTests(unittest.TestCase):
    def test_latest_report_does_not_force_daily_by_default(self) -> None:
        args = type("Args", (), {"force_daily": False})()

        commands = dict(build_daily_commands(args))

        self.assertNotIn("--force", commands["daily prices"])

    def test_latest_report_can_explicitly_force_daily(self) -> None:
        args = type("Args", (), {"force_daily": True})()

        commands = dict(build_daily_commands(args))

        self.assertIn("--force", commands["daily prices"])


if __name__ == "__main__":
    unittest.main()
