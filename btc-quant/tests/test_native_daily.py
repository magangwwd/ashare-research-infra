"""Offline regression tests for the separate Binance native 1d data alternative."""
import datetime as dt
import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile

MODULE_PATH = Path(__file__).resolve().parents[1] / 'data' / 'native_daily.py'
spec = importlib.util.spec_from_file_location('btc_native_daily', MODULE_PATH)
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)

def zip_bars(path, days=202, missing=None, duplicate=False):
    start = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
    rows = []
    for n in range(days):
        if n == missing:
            continue
        day = start + dt.timedelta(days=n)
        rows.append(','.join(map(str, [int(day.timestamp()*1000),100,110,90,101,12,0,0,0,0,0,0])))
    if duplicate:
        rows.append(rows[0])
    with zipfile.ZipFile(path, 'w') as out:
        out.writestr('BTCUSDT-1d-test.csv', '\n'.join(rows))

class NativeDailyTests(unittest.TestCase):
    def test_timestamp_ms_us(self):
        d = dt.datetime(2021, 1, 1, tzinfo=dt.timezone.utc)
        self.assertEqual(native.parse_open(int(d.timestamp()*1000)), d.date())
        self.assertEqual(native.parse_open(int(d.timestamp()*1_000_000)), d.date())

    def test_accept_actual_daily_bars_without_inventing_4h(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root)/'test.zip'
            zip_bars(p)
            result = native.build([p], Path(root)/'output')
            self.assertEqual(result['days'], 202)
            self.assertTrue(result['not_equivalent_to_4h_validated'])
            self.assertTrue((Path(root)/'output'/'native_daily_validation.json').exists())

    def test_reject_missing_day(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root)/'test.zip'
            zip_bars(p, missing=37)
            with self.assertRaisesRegex(ValueError, 'no native daily candle'):
                native.build([p], Path(root)/'output')

    def test_reject_duplicate_day(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root)/'test.zip'
            zip_bars(p, duplicate=True)
            with self.assertRaisesRegex(ValueError, 'duplicate daily'):
                native.build([p], Path(root)/'output')

if __name__ == '__main__':
    unittest.main()
