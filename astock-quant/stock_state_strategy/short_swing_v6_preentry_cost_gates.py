"""V6 PAPER: pre-entry predeclared no-chase gates on V5 lower-runup first.

No broker, no Baostock, no new data vendor. Uses unchanged V1 full cash
simulator, unchanged V2 LOOSE candidate filter, unchanged V5 low-runup rank.
Only trades available as of completed signal-day close; no post-hoc removal.
2024-26 data already studied: not an untouched OOS experiment.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict, replace
import hashlib
import json
import math
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
from short_swing_3to5_v1 import Policy, build_signals, simulate, load_gold
from short_swing_3to5_v2_research import SPECS, filter_signals, signal_day_features
from short_swing_3to5_v5_causal_ranking import rank_signals, yearly_metrics

VERSION = 'MAINBOARD_V6_PREENTRY_CAP_FULL_CASH_PAPER'
BASE = 'LESS_RECENT_RUNUP_FIRST'
VARIANTS = ('V5_LOW_RUNUP_BASELINE','V6_PRIOR5_MAX_08','V6_BREAKOUT_MAX_025')
SLIPPAGES = (0.001,0.003,0.005)
LOOSE = next(s for s in SPECS if s.name == 'LOOSE')

def causal_gate(ranked, features, name):
    if name not in VARIANTS: raise ValueError('Unknown frozen V6 name')
    output={}
    counts={'before':0,'after':0,'removed':0}
    for day,pairs in ranked.items():
        chosen=[]
        for symbol,score in pairs:
            counts['before']+=1
            idx=(pd.Timestamp(day),symbol)
            if idx not in features.index:
                raise ValueError('Missing signal-time features')
            row=features.loc[idx]
            if isinstance(row,pd.DataFrame):
                raise ValueError('Duplicate time-symbol feature')
            a,b=float(row.prior5_gain),float(row.breakout)
            if not (math.isfinite(a) and math.isfinite(b)):
                raise ValueError('Nonfinite lagged feature')
            allow=(name=='V5_LOW_RUNUP_BASELINE' or
                   (name=='V6_PRIOR5_MAX_08' and a<=.08) or
                   (name=='V6_BREAKOUT_MAX_025' and b<=.025))
            if allow:chosen.append((symbol,score))
            else:counts['removed']+=1
        output[day]=chosen
        counts['after']+=len(chosen)
    if counts['before'] != counts['after']+counts['removed']:
        raise AssertionError('Cohort accounting mismatch')
    return output,counts

def evaluate(panel, risk, policy, start):
    original=build_signals(panel,policy,risk)
    features=signal_day_features(panel)
    survivors,legacy_counts=filter_signals(original,features,LOOSE)
    rank=rank_signals(survivors,features,BASE)
    runs=[]
    for name in VARIANTS:
        signals,counts=causal_gate(rank,features,name)
        sim=simulate(panel,risk,policy,signals=signals,start=start)
        if sim.get('mode')!='PAPER_ONLY_NO_BROKER_NO_LIVE_AUTHORIZATION':
            raise RuntimeError('Execution mode changed')
        if any(t['exit_date']<t['entry_date'] for t in sim['trades']):
            raise RuntimeError('Chronology invalid')
        if sim['summary']['closed_trades']<=0:
            raise RuntimeError('Empty experiment: cannot report return as success')
        runs.append({'name':name,'summary':sim['summary'],
                     'by_year':yearly_metrics(sim),
                     'candidate_counts':counts,
                     'assumed_intraday_exits':sum(t['exit_reason'] in
                        ('STOP_INTRADAY_ASSUMED','TARGET_INTRADAY_ASSUMED')
                        for t in sim['trades']),
                     'orders':sim['orders'],'trades':sim['trades'],
                     'daily_nav':sim['daily_nav'],
                     'open_position':sim['open_position'] is not None})
    for v in runs:
        if v['open_position']:raise RuntimeError('Unclosed hold, refuse apparent equity profits')
    return {'versions':[x['name'] for x in runs], 'runs':runs,
            'frozen_filter':asdict(LOOSE),'legacy_counts':legacy_counts}

def research(panel,risk,start):
    rows=[]
    for slip in SLIPPAGES:
        policy=replace(Policy(),slippage_rate=slip)
        policy.validate()
        result=evaluate(panel,risk,policy,start)
        rows.append({'each_side_slippage':slip,**result})
    # Equal baseline is a strict comparison against the V5 baseline source.
    reference=rows[0]['runs'][0]['summary']
    return {'version':VERSION,'mode':'RESEARCH_ONLY_NOT_LIVE_NOT_OOS',
            'policy':asdict(Policy()),
            'fixed_slippage':SLIPPAGES,'fixed_variants':VARIANTS,
            'slippage_results':rows,'benchmark_standard_slip':reference,
            'limitations':[
                'Full 2024-26 sample studied by prior variants; no independent untouched OOS claim.',
                'All fills use frozen daily OHLC assumptions rather than proven real executions.',
                'Entry-signal-day constraints must be evaluated again on a future PAPER sample.',
                'No cherry-picked realized exit filtering; entire one-stock capital path simulated.',
                '0.3 percent one-way adverse slip with maxDD<=25%, positive return, and verified fills are necessary not sufficient.',
                'No automatic strategy promotion, live trading, new schedule or PR merge.']}

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--market-root',type=Path,required=True)
    p.add_argument('--risk-root',type=Path,required=True)
    p.add_argument('--start',default='2024-01-01')
    p.add_argument('--end',required=True)
    p.add_argument('--gold-manifest-sha256',required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    now=datetime.now(ZoneInfo('Asia/Shanghai'))
    if pd.Timestamp(a.start)<pd.Timestamp('2024-01-01') or (
      pd.Timestamp(a.end)>pd.Timestamp(now.date()) or (
      pd.Timestamp(a.end)==pd.Timestamp(now.date()) and now.hour<16)):
        p.error('Invalid or incomplete historical backtest period')
    panel,manifest,source_sha=load_gold(a.market_root,a.start,a.end)
    if source_sha!=a.gold_manifest_sha256:
        raise RuntimeError('Frozen Tushare Gold SHA changed: fail closed')
    from gold_pit_risk_guards import GoldPITRisk
    risk=GoldPITRisk(a.risk_root)
    if not risk.limits or not risk.names:raise RuntimeError('Missing PIT risk data')
    report=research(panel,risk,a.start)
    report['source']={'gold_manifest_sha256':source_sha,
                      'as_of':manifest['as_of_date'],
                      'start':a.start,'end':a.end,'pit':risk.summary()}
    if a.output.exists():raise FileExistsError('Immutable result already exists')
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf8')
    for row in report['slippage_results']:
        print('V6_FULL_CASH_PATH_NO_OOS',row['each_side_slippage'],
              [(r['name'],r['summary']['total_return'],r['summary']['max_drawdown'],
                 r['summary']['closed_trades']) for r in row['runs']])

if __name__=='__main__':main()
