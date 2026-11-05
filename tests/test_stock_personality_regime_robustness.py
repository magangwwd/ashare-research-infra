"""Offline tests for v0.5 peer-relative regime audit; no network or market data."""
import importlib.util
from pathlib import Path
import unittest
import pandas as pd

SCRIPT = Path(__file__).resolve().parents[1] / 'skills/stock-personality-analysis/regime_robustness_v05.py'
spec = importlib.util.spec_from_file_location('regime_robustness_v05', SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class RegimeRobustnessTest(unittest.TestCase):
    def setUp(self):
        dates = pd.bdate_range('2025-01-02', periods=70)
        records = []
        for code in ['600031.SH', '000157.SZ', '000425.SZ', '600761.SH', '601100.SH']:
            for i, d in enumerate(dates):
                o = 10 + i * (.03 if code == '600031.SH' else .02)
                records.append({'ts_code': code, 'trade_date': d.strftime('%Y-%m-%d'),
                                'open': o, 'high': 11 + i * .04,
                                'low': 9 + i * .01, 'close': o, 'vol': 10000})
        self.panel = pd.DataFrame(records)
        stock = {}
        for h in (3, 5):
            stock[str(h)] = {}
            for pattern, _ in mod.PATTERNS:
                trades = []
                for i in (10, 18, 26, 34, 42):
                    op = 10 + .03 * (i + 1)
                    ex = 10 + .03 * (i + h + 1)
                    trades.append({'signal_date': dates[i].strftime('%Y-%m-%d'),
                                   'entry_date': dates[i + 1].strftime('%Y-%m-%d'),
                                   'exit_date': dates[i + h + 1].strftime('%Y-%m-%d'),
                                   'gross_return_pct': round(100 * (ex / op - 1), 4),
                                   'year': 2025})
                stock[str(h)][pattern] = {'target_trades': trades}
        self.execution = {'target': '600031.SH', 'stocks': {'600031.SH': stock}}

    def test_deterministic(self):
        a = mod.analyze(self.panel, self.execution, seed=123, draws=100)
        self.assertEqual(a, mod.analyze(self.panel, self.execution, seed=123, draws=100))
        self.assertEqual(a['groups'][0]['n'], 5)
        self.assertGreater(a['groups'][0]['mean_alpha_pp'], 0)
        self.assertEqual(len(a['groups'][0]['bootstrap_95_pct_interval_pp']), 2)

    def test_exact_sign_p(self):
        self.assertEqual(mod.exact_sign_p(0, 5), .0625)
        self.assertEqual(mod.exact_sign_p(2, 5), 1)

    def test_missing_peer_rejected(self):
        with self.assertRaisesRegex(ValueError, 'aligned sessions'):
            mod.analyze(self.panel.iloc[1:], self.execution, draws=100)

    def test_calendar_rejected(self):
        self.execution['stocks']['600031.SH']['5']['D_breakout']['target_trades'][0]['exit_date'] = '2025-01-03'
        with self.assertRaisesRegex(ValueError, 'calendar mismatch'):
            mod.analyze(self.panel, self.execution, draws=100)

    def test_duplicate_rejected(self):
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            mod.analyze(pd.concat([self.panel, self.panel.iloc[:1]]), self.execution, draws=100)

    def test_draws_rejected(self):
        with self.assertRaisesRegex(ValueError, 'draws'):
            mod.analyze(self.panel, self.execution, draws=99)
