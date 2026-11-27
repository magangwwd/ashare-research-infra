"""Offline regression tests for 600031 next-open execution sensitivity."""
from pathlib import Path
import importlib.util
import pandas as pd

SCRIPT = Path(__file__).resolve().parents[1] / 'skills/stock-personality-analysis/execution_backtest.py'
spec = importlib.util.spec_from_file_location('personality_execution', SCRIPT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

def panel():
    dates=pd.bdate_range('2025-01-01',periods=70)
    df=pd.DataFrame({'ts_code':'600031.SH','trade_date':dates,
      'open':10.,'high':11.,'low':9.,'close':10.,'vol':100.,'pre_close':10.})
    df.loc[30,['close','high','vol']]=[10.9,11.5,250.]
    df.loc[31,'open']=10.8
    df.loc[36,'open']=11.0
    df['pre_close']=df.close.shift(1).fillna(df.close.iloc[0])
    return df

def test_next_open_execution():
    g=panel()
    t=m.trades(g,'D_breakout',5)
    assert len(t)==1
    assert t[0]['entry_date']==str(g.loc[31,'trade_date'].date())
    assert t[0]['exit_date']==str(g.loc[36,'trade_date'].date())
    assert t[0]['shares']%100==0

def test_nonoverlap():
    g=panel()
    g.loc[31:34,'close']=11.0
    g.loc[31:34,'high']=11.5
    g.loc[31:34,'vol']=300.
    assert len(m.trades(g,'D_breakout',5))==1

def test_corporate_action_sensitivity():
    g=panel()
    g.loc[35,'pre_close']=9.8
    a=m.adjust_open(g)
    assert abs(a.loc[35,'adjusted_open']/a.loc[35,'open']-10/9.8)<1e-9

def test_cost_drag():
    t=m.trades(panel(),'D_breakout',5)[0]
    assert t['net_return_pct']<t['gross_return_pct']

def test_exclude_cross_year():
    g=panel()
    g['trade_date']=pd.bdate_range('2025-11-17',periods=70)
    assert len(m.trades(g,'D_breakout',5))==0
