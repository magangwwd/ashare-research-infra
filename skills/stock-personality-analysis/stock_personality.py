#!/usr/bin/env python3
"""Stock behavior fingerprints, time-split descriptive study (no investment advice).
Input: CSV/Parquet with ts_code,trade_date,open,high,low,close,vol.
Usage: python stock_personality.py --input peer_daily.csv --target 600031.SH --out stats.json
"""
import argparse
import json
from pathlib import Path
import numpy as np
import pandas as pd

REQUIRED = ['ts_code','trade_date','open','high','low','close','vol']
PATTERNS = {
 'A_volume_down': '放量阴线：收盘低于开盘，量大于此前20日均量的1.5倍',
 'B_breakdown': '放量跌破20日收盘低点：收盘低于此前20日最低收盘，量大于此前20日均量',
 'C_lowvol_rebound': '跌后缩量阳线：过去5日跌超3%，今日阳线且量低于此前20日均量0.8倍',
 'D_breakout': '放量突破20日收盘高点：收盘高于此前20日最高收盘，量大于此前20日均量1.3倍',
 'E_sharpfall_rebound': '五日急跌后放量反弹：过去5日跌超5%，今日阳线且量高于此前20日均量',
 'F_tight_lowvol': '十日窄幅缩量：此前10日最高价/最低价波幅小于5%，此前5日均量低于此前20日均量',
 'G_upper_wick': '放量长上影：上影线占当日振幅超45%，量高于此前20日均量1.2倍',
 'H_lowvol_up': '站稳20日线缩量上涨：收盘高于此前20日收盘均值，今日上涨且量低于此前20日均量0.8倍',
 'I_lowvol_after_rise': '三日上涨后缩量阴线：过去3日涨超3%，今日阴线且量低于此前20日均量0.7倍',
}

def load_panel(path):
 p=Path(path)
 df=pd.read_parquet(p) if p.suffix.lower()=='.parquet' else pd.read_csv(p)
 missing=set(REQUIRED)-set(df)
 if missing:raise ValueError(f'missing columns: {sorted(missing)}')
 df=df.copy();df['trade_date']=pd.to_datetime(df.trade_date,errors='raise')
 for c in ['open','high','low','close','vol']:
  df[c]=pd.to_numeric(df[c],errors='raise')
 if df[REQUIRED].isna().any().any():raise ValueError('missing critical fields')
 if df.duplicated(['ts_code','trade_date']).any():raise ValueError('duplicate stock/date')
 if not (df.close.gt(0)&df.vol.ge(0)&(df.high>=df[['open','low','close']].max(axis=1))&(df.low<=df[['open','high','close']].min(axis=1))).all():
  raise ValueError('invalid OHLC/volume')
 return df.sort_values(['ts_code','trade_date']).reset_index(drop=True)

def features(df,h=5):
 x=df.sort_values('trade_date').copy().reset_index(drop=True)
 close,vol=x.close,x.vol
 v20=vol.shift(1).rolling(20).mean()
 r3=close/close.shift(3)-1;r5=close/close.shift(5)-1
 ma20=close.shift(1).rolling(20).mean()
 prev20low=close.shift(1).rolling(20).min();prev20high=close.shift(1).rolling(20).max()
 prior_range10=x.high.shift(1).rolling(10).max()/x.low.shift(1).rolling(10).min()-1
 upper_wick=(x.high-x[['open','close']].max(axis=1))/(x.high-x.low).replace(0,np.nan)
 cond={
 'A_volume_down':(close<x.open)&(vol>1.5*v20),
 'B_breakdown':(close<prev20low)&(vol>v20),
 'C_lowvol_rebound':(r5<-.03)&(close>x.open)&(vol<.8*v20),
 'D_breakout':(close>prev20high)&(vol>1.3*v20),
 'E_sharpfall_rebound':(r5<-.05)&(close>x.open)&(vol>v20),
 'F_tight_lowvol':(prior_range10<.05)&(vol.shift(1).rolling(5).mean()<v20),
 'G_upper_wick':(upper_wick>.45)&(vol>1.2*v20),
 'H_lowvol_up':(close>ma20)&(close>close.shift(1))&(vol<.8*v20),
 'I_lowvol_after_rise':(r3>.03)&(close<x.open)&(vol<.7*v20),
 }
 x['fwd_ret']=close.shift(-h)/close-1
 x['fwd_date']=x.trade_date.shift(-h)
 x['ready']=v20.notna()&x.fwd_ret.notna()
 for name,c in cond.items():x[name]=c.fillna(False)
 return x

def episode_indices(index,gap=5):
 out=[];last=-1000000
 for i in index:
  if i-last>=gap:out.append(int(i));last=i
 return out

def summarise(x,mask):
 y=x.loc[mask&x.ready,'fwd_ret'].dropna();n=len(y)
 if not n:return {'n':0,'episodes':0}
 episodes=episode_indices(y.index)
 return {'n':n,'episodes':len(episodes), 'up_ge_3pct':int((y>=.03).sum()),
  'down_le_minus_3pct':int((y<=-.03).sum()),'flat_within_2pct':int((y.abs()<=.02).sum()),
  'mean_return_pct':round(float(y.mean()*100),3),
  'median_return_pct':round(float(y.median()*100),3),
  'up_rate_pct':round(float((y>=.03).mean()*100),2),
  'down_rate_pct':round(float((y<=-.03).mean()*100),2),
  'episode_down_rate_pct':round(float((x.loc[episodes,'fwd_ret']<=-.03).mean()*100),2)}

def analyze(panel,h=5):
 result={'horizon_days':h,'target_thresholds':{'up':'>=3%','down':'<=-3%','flat':'absolute return <=2%'},'patterns':PATTERNS,'stocks':{}}
 for code,grp in panel.groupby('ts_code'):
  x=features(grp,h)
  item={'days':len(x),'start':str(x.trade_date.min().date()),'end':str(x.trade_date.max().date()),'periods':{}}
  for year in sorted(x.trade_date.dt.year.unique()):
   mask=(x.trade_date.dt.year==year)&(x.fwd_date.dt.year==year)
   period={'baseline':summarise(x,mask),'signals':{}}
   for name in PATTERNS:period['signals'][name]=summarise(x,mask & x[name])
   item['periods'][str(year)]=period
  result['stocks'][code]=item
 return result

def main():
 a=argparse.ArgumentParser();a.add_argument('--input',required=True);a.add_argument('--target',default='600031.SH');a.add_argument('--out',required=True);a.add_argument('--horizon',type=int,default=5)
 args=a.parse_args();panel=load_panel(args.input)
 if args.target not in set(panel.ts_code):raise ValueError('target absent')
 if not 1<=args.horizon<=10:raise ValueError('horizon must be 1..10')
 r=analyze(panel,args.horizon);r['target']=args.target;r['input']=str(args.input)
 Path(args.out).write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8')
 print(f"Analyzed {args.target}: {r['stocks'][args.target]['days']} days, output={args.out}")
if __name__=='__main__':main()
