"""Optional-numerical focused QA; repo's lean global CI may lack pandas/numpy."""
import unittest
try:
    import numpy as np
    import pandas as pd
    from ai_research.kline_15m_bridge import (
        analyze_intraday_minute_rows, compute, write, SLOTS,
    )
except ModuleNotFoundError as e:
    if e.name not in ("numpy","pandas"):
        raise
    pd = np = None


def single_session(day, code="600351.SH", auction=True, broken=False):
    stamps = (["09:30"] if auction else []) + SLOTS
    ts=[pd.Timestamp(day+" "+t) for t in stamps]
    close=np.linspace(10.00,10.18,len(ts))
    frame=pd.DataFrame({
        "ts_code":[code]*len(ts), "trade_time":ts,
        "open":close-.01, "high":close+.03, "low":close-.04,
        "close":close,"vol":[1000.0]*len(ts)})
    if broken:
        frame.loc[frame.trade_time.dt.strftime("%H:%M")=="14:15","high"]=1
    return frame


@unittest.skipIf(pd is None,"Dedicated kline CI installs pandas and numpy")
class MinuteGateTests(unittest.TestCase):
    def test_accepts_16_bars_skips_auction(self):
        p = single_session("2026-10-15")
        out,info=analyze_intraday_minute_rows(p)
        self.assertEqual(info["accepted_days"],1)
        self.assertEqual(info["auction_rows_excluded"],1)
        self.assertEqual(info["expected_bars_per_accepted_day"],16)
        self.assertAlmostEqual(out.iloc[0]["afternoon_vol_share"],.5)

    def test_missing_one_bar_rejects_whole_day(self):
        p=single_session("2026-10-15")
        p=p.loc[p.trade_time.dt.strftime("%H:%M")!="14:30"]
        out,meta=analyze_intraday_minute_rows(p)
        self.assertTrue(out.empty)
        self.assertEqual(meta["rejected_days"],1)

    def test_bad_ohlc_rejects_day(self):
        out,meta=analyze_intraday_minute_rows(single_session(
            "2026-10-15",broken=True))
        self.assertTrue(out.empty)
        self.assertEqual(meta["rejected_days"],1)

    def test_rejects_duplicated_minute_key(self):
        p=single_session("2026-10-15",auction=False)
        p=pd.concat([p,p.iloc[[3]]],ignore_index=True)
        with self.assertRaisesRegex(ValueError,"Duplicate"):
            analyze_intraday_minute_rows(p)

    def test_rejects_missing_stock(self):
        p=single_session("2026-10-15",code="000001.SZ")
        with self.assertRaisesRegex(ValueError,"absent"):
            analyze_intraday_minute_rows(p)

    def test_rejects_no_volume_entire_day(self):
        p=single_session("2026-10-15")
        p["vol"]=0
        out,meta=analyze_intraday_minute_rows(p)
        self.assertTrue(out.empty)

    def test_no_source_returns_empty_safely(self):
        out,meta=analyze_intraday_minute_rows(pd.DataFrame())
        self.assertTrue(out.empty)
        self.assertEqual(meta["accepted_days"],0)

    def test_cross_vendor_close_mismatch_is_audited_not_imputed(self):
        dates=pd.bdate_range("2023-05-02","2026-10-16")
        n=len(dates)
        daily=pd.DataFrame({
            "open":np.full(n,10.0), "high":np.full(n,10.2),
            "low":np.full(n,9.8), "close":np.full(n,10.0),
            "adjustment_factor":np.ones(n),
            "volume_shares":np.full(n,100000.0)},index=dates)
        out=compute(daily,single_session("2026-10-15"))
        self.assertEqual(out["source_quality"]["daily_close_mismatch_days"],1)
        self.assertEqual(out["minute_valid_days_aligned"],0)
        self.assertEqual(out["status"],"INCOMPLETE_MINUTE_COVERAGE_NO_CONCLUSIONS")
        self.assertEqual(out["source_quality"]["daily_close_mismatch_examples"][0]["date"],
                         "2026-10-15")

    def test_report_output_preserves_no_data_status(self):
        import tempfile
        from pathlib import Path
        record={"version":"KLINE_600351_15M_JOIN_V1","symbol":"600351.SH",
                "as_of":"2026-10-16","daily_valid_days":728,
                "minute_daily_coverage":0.0,"status":"NO_15M_DATA",
                "risk":["No parquet"],"patterns":{},
                "authorization":"RESEARCH_ONLY_NO_TRADING"}
        with tempfile.TemporaryDirectory() as folder:
            write(record,Path(folder))
            self.assertEqual(set(p.name for p in Path(folder).iterdir()),
                             {"report.md","statistics.json","manifest.json"})
            txt=(Path(folder)/"report.md").read_text()
            self.assertIn("NO_15M_DATA",txt)
            self.assertNotIn("EXPLORATORY_COVERAGE_PASSED",txt)


if __name__=="__main__":
    unittest.main()
