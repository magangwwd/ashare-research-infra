"""Offline tests for one-position portfolio overlap sensitivity; no market data needed."""
from pathlib import Path
import sys
import unittest

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills/stock-personality-analysis"
sys.path.insert(0, str(SKILL_DIR))
from portfolio_overlap_v09 import analyze


def sample():
    def t(entry, exit_date, pnl):
        return {'signal_date':'2025-01-01','entry_date':entry,'exit_date':exit_date,
                'net_return_pct':pnl/400,'net_pnl_yuan':pnl,'year':2025}
    return {'sessions':20,'rows':100,'results':[
        {'signal':'D_breakout','hold_sessions':5,'trades':[t('2025-01-02','2025-01-08',100)]},
        {'signal':'G_upper_wick','hold_sessions':3,'trades':[t('2025-01-02','2025-01-06',-50)]},
        {'signal':'B_breakdown','hold_sessions':5,'trades':[t('2025-01-08','2025-01-12',80)]},
        {'signal':'F_tight_lowvol','hold_sessions':5,'trades':[]}]}


class PortfolioOverlapTests(unittest.TestCase):
    def test_same_open_priority_and_exit_reentry(self):
        r=analyze(sample())
        self.assertEqual(r['pattern_independent']['n'],3)
        self.assertEqual(r['one_position_total']['n'],2)
        self.assertEqual(r['blocked_due_to_capital']['n'],1)
        self.assertEqual(r['one_position_total']['sum_net_pnl_yuan'],180)
    def test_max_concurrency(self):
        r=analyze(sample())
        self.assertEqual(r['max_concurrent_pattern_positions'],2)
        self.assertEqual(r['same_open_multiple_entries'],{'2025-01-02':2})
    def test_bad_dates_rejected(self):
        x=sample(); x['results'][0]['trades'][0]['exit_date']='2025-01-01'
        with self.assertRaises(ValueError): analyze(x)
    def test_unexpected_signal_rejected(self):
        x=sample();x['results'][0]['signal']='unknown'
        with self.assertRaises(ValueError): analyze(x)

if __name__=='__main__':unittest.main()
