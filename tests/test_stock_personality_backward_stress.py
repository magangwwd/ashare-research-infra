"""Offline regression tests for 2024 backward-year peer comparison."""
import importlib.util
from pathlib import Path
import sys
import unittest

import pandas as pd

SKILL_DIR = Path(__file__).resolve().parents[1] / 'skills/stock-personality-analysis'
sys.path.insert(0, str(SKILL_DIR))
spec = importlib.util.spec_from_file_location('backward_stress_v06', SKILL_DIR / 'backward_stress_v06.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class BackwardStressTest(unittest.TestCase):
    def panel(self):
        dates = pd.bdate_range('2024-01-02', periods=60)
        records = []
        for code in [mod.TARGET, *mod.PEERS]:
            for i, d in enumerate(dates):
                price = 10 + i * .01
                records.append({'ts_code': code, 'trade_date': d,
                                'open': price, 'high': price + .1, 'low': price - .1,
                                'close': price, 'vol': 1000., 'pre_close': price - .01})
        return pd.DataFrame(records)

    def test_aligned_panel(self):
        result = mod.audit(self.panel())
        self.assertEqual(result['sessions'], 60)
        self.assertEqual(len(result['results']), 4)

    def test_missing_peer_day_rejected(self):
        panel = self.panel()
        panel = panel.drop(panel[panel.ts_code == mod.PEERS[0]].index[0])
        with self.assertRaisesRegex(ValueError, 'Missing peer'):
            mod.audit(panel)

    def test_mixed_year_rejected(self):
        panel = self.panel()
        panel.loc[panel.index[0], 'trade_date'] = pd.Timestamp('2025-01-02')
        with self.assertRaisesRegex(ValueError, 'one complete calendar year'):
            mod.audit(panel)


if __name__ == '__main__':
    unittest.main()
