"""Strict PIT risk guard test cases; no external data or brokerage required."""
from collections import defaultdict
import unittest
import pandas as pd
from gold_pit_risk_guards import GoldPITRisk

D=pd.Timestamp("2024-01-03")
def example():
    ctx=GoldPITRisk.__new__(GoldPITRisk)
    ctx.limits={("000001.SZ",D):(11.00,9.00)}
    ctx.suspended=set()
    ctx.names=defaultdict(list,{"000001.SZ":[
        (pd.Timestamp("2020-01-01"),None,pd.Timestamp("2020-01-01"),False)]})
    return ctx

class RiskGuardsTests(unittest.TestCase):
    def test_unknown_name_is_never_safe(self):
        ctx=example()
        self.assertEqual(ctx.name_status("000002.SZ",D),"NAME_UNKNOWN")
        self.assertEqual(ctx.entry_reason("000002.SZ",D,{"open":10}),"LIMIT_UNKNOWN")
    def test_future_announced_name_is_not_used(self):
        ctx=example()
        ctx.names["000001.SZ"]=[(pd.Timestamp("2024-01-01"),None,pd.Timestamp("2024-01-04"),False)]
        self.assertEqual(ctx.name_status("000001.SZ",D),"NAME_UNKNOWN")
    def test_st_interval_blocks(self):
        ctx=example()
        ctx.names["000001.SZ"]=[(pd.Timestamp("2024-01-01"),None,pd.Timestamp("2024-01-02"),True)]
        self.assertEqual(ctx.entry_reason("000001.SZ",D,{"open":10}),"ST")
    def test_limit_up_and_suspension(self):
        ctx=example()
        self.assertEqual(ctx.entry_reason("000001.SZ",D,{"open":11}),"AT_LIMIT_UP_CANNOT_ASSUME_FILL")
        ctx.suspended.add(("000001.SZ",D))
        self.assertEqual(ctx.entry_reason("000001.SZ",D,{"open":10}),"SUSPENDED")
    def test_locked_down_defers_exit_not_assumed_liquid(self):
        ctx=example()
        self.assertEqual(ctx.exit_reason("000001.SZ",D,
             {"open":9,"high":9,"low":9}),"EXIT_LOCKED_LIMIT_DOWN")
        self.assertIsNone(ctx.exit_reason("000001.SZ",D,
             {"open":10,"high":10.2,"low":9.1}))
        self.assertEqual(ctx.exit_reason("000001.SZ",pd.Timestamp("2024-01-04"),
             {"open":10,"high":10.2,"low":9.1}),"EXIT_LIMIT_UNKNOWN")
    def test_duplicate_latest_name_intervals_fail_closed(self):
        ctx=example()
        n=ctx.names["000001.SZ"][0]
        ctx.names["000001.SZ"].append((n[0],n[1],n[2],True))
        self.assertEqual(ctx.name_status("000001.SZ",D),"NAME_UNKNOWN")

if __name__=="__main__":unittest.main()
