"""Offline deterministic regression tests for 600031 v1.0 account ledger."""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"skills/stock-personality-analysis"))
from portfolio_equity_v10 import simulate

class PortfolioEquityV10Tests(unittest.TestCase):
    def setUp(self):
        self.prices={
            "2026-01-01":{"open":10.,"high":11.,"low":9.,"close":10.},
            "2026-01-02":{"open":10.,"high":11.,"low":9.,"close":10.5},
            "2026-01-03":{"open":11.,"high":11.5,"low":10.,"close":11.}}
        self.trade={"signal_date":"2026-01-01","entry_date":"2026-01-02",
                    "exit_date":"2026-01-03","entry_open":10.,"exit_open":11.,
                    "signal":"D_breakout"}
    def test_cash_ledger_and_mark_to_market(self):
        r=simulate([self.trade],self.prices)
        self.assertEqual(r["trades_executed"],1)
        self.assertEqual(r["days_in_position"],1)
        self.assertEqual(r["daily_equity"][-1]["shares"],0)
        self.assertAlmostEqual(r["net_profit_yuan"],r["executed_trades"][0]["realized_net_pnl"],places=2)
        self.assertTrue(all(x["cash"]>=0 for x in r["daily_equity"]))
    def test_minimum_commission_small_account(self):
        a=simulate([self.trade],self.prices,initial=2500)
        b=simulate([self.trade],self.prices,initial=2500,minimum_fee=5)
        self.assertLess(b["ending_equity_yuan"],a["ending_equity_yuan"])
    def test_no_trades(self):
        r=simulate([],self.prices)
        self.assertEqual(r["total_return_pct"],0)
        self.assertEqual(r["max_drawdown_pct"],0)
    def test_invalid_price_and_overlap(self):
        with self.assertRaises(ValueError):
            simulate([{**self.trade,"entry_open":9.9}],self.prices)
        with self.assertRaises(ValueError):
            simulate([self.trade,self.trade],self.prices)

if __name__=="__main__":unittest.main()
