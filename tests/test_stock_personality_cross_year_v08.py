"""Offline tests for frozen 600031 cross-year warm-up audit."""
from pathlib import Path
import sys
import tempfile
import unittest

import pandas as pd

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills/stock-personality-analysis"
sys.path.insert(0, str(SKILL_DIR))
from cross_year_audit_v08 import audit, check_panel, TARGET, PEERS


def synthetic():
    dates = pd.bdate_range("2025-11-17", "2026-02-20")
    rows = []
    for code in [TARGET, *PEERS]:
        prev = 10.0
        for d in dates:
            jump = d == pd.Timestamp("2026-01-02")
            close = 10.0 if d < pd.Timestamp("2026-01-02") else 10.5
            op = 10.0 if jump else close
            rows.append({"ts_code": code, "trade_date": d, "open": op,
                         "high": max(op, close)*1.005,
                         "low": min(op, close)*0.995,
                         "close": close, "pre_close": prev,
                         "vol": 250 if jump else 100, "amount": 1000})
            prev = close
    return pd.DataFrame(rows)


class CrossYearAuditTests(unittest.TestCase):
    def test_early_january_signal_uses_previous_december(self):
        df = synthetic()
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            old = df[df.trade_date.dt.year == 2025].copy()
            old["trade_date"] = old.trade_date - pd.DateOffset(years=2)
            paths = [p/"2023.csv", p/"2025.csv", p/"2026.csv"]
            old.to_csv(paths[0], index=False)
            df[df.trade_date.dt.year == 2025].to_csv(paths[1], index=False)
            df[df.trade_date.dt.year == 2026].to_csv(paths[2], index=False)
            result = audit(paths)
        item = next(x for x in result["results"]
                    if x["year"] == 2026 and x["signal"] == "D_breakout")
        self.assertEqual(item["year_reset_trade_n"], 0)
        self.assertGreaterEqual(item["continuous_warmup_trade_n"], 1)
        self.assertIn("2026-01-02", item["added_by_prior_year_warmup"])

    def test_missing_peer_session_rejected(self):
        df = synthetic()
        df = df[~((df.ts_code == PEERS[0]) &
                  (df.trade_date == pd.Timestamp("2026-01-02")))]
        with self.assertRaisesRegex(ValueError, "calendars"):
            check_panel(df)

    def test_duplicate_source_rows_rejected(self):
        df = synthetic()
        with tempfile.TemporaryDirectory() as td:
            paths = [Path(td)/f"{i}.csv" for i in range(3)]
            for p in paths:
                df.to_csv(p, index=False)
            with self.assertRaisesRegex(ValueError, "Overlapping source"):
                audit(paths)


if __name__ == "__main__":
    unittest.main()
