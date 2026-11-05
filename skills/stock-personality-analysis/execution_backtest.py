#!/usr/bin/env python3
"""Retrospective next-open execution sensitivity, not an investable backtest.
Signal at session t close; buy at t+1 open; sell at t+1+h open.
Single position per pattern; pre_close ratio is only synthetic ex-rights sensitivity.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import numpy as np
import pandas as pd

spec = importlib.util.spec_from_file_location('personality', Path(__file__).with_name('stock_personality.py'))
pers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pers)

def adjust_open(g):
    g = g.sort_values('trade_date').copy().reset_index(drop=True)
    if 'pre_close' not in g:
        raise ValueError('pre_close required for corporate-action discontinuity checks')
    pc = pd.to_numeric(g.pre_close, errors='coerce')
    prev = g.close.shift(1)
    ratio = (prev / pc).where(pc.gt(0) & prev.gt(0), 1.0).fillna(1.0)
    g['adjusted_open'] = g.open * ratio.cumprod()
    return g

def trades(g, signal, h, cash=40000, commission=.0003, stamp=.0005, slip=.0005):
    if signal not in pers.PATTERNS or not 1 <= h <= 10 or cash <= 0:
        raise ValueError('invalid signal, holding window or cash')
    x = pers.features(adjust_open(g), h)
    last_exit = -1
    out = []
    for t in range(20, len(x)-h-1):
        if not bool(x.loc[t,signal]) or t+1 <= last_exit:
            continue
        entry=t+1
        exit_=entry+h
        if x.loc[t,'trade_date'].year != x.loc[exit_,'trade_date'].year:
            continue
        op=float(x.loc[entry,'open'])
        ex=float(x.loc[exit_,'open'])
        ao=float(x.loc[entry,'adjusted_open'])
        ae=float(x.loc[exit_,'adjusted_open'])
        if min(op,ex,ao,ae) <= 0:
            continue
        buy=op*(1+slip)
        shares=int((cash/(buy*(1+commission)))//100)*100
        if shares < 100:
            continue
        spent=shares*buy*(1+commission)
        sold=shares*ex*(1-slip)*(1-commission-stamp)
        adj_sold=shares*(ae/ao)*buy*(1-2*slip)*(1-commission-stamp)
        out.append({
          'signal_date':str(x.loc[t,'trade_date'].date()),
          'entry_date':str(x.loc[entry,'trade_date'].date()),
          'exit_date':str(x.loc[exit_,'trade_date'].date()),
          'year':int(x.loc[t,'trade_date'].year),
          'shares':shares,
          'gross_return_pct':round(100*(ex/op-1),4),
          'net_return_pct':round(100*(sold/spent-1),4),
          'net_pnl_yuan':round(sold-spent,2),
          'synthetic_adj_return_pct':round(100*(adj_sold/spent-1),4),
          'ex_factor_during_holding':round(float((ae/ex)/(ao/op)),8)})
        last_exit=exit_
    return out

def summary(ts):
    if not ts:
        return {'n':0}
    a=np.array([t['net_return_pct'] for t in ts])
    b=np.array([t['synthetic_adj_return_pct'] for t in ts])
    return {'n':len(ts),'win_n':int((a>0).sum()),
            'win_rate_pct':round(float((a>0).mean()*100),2),
            'mean_net_return_pct':round(float(a.mean()),3),
            'median_net_return_pct':round(float(np.median(a)),3),
            'mean_synthetic_adj_return_pct':round(float(b.mean()),3),
            'sum_net_pnl_yuan':round(float(sum(t['net_pnl_yuan'] for t in ts)),2),
            'ex_action_trade_n':sum(abs(t['ex_factor_during_holding']-1)>1e-6 for t in ts)}

def analyze(panel, target='600031.SH', holds=(3,5), cash=40000):
    if target not in set(panel.ts_code):
        raise ValueError('target absent')
    result={'target':target,
      'assumptions':{'entry':'next session open','exit':'open h sessions after entry',
        'cash_yuan':cash,'single_position_per_pattern':True,'board_lot':100,
        'commission_each_side':.0003,'sell_stamp':.0005,'slippage_each_side':.0005,
        'approx_round_trip_cost_pct':.21,
        'limits':'No suspension/limit-up fill checks; synthetic ex-rights not cash dividends',
        'holdout':'Retrospective; 2026 already examined and is not untouched holdout'},
      'stocks':{}}
    for code,g in panel.groupby('ts_code'):
        stock={}
        for h in holds:
            patterns={}
            for signal in pers.PATTERNS:
                t=trades(g,signal,h,cash)
                d={str(year):summary([v for v in t if v['year']==year])
                   for year in sorted(set(panel.trade_date.dt.year))}
                if code==target:
                    d['target_trades']=t
                patterns[signal]=d
            stock[str(h)]=patterns
        result['stocks'][code]=stock
    return result

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--input',required=True)
    p.add_argument('--out',required=True)
    p.add_argument('--target',default='600031.SH')
    p.add_argument('--cash',type=float,default=40000)
    a=p.parse_args()
    result=analyze(pers.load_panel(a.input),a.target,cash=a.cash)
    Path(a.out).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Wrote',a.out)

if __name__=='__main__':
    main()
