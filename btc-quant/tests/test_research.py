"""Deterministic synthetic tests; NOT historical BTC performance evidence."""
import csv
import datetime as dt
import importlib.util
import math
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]

def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name,path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

v = load_module('validate', ROOT/'data'/'validate.py')
e = load_module('bt_engine', ROOT/'backtest'/'run.py')


def sample_daily(n=260):
    start = dt.date(2023,1,1)
    result = []
    for i in range(n):
        p = 10000 * math.exp(i*.001)
        result.append({'date':start+dt.timedelta(days=i),'open':p,
                       'high':p*1.02,'low':p*.98,'close':p*1.005,'volume':10})
    return result


class HistoricalDataValidation(unittest.TestCase):
    def test_timestamp_ms_and_us(self):
        instant = dt.datetime(2025,2,1,tzinfo=dt.timezone.utc)
        self.assertEqual(v.timestamp(int(instant.timestamp()*1000)),instant)
        self.assertEqual(v.timestamp(int(instant.timestamp()*1_000_000)),instant)

    def test_read_and_aggregate(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp)/'test.zip'
            start = dt.datetime(2023,1,1,tzinfo=dt.timezone.utc)
            rows = []
            for i in range(6*202):
                instant = start+dt.timedelta(hours=4*i)
                rows.append([str(int(instant.timestamp()*1000)), '100','101','99','100','10', '0','0','0','0','0','0'])
            with zipfile.ZipFile(file,'w') as z:
                z.writestr('candles.csv','\n'.join(','.join(r) for r in rows))
            candles, digest = v.read_4h([file])
            daily, dropped = v.aggregate_daily(candles)
            self.assertEqual(len(daily),202)
            self.assertEqual(dropped,[])
            r = v.save_daily(daily,tmp,digest,len(candles),dropped)
            self.assertEqual(r['status'],'PASS')
            self.assertEqual(len(e.load_daily(Path(tmp)/'btc_daily_utc.csv')),202)
            broken = rows[:99]+rows[100:]
            with zipfile.ZipFile(file,'w') as z:
                z.writestr('candles.csv','\n'.join(','.join(r) for r in broken))
            with self.assertRaisesRegex(ValueError,'missing 4h'):
                v.read_4h([file])


class StrategyExecution(unittest.TestCase):
    def test_no_lookahead_next_open(self):
        x = sample_daily()
        orig = e.simulate(x,'v3')
        x2 = [r.copy() for r in x]
        x2[200]['close'] *= 1.2
        x2[200]['high'] *= 1.2
        changed = e.simulate(x2,'v3')
        self.assertEqual([r for r in orig[1] if r['date'] <= str(x[200]['date'])],
                         [r for r in changed[1] if r['date'] <= str(x[200]['date'])])

    def test_cash_benchmark_and_bounds(self):
        x = sample_daily()
        nav, trades, cost = e.simulate(x,'cash')
        self.assertEqual(cost,0)
        self.assertFalse(trades)
        self.assertTrue(all(row['nav']==1000 for row in nav))
        for name in ('v1_kalman_rebuilt','v2_breakout','v2_ema_buffer','v3','v3_notrend','half_monthly'):
            nav, trades, cost = e.simulate(x,name,protected=True)
            self.assertTrue(all(row['nav']>0 and row['cash_usdt']>=-1e-6 and 0<=row['btc_weight']<=1.0 for row in nav),name)
            self.assertTrue(cost>=0)

    def test_monthly_benchmark_does_not_rebalance_daily(self):
        x=sample_daily(260)
        _, trades, _ = e.simulate(x,'half_monthly')
        self.assertLessEqual(len(trades), 11)
        self.assertGreaterEqual(len(trades), 8)

    def test_guard_stops_on_drawdown_and_never_resumes(self):
        x = sample_daily(270)
        for i in range(235,270):
            for k in ('open','high','low','close'):
                x[i][k] *= .5
        eq, trades, cost = e.simulate(x,'v3_notrend',protected=True)
        stopped = [r for r in eq if r['stopped']]
        self.assertTrue(stopped)
        self.assertTrue(all(r['btc_qty']==0 for r in stopped[1:]))

    def test_artifacts_are_actual_test_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp)/'daily.csv'
            rows = sample_daily()
            with f.open('w',newline='') as sink:
                writer=csv.DictWriter(sink,fieldnames=['date','open','high','low','close','volume'])
                writer.writeheader()
                for row in rows:
                    row=row.copy();row['date']=row['date'].isoformat();writer.writerow(row)
            result = e.run(f,Path(tmp)/'out')
            self.assertEqual(len(result['strategies']),18)
            self.assertTrue((Path(tmp)/'out'/'summary.json').is_file())
            self.assertTrue((Path(tmp)/'out'/'v3_guarded_1x_cost_equity.csv').is_file())


if __name__ == '__main__':
    unittest.main()
