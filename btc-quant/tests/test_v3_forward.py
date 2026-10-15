"""Offline regression checks for the frozen V3 forward-only daily shadow ledger."""
import datetime as dt
import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("btc_forward_unit", ROOT/"forward"/"paper.py")
p=importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)

class Freeze:
    @staticmethod
    def weekly_weight(rows,i):
        return .50

CFG={"strategy_id":"BTC_V3_DAILY_CANDIDATE_FROZEN_20261015",
     "frozen_git_commit":"d60c667b3654dbfb00efea9bc72241cca3bb8d5c"}

def rows(end=dt.date(2026,10,25),price=100.0):
    out=[]
    d=dt.date(2020,1,1)
    while d<=end:
        out.append({"date":d,"open":price,"high":price*1.01,
                    "low":price*.99,"close":price,"volume":1.0})
        d+=dt.timedelta(days=1)
    return out

class ForwardCandidateTests(unittest.TestCase):
    def test_genesis_cash_starts_only_oct14(self):
        d=rows()
        s=p.genesis(CFG,d)
        self.assertEqual(s["state_asof_utc"],"2026-10-14")
        self.assertEqual(s["cash_usdt"],1000)
        self.assertEqual(s["btc_qty"],0)

    def test_daily_forward_no_weekday_buy_until_monday(self):
        d=rows()
        s=p.genesis(CFG,d)
        for when in (dt.date(2026,10,15),dt.date(2026,10,16),
                     dt.date(2026,10,17),dt.date(2026,10,18)):
            s=p.step(s,d,Freeze,CFG)
            self.assertEqual(s["btc_qty"],0)
            self.assertEqual(s["state_asof_utc"],str(when))
        s=p.step(s,d,Freeze,CFG)
        self.assertEqual(s["state_asof_utc"],"2026-10-19")
        self.assertGreater(s["btc_qty"],0)
        self.assertEqual(s["last_signal_inputs_through_utc"],"2026-10-18")

    def test_first_8pct_cut_reduces_only_once_and_retains_hwm(self):
        d=rows()
        s=p.genesis(CFG,d)
        s.update({"cash_usdt":500.0,"btc_qty":5.0,"nav_usdt":1000,
                  "hwm_usdt":1100.0,"drawdown":.09,
                  "weekly_target_weight":.5})
        s=p.step(s,d,Freeze,CFG)
        self.assertEqual(s["risk_state"],"REDUCED")
        self.assertEqual(s["event"]["side"],"SELL")
        self.assertEqual(s["event"]["reason"],"FIRST_8PCT_RISK_CUT")
        self.assertLessEqual(s["btc_qty"]*100/s["nav_usdt"],.251)
        self.assertEqual(s["hwm_usdt"],1100)
        s2=p.step(s,d,Freeze,CFG)
        self.assertEqual(s2["risk_state"],"REDUCED")
        self.assertIsNone(s2["event"])

    def test_halted_forever_even_after_rebound(self):
        d=rows()
        s=p.genesis(CFG,d)
        s.update({"btc_qty":2.0,"cash_usdt":800.0,"hwm_usdt":1200.0,
                  "drawdown":.16,"weekly_target_weight":.50})
        s=p.step(s,d,Freeze,CFG)
        self.assertEqual(s["risk_state"],"HALTED")
        self.assertEqual(s["btc_qty"],0)
        self.assertEqual(s["event"]["reason"],"PERMANENT_HALT")
        s["drawdown"]=0.0  # even synthetic recovery cannot resume
        s2=p.step(s,d,Freeze,CFG)
        self.assertEqual(s2["risk_state"],"HALTED")
        self.assertEqual(s2["btc_qty"],0)

    def test_historical_price_drift_fails_closed(self):
        d=rows()
        s=p.genesis(CFG,d)
        d[100]["close"]+=1
        with self.assertRaisesRegex(ValueError,"PREFIX CHANGED"):
            p.step(s,d,Freeze,CFG)

    def test_recovery_only_monday_and_prev_dd_four_percent(self):
        d=rows()
        s=p.genesis(CFG,d)
        s.update({"risk_state":"REDUCED","drawdown":.02,"weekly_target_weight":.5,
                  "hwm_usdt":1000})
        s2=p.step(s,d,Freeze,CFG) # 10/15 Thu
        self.assertEqual(s2["risk_state"],"REDUCED")
        for day in (16,17,18,19):
            s2=p.step(s2,d,Freeze,CFG)
        self.assertEqual(s2["state_asof_utc"],"2026-10-19")
        self.assertEqual(s2["risk_state"],"NORMAL")

if __name__=="__main__":
    unittest.main()
