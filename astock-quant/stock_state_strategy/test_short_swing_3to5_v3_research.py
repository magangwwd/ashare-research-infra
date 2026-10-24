"""Synthetic-only V3 stateful gate and frozen V1 reproduction tests."""
import unittest
import pandas as pd
from short_swing_3to5_v1 import Policy, simulate
from short_swing_3to5_v3_research import EntryGate, GateSpec, GATES, simulate_with_gate

class NeutralPIT:
    def entry_reason(self,*args):return None
    def exit_reason(self,*args):return None

def artificial_market():
    dates=pd.bdate_range('2024-01-01',periods=80)
    rows=[{'vendor_symbol':'600001.SH','trade_date':d,'open':10+i*.001,
           'high':(10+i*.001)*1.02,'low':(10+i*.001)*.98,'close':10+i*.001,
           'adjustment_factor':1.,'volume_shares':1000000,
           'turnover_cny':200000000,'is_quality_valid':True} for i,d in enumerate(dates)]
    return pd.DataFrame(rows),{dates[61]:[('600001.SH',1.)],dates[68]:[('600001.SH',1.)]}

class RiskOverlay(unittest.TestCase):
    def test_two_loss_cooldown_no_future_information(self):
        gate=EntryGate(GATES[0])
        day=pd.Timestamp('2024-01-03')
        gate.on_exit(1,day,{'pnl_cny':-100})
        gate.on_exit(3,day,{'pnl_cny':-200})
        self.assertEqual(gate.pause_through_index,13)
        self.assertEqual(len(gate.events),1)
        self.assertFalse(gate.permit_entry(4,day,50000,50000))
        self.assertFalse(gate.permit_entry(13,day,50000,50000))
        self.assertTrue(gate.permit_entry(14,day,50000,50000))
        gate.on_exit(16,day,{'pnl_cny':200})
        self.assertEqual(gate.loss_streak,0)

    def test_permanent_halt_from_known_flat_cash(self):
        gate=EntryGate(GATES[1])
        day=pd.Timestamp('2024-01-05')
        self.assertTrue(gate.permit_entry(10,day,83000,100000))
        self.assertFalse(gate.permit_entry(11,day,81000,100000))
        self.assertFalse(gate.permit_entry(100,day,999999,100000))
        self.assertEqual(len(gate.events),1)
        self.assertEqual(gate.events[0]['kind'],'PERMANENT_NEW_ENTRY_HALT')

    def test_no_gate_identical_to_v1_and_halted_blocks_new_buy_only(self):
        panel,signals=artificial_market()
        baseline=simulate(panel,NeutralPIT(),Policy(),signals=signals,start='2024-01-01')
        mirrored=simulate_with_gate(panel,NeutralPIT(),Policy(),signals=signals,start='2024-01-01')
        self.assertEqual(baseline,mirrored)
        self.assertTrue(baseline['orders'])
        gate=EntryGate(GATES[1])
        gate.halted=True
        blocked=simulate_with_gate(panel,NeutralPIT(),Policy(),signals=signals,
                                  start='2024-01-01',gate=gate)
        self.assertEqual(blocked['orders'],[])
        self.assertEqual(blocked['summary']['ending_equity_cny'],50000)
        self.assertGreaterEqual(gate.denied_sessions,1)

    def test_reject_invalid_gate_specs(self):
        with self.assertRaises(ValueError):GateSpec('bad',dd_halt_fraction=0)
        with self.assertRaises(ValueError):GateSpec('bad',consecutive_losses=1,cooldown_sessions=10)
        with self.assertRaises(ValueError):GateSpec('bad',consecutive_losses=2,cooldown_sessions=0)

if __name__=='__main__':unittest.main()
