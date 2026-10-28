"""Offline guardrails: never label Sep-Dec 2023 as full-year evidence."""
import importlib.util
from pathlib import Path
import sys
import unittest
import pandas as pd

SKILL_DIR = Path(__file__).resolve().parents[1] / 'skills/stock-personality-analysis'
sys.path.insert(0, str(SKILL_DIR))
spec = importlib.util.spec_from_file_location('coverage_guard_v07', SKILL_DIR / 'coverage_guard_v07.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

CODES = ['600031.SH', '000157.SZ', '000425.SZ', '600761.SH', '601100.SH']

class PartialYearCoverageTest(unittest.TestCase):
    def panel(self):
        dates = pd.bdate_range('2023-09-06', periods=32)
        return pd.DataFrame([{'ts_code': code, 'trade_date': d, 'open': 10., 'high': 10.1,
                              'low': 9.9, 'close': 10., 'vol': 1000., 'pre_close': 10.}
                             for code in CODES for d in dates])

    def test_partial_year_label(self):
        r = mod.inspect(self.panel(), 2023, [9, 10])
        self.assertTrue(r['partial_year'])
        self.assertEqual(r['coverage_months'], [9, 10])
        self.assertEqual(r['rows'], 160)

    def test_reject_false_full_year_claim(self):
        with self.assertRaisesRegex(ValueError, 'Month coverage mismatch'):
            mod.inspect(self.panel(), 2023, range(1, 13))

    def test_reject_misaligned_peer(self):
        p = self.panel()
        p = p[~((p.ts_code == '600031.SH') & (p.trade_date == p.trade_date.max()))]
        with self.assertRaisesRegex(ValueError, 'calendars'):
            mod.inspect(p, 2023, [9, 10])

    def test_reject_duplicate_date(self):
        p = self.panel()
        p = pd.concat([p, p.iloc[:1]], ignore_index=True)
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            mod.inspect(p, 2023, [9, 10])

if __name__ == '__main__':
    unittest.main()
