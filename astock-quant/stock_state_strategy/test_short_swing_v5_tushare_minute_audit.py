"""Offline tests for Tushare-only V5 15-minute necessary-trigger audit."""
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

import pandas as pd
import short_swing_v5_tushare_minute_audit as audit


def fixture():
    clock=sorted(audit.TIMES | {"09:30"})
    rows=[]
    for date in ["2026-01-07","2026-01-09"]:
        for tm in clock:
            rows.append({"ts_code":"600598.SH","trade_time":date+" "+tm+":00",
                         "trade_date":date,"open":10.0,"high":10.6,"low":9.6,"close":10.1})
    variants=[]
    for name in ["V2_LOOSE_UNCHANGED","LESS_EXTENDED_BREAKOUT_FIRST","LESS_RECENT_RUNUP_FIRST"]:
        variants.append({"name":name,"trades":[
            {"symbol":"600598.SH","entry_date":"2026-01-07","exit_date":"2026-01-09",
             "entry_price_raw":10.01,"exit_reason":"TARGET_INTRADAY_ASSUMED",
             "corporate_action_proxy":False}]})
    return {"version":"MAINBOARD_3TO5_CAUSAL_RANKING_PAPER_V5",
        "period":{"start":"2024-01-01","end":"2026-10-16"},"variants":variants},pd.DataFrame(rows)


class AuditTests(unittest.TestCase):
    def test_full_january_trading_day_and_touch_without_fill_claim(self):
        v5,minute=fixture()
        r=audit.inspect_v5_january(v5,minute,0)
        self.assertEqual(r["both_entry_exit_dates_17_tushare_records"],3)
        self.assertEqual(r["trigger_checked"],3)
        self.assertEqual(r["trigger_inconsistent"],0)
        self.assertEqual(r["verified_actual_fills"],0)
    def test_fails_when_required_15min_bar_is_missing(self):
        v5,minute=fixture()
        q=minute.drop(minute.index[0])
        r=audit.inspect_v5_january(v5,q,1)
        self.assertEqual(r["both_entry_exit_dates_17_tushare_records"],0)
    def test_contradictory_target_is_reported_not_accepted(self):
        v5,minute=fixture()
        minute.loc[minute["trade_date"]=="2026-01-09","high"]=10.3
        r=audit.inspect_v5_january(v5,minute,0)
        self.assertEqual(r["trigger_inconsistent"],3)
        self.assertTrue(all(t["bar_trigger_consistent"] is False for t in r["details"]))
    def test_future_trade_group_or_wrong_version_fails(self):
        v5,minute=fixture()
        v5["version"]="MISLABELED_PROFIT"
        with self.assertRaises(ValueError):audit.inspect_v5_january(v5,minute,0)
    def test_duplicate_minute_key_fails(self):
        v5,minute=fixture()
        minute=pd.concat([minute,minute.iloc[[0]]],ignore_index=True)
        with self.assertRaises(ValueError):audit.inspect_v5_january(v5,minute,0)
    def test_missing_expected_partition_is_reported(self):
        # The official full 64-partition contract must fail closed before
        # trusting any synthetic / incomplete manifest.
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                audit.validate_file_manifest({"validated":True,
                 "version":audit.PINNED_VERSION,"files":[]},Path(folder))
    def test_bad_ohlc_fails(self):
        v5,minute=fixture()
        minute.loc[0,"low"]=11.9
        with self.assertRaises(ValueError):audit.inspect_v5_january(v5,minute,0)


if __name__=="__main__":unittest.main()
