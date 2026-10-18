"""Tests for isolated real Tushare minutes (no network, no secrets)."""
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

try:
    import numpy as np
    import pandas as pd
    from ai_research.tushare_600351_minute import (
        normalize_response, query_live, monthly_windows, build, SourceUnavailable,
    )
    from ai_research.kline_15m_bridge import WANTED
except ModuleNotFoundError as exc:
    if exc.name not in ("pandas","numpy","pyarrow"):
        raise
    pd=np=None


SLOTS=["09:45","10:00","10:15","10:30","10:45","11:00","11:15","11:30",
       "13:15","13:30","13:45","14:00","14:15","14:30","14:45","15:00"]


def sample(day, symbol="600351.SH"):
    return pd.DataFrame({
        "ts_code":[symbol]*16,
        "trade_time":[day+" "+x+":00" for x in SLOTS],
        "open":[10.00]*16,
        "high":[10.05]*16,
        "low":[9.95]*16,
        "close":[10.01]*16,
        "vol":[1000.0]*16,
        "amount":[10000.0]*16,
    })


class FakeAPI:
    def __init__(self, empty=False, forbidden=False):
        self.requests=[]
        self.empty=empty
        self.forbidden=forbidden

    def query(self,endpoint,**kwargs):
        self.requests.append((endpoint,kwargs))
        if self.forbidden:
            raise RuntimeError("Permission denied for stk_mins")
        if self.empty:
            return pd.DataFrame()
        begin=date.fromisoformat(kwargs["start_date"][:10])
        end=date.fromisoformat(kwargs["end_date"][:10])
        rows=[]
        for day in pd.bdate_range(begin,end):
            rows.append(sample(str(day.date())))
        return pd.concat(rows,ignore_index=True)


@unittest.skipIf(pd is None,"Optional pandas/pyarrow only in dedicated 15m CI")
class TushareMinuteTests(unittest.TestCase):
    def test_calendar_batches_and_fixed_bounds(self):
        windows=list(monthly_windows())
        self.assertEqual(str(windows[0][0]),"2023-10-16")
        self.assertEqual(str(windows[-1][1]),"2026-10-16")
        self.assertEqual(len(windows),37)

    def test_mocked_live_probe_and_all_months(self):
        api=FakeAPI()
        with TemporaryDirectory() as td:
            import pyarrow
            result=build(api,Path(td),start=date(2026,9,1),
                         end=date(2026,10,16),sleeper=lambda n:None)
            self.assertEqual(result["output_partitions"],2)
            self.assertGreater(result["total_raw_bars"],100)
            self.assertEqual(len(api.requests),2)
            self.assertTrue(all(r[0]=="stk_mins" for r in api.requests))
            self.assertEqual(result["source_permission_verified_with_nonempty_probe"],True)
            self.assertTrue((Path(td)/"source_manifest.json").exists())

    def test_permission_denied_no_false_success(self):
        api=FakeAPI(forbidden=True)
        with self.assertRaisesRegex(SourceUnavailable,"Permission denied"):
            query_live(api,date(2026,10,15),date(2026,10,16),
                       sleeper=lambda n:None)

    def test_empty_response_no_false_permission(self):
        with self.assertRaisesRegex(SourceUnavailable,"ZERO rows"):
            query_live(FakeAPI(empty=True),date(2026,10,15),
                       date(2026,10,16),sleeper=lambda n:None)

    def test_near_limit_rejects(self):
        df=pd.concat([sample("2026-10-15")]*500,ignore_index=True)
        with self.assertRaisesRegex(SourceUnavailable,"8000-row"):
            normalize_response(df,date(2026,10,15),date(2026,10,15))

    def test_invalid_bar_timestamp_rejected(self):
        df=sample("2026-10-15")
        df.loc[0,"trade_time"]="2026-10-15 12:15:00"
        with self.assertRaisesRegex(SourceUnavailable,"timestamps"):
            normalize_response(df,date(2026,10,15),date(2026,10,15))

    def test_outside_date_rejected(self):
        with self.assertRaisesRegex(SourceUnavailable,"outside"):
            normalize_response(sample("2026-10-14"),date(2026,10,15),
                               date(2026,10,15))

    def test_vol_expansion_in_analysis(self):
        self.assertEqual(WANTED[0],"volatility_expansion")


if __name__=="__main__":
    unittest.main()
