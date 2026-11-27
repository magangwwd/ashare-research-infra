"""Offline synthetic regression tests for dividend cash v1.1 (no market data in repo)."""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"skills/stock-personality-analysis"))
from dividend_cash_v11 import validate_events,simulate
from portfolio_equity_v10 import simulate as baseline_simulate

class DividendCashV11Tests(unittest.TestCase):
    def setUp(self):
        self.prices={
            "2024-06-20":{"open":10.,"high":10.,"low":10.,"close":10.,"pre_close":10.},
            "2024-06-21":{"open":9.8,"high":10.,"low":9.8,"close":9.9,"pre_close":9.8},
            "2024-06-24":{"open":10.,"high":10.,"low":10.,"close":10.,"pre_close":9.9}}
        self.event={"record_date":"2024-06-20","ex_date":"2024-06-21","cash_per_share_yuan":.2}
        self.trade={"signal_date":"2024-06-19","entry_date":"2024-06-20",
                    "exit_date":"2024-06-21","entry_open":10.,"exit_open":9.8,"signal":"B_breakdown"}

    def test_event_price_reconciliation(self):
        self.assertEqual(len(validate_events(self.prices,[self.event])),1)
        with self.assertRaises(ValueError):
            validate_events(self.prices,[{**self.event,"cash_per_share_yuan":.3}])

    def test_duplicate_event_rejected(self):
        with self.assertRaises(ValueError):
            validate_events(self.prices,[self.event,self.event])

    def test_zero_dividend_matches_v10(self):
        r=simulate([self.trade],self.prices,[],initial=4000.,minimum_fee=5.)
        b=baseline_simulate([self.trade],self.prices,initial=4000.,minimum_fee=5.)
        self.assertEqual(r["ending_equity_yuan"],b["ending_equity_yuan"])
        self.assertEqual(r["max_drawdown_pct"],b["max_drawdown_pct"])

    def test_record_date_entitlement_survives_ex_date_sale(self):
        r=simulate([self.trade],self.prices,[self.event],initial=4000.,minimum_fee=0.)
        self.assertEqual(r["dividend_ledger"][0]["shares"],300)
        self.assertEqual(r["dividend_gross_yuan"],60.)
        self.assertEqual(r["trades_executed"],1)

    def test_ex_date_entry_is_not_entitled(self):
        trade={**self.trade,"signal_date":"2024-06-20","entry_date":"2024-06-21",
               "exit_date":"2024-06-24","entry_open":9.8,"exit_open":10.}
        r=simulate([trade],self.prices,[self.event],initial=4000.,minimum_fee=0.)
        self.assertEqual(r["dividend_gross_yuan"],0.)

    def test_tax_and_fill_flags(self):
        gross=simulate([self.trade],self.prices,[self.event],initial=4000.,minimum_fee=0.)
        taxed=simulate([self.trade],self.prices,[self.event],initial=4000.,minimum_fee=0.,tax_rate=.2)
        self.assertLess(taxed["ending_equity_yuan"],gross["ending_equity_yuan"])
        self.assertEqual(gross["fill_count"],2)
        self.assertEqual(gross["fill_possible_unfillable_limit"],0)
        self.assertEqual(gross["fill_missing_pre_close"],0)

if __name__=="__main__":unittest.main()
