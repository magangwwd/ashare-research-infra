"""No-network Baostock source adapter tests; never fetch provider on CI."""
import unittest
try:
    import pandas as pd
    from ai_research.baostock_600351_minute import (
        normalize_row_batch, save, TARGET_DEST, FIELDS,
    )
except ModuleNotFoundError as exc:
    if exc.name not in ("pandas",):
        raise
    pd = None


def make_source():
    return pd.DataFrame([{
        "date":"2026-10-15","time":"20261015094500000",
        "code":"sh.600351","open":"6.80","high":"6.91","low":"6.78",
        "close":"6.86","volume":"15000","amount":"102300","adjustflag":"3"},
        {"date":"2026-10-15","time":"20261015100000000",
         "code":"sh.600351","open":"6.86","high":"6.93","low":"6.82",
         "close":"6.89","volume":"17000","amount":"116500","adjustflag":"3"}])


@unittest.skipIf(pd is None,"Optional pandas available in dedicated study CI")
class BaostockAdapterTests(unittest.TestCase):
    def test_vendor_times_and_units(self):
        x=normalize_row_batch(make_source())
        self.assertEqual(len(x),2)
        self.assertEqual(x["ts_code"].iloc[0],TARGET_DEST)
        self.assertEqual(str(x.trade_time.iloc[0]),"2026-10-15 09:45:00")
        self.assertEqual(x.vol.iloc[0],15000)

    def test_adjusted_source_not_accepted(self):
        x=make_source()
        x.loc[0,"adjustflag"]="2"
        with self.assertRaisesRegex(ValueError,"Non-adjusted"):
            normalize_row_batch(x)

    def test_timestamp_venue_date_consistent(self):
        x=make_source()
        x.loc[0,"date"]="2026-10-16"
        with self.assertRaisesRegex(ValueError,"Date/bar"):
            normalize_row_batch(x)

    def test_duplicate_bar_time_refused(self):
        x=make_source()
        x.loc[1,"time"]=x.loc[0,"time"]
        with self.assertRaisesRegex(ValueError,"Duplicate"):
            normalize_row_batch(x)

    def test_save_year_month_and_manifest(self):
        import tempfile
        from pathlib import Path
        x=normalize_row_batch(make_source())
        with tempfile.TemporaryDirectory() as folder:
            r=save(x,[{"start":"2026-10-01","end":"2026-10-16"}],Path(folder))
            self.assertEqual(r["rows"],2)
            self.assertEqual(r["partitions"],1)
            self.assertTrue((Path(folder)/"kline_15m/year=2026/month=10/data.parquet").exists())
            self.assertTrue(r["files_sha256"])
            self.assertTrue(r["non_adjusted"])


if __name__=="__main__":
    unittest.main()
