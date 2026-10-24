"""Adversarial evidence tests for 15m shadow-only mode, independent from the market feed."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from short_swing_15m_audit import EXPECTED, audit_source, validate_one_day


def bars():
    day="2026-10-16"
    return pd.DataFrame({
        "ts_code":["600351.SH"]*16,
        "trade_time":pd.to_datetime([day+" "+s for s in EXPECTED]),
        "open":[10.]*16, "high":[10.01]*16, "low":[9.99]*16, "close":[10.]*16,
        "vol":[100.]*12+[150.]*4, "amount":[1000.]*16,
    })


class MinuteContract(unittest.TestCase):
    def test_valid_fully_closed_bar_set(self):
        row=validate_one_day(bars(),10.)
        self.assertEqual(row["bar_count"],16)
        self.assertAlmostEqual(row["last_hour_vol_fraction"],1/3,places=5)
        self.assertTrue(row["last_hour_ge30_shadow"])

    def test_incomplete_duplicate_auction_bad_close_bad_volume(self):
        frame=bars()
        changes=[
            frame.iloc[:-1],pd.concat([frame,frame.tail(1)]),
            frame.assign(trade_time=frame.trade_time.shift(1)),
            frame.assign(vol=-1.),frame.assign(close=0.)
        ]
        for altered in changes:
            with self.assertRaises(ValueError):
                validate_one_day(altered,10.)
        with self.assertRaises(ValueError):
            validate_one_day(frame,9.8)

    def test_full_manifest_sha_and_unknown_symbol_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            part=root/"kline_15m/year=2026/month=10/data.parquet"
            part.parent.mkdir(parents=True)
            part.write_bytes(b"offline-fixture")
            m={"version":"BAOSTOCK_600351_15M_PROBE_V1",
               "symbol":"600351.SH","non_adjusted":True,
               "authorization":"RESEARCH_ONLY_NO_TRADING",
               "bar_time_basis":"Asia/Shanghai 15m bar-end",
               "partitions":1,"rows":16,
               "files_sha256":{str(part.relative_to(root)):hashlib.sha256(part.read_bytes()).hexdigest()}}
            manifest=root/"provider_manifest.json"
            manifest.write_text(json.dumps(m))
            daily=pd.DataFrame({"trade_date":[pd.Timestamp("2026-10-16")],
                                "vendor_symbol":["600351.SH"],"close":[10.]})
            signals={pd.Timestamp("2026-10-16"):[("600351.SH",0.01),("600001.SH",0.01)]}
            executed=[{"symbol":"600351.SH","signal_date":"2026-10-16","pnl_cny":5.}]
            with patch("pandas.read_parquet",return_value=bars()):
                result=audit_source(root,daily,signals,executed)
            self.assertEqual(result["verified_minute_signal_days"],1)
            # A warmed-up signal predating the requested interval is audit-only, not in-period.
            with patch("pandas.read_parquet",return_value=bars()):
                late=audit_source(root,daily,signals,executed,start="2026-10-17")
            self.assertEqual(late["stock_daily_signal_days"],0)
            self.assertEqual(late["verified_minute_signal_days"],0)
            self.assertFalse(result["marketwide_minute_coverage_verified"])
            self.assertFalse(result["incremental_15m_profitability_proven"])
            self.assertEqual(result["matched_paper_trades"][0]["pnl_cny"],5.)
            part.write_bytes(b"tampered")
            with self.assertRaises(ValueError):
                audit_source(root,daily,signals,executed)
            m["symbol"]="000001.SZ"
            manifest.write_text(json.dumps(m))
            with self.assertRaises(ValueError):
                audit_source(root,daily,signals,executed)


if __name__=="__main__":
    unittest.main()
