"""Focused execution audit regression tests. Deterministic synthetic prices only."""
import datetime as dt
import importlib.util
import math
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('btc_v3_audit_engine', ROOT/'backtest'/'run.py')
e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e)


def bars(prices):
    start = dt.date(2023, 1, 1)  # Sunday, day 1 = Monday
    out = []
    for i, (op, cl) in enumerate(prices):
        out.append({'date':start+dt.timedelta(days=i),
                    'open':float(op), 'close':float(cl),
                    'high':max(op, cl)*1.01, 'low':min(op, cl)*.99,
                    'volume':10.0})
    return out


class FrozenV3Audit(TestCase):
    def test_zero_volatility_or_invalid_sigma_goes_cash(self):
        x = bars([(100, 100)]*260)
        self.assertEqual(e.weekly_weight(x, 200), 0.0)
        self.assertEqual(e.weekly_weight(x, 200, no_trend=True), 0.0)

    def test_volatility_uses_sample_std_of_daily_log_returns(self):
        x = bars([(100*math.exp(i*.015*math.sin(i/3)),)*2 for i in range(240)])
        i = 220
        j = i-1
        ret = [math.log(x[k]['close']/x[k-1]['close']) for k in range(j-59,j+1)]
        sigma = __import__('statistics').stdev(ret)*math.sqrt(365)
        expected = min(.5, .10/sigma)
        self.assertAlmostEqual(e.weekly_weight(x, i, no_trend=True), expected, places=10)

    def test_zero_target_liquidates_small_residual_even_under_5pp(self):
        x = bars([(100,100)]*260)
        cutoff = next(i for i in range(210,250) if x[i]['date'].weekday()==0)
        x[cutoff-1]['close'] = 35.0  # causes residual BTC weight <5pp
        for i in range(cutoff, len(x)):
            x[i]['open'] = 35.0
            x[i]['close'] = 35.0
        with patch.object(e, 'target_signal', side_effect=lambda rows,i,s,old,f: .10 if i < cutoff else 0.0):
            equity, trades, _ = e.simulate(x,'v3')
        self.assertTrue(any(t['side']=='BUY' for t in trades))
        self.assertLess(equity[cutoff-1]['btc_weight'], .05)
        self.assertEqual(equity[cutoff]['btc_qty'], 0.0)
        self.assertEqual(trades[-1]['side'], 'SELL')

    def test_8pct_reduction_only_once_no_daily_churn(self):
        prices = [(100,100)]*220+[(100,75)]+[(75,75)]*39
        x = bars(prices)
        with patch.object(e, 'target_signal', return_value=.50):
            eq, trades, _ = e.simulate(x,'v3',protected=True)
        self.assertTrue(eq[221]['risk_reduced'])
        self.assertLessEqual(eq[221]['btc_weight'], .251)
        self.assertEqual(len([t for t in trades if t['risk_transition']=='REDUCED']),1)
        # With stable 75 price there should be no daily churn in reduced mode.
        self.assertFalse([t for t in trades if t['date']>eq[221]['date']])
        self.assertTrue(eq[-1]['risk_reduced'])

    def test_recover_only_on_monday_below_4pct_not_on_8pct_boundary(self):
        prices = [(100,100)]*220+[(100,75)]+[(75,75)]*12+[(75,115)]+[(115,115)]*26
        x=bars(prices)
        with patch.object(e,'target_signal',return_value=.50):
            eq, trades, _ = e.simulate(x,'v3',protected=True)
        self.assertTrue(eq[221]['risk_reduced'])
        eligible = [i for i in range(234,len(eq)) if x[i]['date'].weekday()==0 and eq[i-1]['drawdown']<=.04]
        self.assertTrue(eligible)
        self.assertFalse(eq[eligible[0]]['risk_reduced'])
        self.assertFalse(eq[-1]['stopped'])

    def test_hard_stop_is_permanent_and_liquidates(self):
        x = bars([(100,100)]*220+[(100,50)]+[(50,50)]*39)
        with patch.object(e,'target_signal',return_value=.50):
            eq, trades, _ = e.simulate(x,'v3',protected=True)
        stop_indices=[i for i,r in enumerate(eq) if r['stopped']]
        self.assertTrue(stop_indices)
        first=stop_indices[0]
        self.assertEqual(eq[first]['btc_qty'],0.0)
        self.assertTrue(all(r['stopped'] and r['btc_qty']==0.0 for r in eq[first:]))


if __name__=='__main__':
    main()
