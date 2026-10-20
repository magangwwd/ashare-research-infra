"""Synthetic validation of the 5-trade 15-minute downloader; offline/no broker."""
import tempfile
from pathlib import Path
import unittest
from short_swing_3to5_v4_minute_sample_probe import normalize, summarize_rows, save_probe, EXPECTED

def makebar(t='20260127094500000'):
    return {'date':t[:4]+'-'+t[4:6]+'-'+t[6:8],
            'time':t,'code':'sz.002688',
            'open':'10','high':'10.4','low':'9.9','close':'10.2',
            'volume':'100','amount':'1000'}

class MinuteProbe(unittest.TestCase):
    def test_time_and_ohlcv(self):
        o=normalize(makebar())
        self.assertEqual(o['bar_end_hhmm'],'09:45')
    def test_wrong_day_is_rejected(self):
        b=makebar();b['date']='2026-01-28'
        with self.assertRaises(ValueError):normalize(b)
    def test_ohlc_corruption_is_rejected(self):
        b=makebar();b['low']='10.5'
        with self.assertRaises(ValueError):normalize(b)
    def test_full_session_completeness_only_16_standard_bars(self):
        bars=[normalize(makebar('20260127'+t.replace(':','')+'00000')) for t in EXPECTED]
        x=summarize_rows(bars,'2026-01-27','2026-01-27')
        self.assertEqual(x['status'],'COMPLETE_REQUESTED_DAYS')
        self.assertTrue(x['entry_and_exit_days_complete'])
        bars.pop()
        self.assertEqual(summarize_rows(bars,'2026-01-27','2026-01-27')['status'],
                         'PARTIAL_OR_MISSING_SESSION')
    def test_missing_session_is_not_success(self):
        self.assertEqual(summarize_rows([],'2026-01-27','2026-01-30')['status'],'NO_BARS_RETURNED')
    def test_artifact_integrity_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'out'
            manifest=save_probe(p,[{'symbol':'002688.SZ','entry_date':'2026-01-27',
                'exit_date':'2026-01-30','historical_v2_exit_reason':'STOP_INTRADAY_ASSUMED',
                'bars':[{'vendor_symbol':'002688.SZ',**normalize(makebar())}],
                'quality':summarize_rows([normalize(makebar())],
                                        '2026-01-27','2026-01-30')}])
            self.assertEqual(manifest['rows'],1)
            self.assertTrue((p/'manifest.json').exists())
            with self.assertRaises(FileExistsError):save_probe(p,[])
if __name__=='__main__':unittest.main()
