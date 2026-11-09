"""V7 PAPER: fixed budget and market breadth regime, no V6 winner mining.

Predeclared independent levers within original user limits:
- high breadth (55% of valid mainboard symbols over MA20), known signal close
- RMB 35,000 order budget (still >=30,000 and <=50,000) vs 40,000.
Reuses unchanged V1 simulation and V2 LOOSE/V5 low-runup features and rank.
All old years are studied; NOT untouched OOS; nothing is broker executable.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict, dataclass, replace
import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
from short_swing_3to5_v1 import Policy, build_signals, load_gold, simulate
from short_swing_3to5_v2_research import SPECS, signal_day_features, filter_signals
from short_swing_3to5_v5_causal_ranking import rank_signals, yearly_metrics

VERSION='V7_BREADTH_BUDGET_FULL_CASH_PAPER_NO_OOS'
LOOSE=next(s for s in SPECS if s.name=='LOOSE')
SLIP_LEVELS=(.001,.003,.005)
@dataclass(frozen=True)
class Candidate:
    name:str
    breadth:float
    budget:float

CANDIDATES=(
    Candidate('V5_BASE_45_BREADTH_40K',.45,40000.),
    Candidate('V7_55_BREADTH_40K',.55,40000.),
    Candidate('V7_45_BREADTH_35K',.45,35000.),
    Candidate('V7_55_BREADTH_35K',.55,35000.),
)

def make_policy(c:Candidate,slip:float):
    if c not in CANDIDATES or slip not in SLIP_LEVELS:
        raise ValueError('Only frozen V7 conditions allowed')
    policy=replace(Policy(),order_budget_cny=c.budget,
                   min_breadth_fraction=c.breadth,slippage_rate=slip)
    policy.validate()
    if not (30000 <= policy.order_budget_cny <=50000):
        raise AssertionError('Outside funded trade limits')
    return policy

def research(panel,risk,start):
    features=signal_day_features(panel)
    by_breadth={}
    for broad in sorted({c.breadth for c in CANDIDATES}):
        p=replace(Policy(),min_breadth_fraction=broad)
        signals=build_signals(panel,p,risk)
        eligible,stats=filter_signals(signals,features,LOOSE)
        ranked=rank_signals(eligible,features,'LESS_RECENT_RUNUP_FIRST')
        by_breadth[broad]=(ranked,stats)
    entries=[]
    for slip in SLIP_LEVELS:
        variants=[]
        for c in CANDIDATES:
            policy=make_policy(c,slip)
            ranked,stats=by_breadth[c.breadth]
            sim=simulate(panel,risk,policy,signals=ranked,start=start)
            if sim['mode']!='PAPER_ONLY_NO_BROKER_NO_LIVE_AUTHORIZATION':
                raise RuntimeError('Execution backend compromised')
            if sim['summary']['closed_trades']<1:
                raise RuntimeError('Unrepresentative zero-trade experiment')
            orders=[x for x in sim['orders'] if x['side']=='BUY_ALL']
            if any(not(30000<=x['notional_cny']<=50000) for x in orders):
                raise RuntimeError('Order notional outside user restriction')
            variants.append({'name':c.name,'policy':asdict(policy),
                             'summary':sim['summary'],'by_year':yearly_metrics(sim),
                             'candidate_statistics':stats,
                             'buy_orders':len(orders),
                             'assumed_intraday_exits':sum(
                                t['exit_reason'] in ('STOP_INTRADAY_ASSUMED','TARGET_INTRADAY_ASSUMED')
                                for t in sim['trades']),
                             'open_position':sim['open_position'] is not None,
                             'orders':sim['orders'],'trades':sim['trades'],'daily_nav':sim['daily_nav']})
        entries.append({'one_way_slippage':slip,'variants':variants})
    base=entries[1]['variants'][0]['summary']
    if abs(base['total_return']-.020672)>1e-6 or abs(base['max_drawdown']-(-.27479458))>1e-6:
        raise RuntimeError('Pinned V5 0.3 pct baseline divergence')
    return {'version':VERSION,'mode':'RESEARCH_ONLY_NOT_OOS_NOT_LIVE',
            'fixed_specs':[asdict(c) for c in CANDIDATES],
            'slippages':SLIP_LEVELS,'results':entries,'baseline_reproduced':True,
            'limitations':[
             '2024-2026 history is repeatedly studied, no clean OOS claim.',
             'Daily OHLC intraday target/stop assumes execution; minute touch alone is not broker fill.',
             'High breadth regimes reduce signals but never change frozen stop or target.',
             '35k budget remains in authorised 30-50k trading notional range.',
             'No a posteriori selection of winning trades and no auto-selection for live usage.',
             'Historical 0.3%-per-side positive return and maxDD<=25% are necessary, not sufficient.',
             'No scheduler, broker, automatic PR merge, or real orders.']}

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
        p.error('Invalid Gold backtest window')
    panel,manifest,h=load_gold(a.market_root,a.start,a.end)
    if h!=a.gold_manifest_sha256:raise RuntimeError('Gold changed since accepted snapshot')
    from gold_pit_risk_guards import GoldPITRisk
    risk=GoldPITRisk(a.risk_root)
    if not risk.limits or not risk.names:raise RuntimeError('Missing PIT limits/name records')
    r=research(panel,risk,a.start)
    r['source']={'period':{'start':a.start,'end':a.end},'gold_manifest_sha256':h,
                 'as_of':manifest['as_of_date'],'pit':risk.summary()}
    if a.output.exists():raise FileExistsError('Do not overwrite old evidence')
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(r,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf8')
    for entry in r['results']:
        print('V7_FULL_CASH_PAPER',entry['one_way_slippage'],
              [(z['name'],z['summary']['total_return'],z['summary']['max_drawdown'],
                z['summary']['closed_trades']) for z in entry['variants']])

if __name__=='__main__':main()
