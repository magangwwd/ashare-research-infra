"""Frozen V7 close-of-day forward PAPER signal receipt (NO BROKER).

Eligibility: run on actual completed Shanghai-market date >= 2026-10-21
after 15:30 CST but before the next opening session, only against latest
validated Tushare Gold/PIT. Produce immutable candidate receipt, even NO SIGNAL.
Trade PnL CANNOT be claimed from this artifact; separate future fill audit.
No scheduler and no automated brokerage API.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict, replace
from datetime import date,datetime,time,timedelta
import hashlib
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
from short_swing_3to5_v1 import Policy,build_signals,load_gold
from short_swing_3to5_v2_research import SPECS,filter_signals,signal_day_features
from short_swing_3to5_v5_causal_ranking import rank_signals

FREEZE_DATE=date(2026,10,20)
POLICY=replace(Policy(),min_breadth_fraction=.55,order_budget_cny=40000.,
               slippage_rate=.003)
POLICY.validate()
LOOSE=next(s for s in SPECS if s.name=='LOOSE')
VERSION='V7_55_BREADTH_40K_FROZEN_FORWARD_PAPER_20261020'

def require_true_forward_clock(asof:date,now:datetime):
    """Only a genuine postclose signal or next *business* morning before open.

    Morning capture is necessary to share the existing daily 08:00 A-share
    task. Missing official weekday-holiday calendars fail closed; a stale
    older session may never be relabelled as a fresh prospective signal.
    """
    if now.tzinfo is None:
        raise ValueError('Clock must include timezone')
    clock=now.astimezone(ZoneInfo('Asia/Shanghai'))
    if asof<=FREEZE_DATE or asof>clock.date() or asof.weekday()>4:
        raise ValueError('Invalid as-of session, no postdated or in-sample receipt')
    same_day=(clock.date()==asof and clock.time()>=time(15,30))
    next_business=asof+timedelta(days=1)
    while next_business.weekday()>4:
        next_business+=timedelta(days=1)
    premarket=(clock.date()==next_business and
               clock.weekday()<5 and time(7,0)<=clock.time()<time(9,20))
    if not (same_day or premarket):
        raise ValueError('Not postclose or verified next business morning preopen; never backfill as forward')

def receipt(panel:pd.DataFrame,risk,asof:date,manifest_sha:str,manifest_asof:str,now:datetime)->dict:
    require_true_forward_clock(asof,now)
    if pd.Timestamp(manifest_asof).date()<asof:
        raise ValueError('Gold manifest older than requested signal day')
    dates=pd.to_datetime(panel['trade_date']).dt.date
    if dates.max()!=asof:
        raise ValueError('Gold panel not closed through signal date')
    expected={str(s) for s in panel.loc[dates.eq(asof),'vendor_symbol']}
    if len(expected)<POLICY.min_breadth_symbols:
        raise ValueError('Insufficient current market universe; cannot claim NO SIGNAL')
    features=signal_day_features(panel)
    original=build_signals(panel,POLICY,risk)
    allowed,statistics=filter_signals(original,features,LOOSE)
    ranked=rank_signals(allowed,features,'LESS_RECENT_RUNUP_FIRST')
    selected=[]
    for symbol,score in ranked.get(pd.Timestamp(asof),[])[:10]:
        if symbol not in expected:
            raise AssertionError('Candidate not in current Gold universe')
        f=features.loc[(pd.Timestamp(asof),symbol)]
        selected.append({'symbol':symbol,'legacy_score':round(float(score),8),
                         'known_prior5_return':round(float(f.prior5_gain),8),
                         'known_breakout':round(float(f.breakout),8)})
    code_sha=os.environ.get('V7_RESEARCH_COMMIT_SHA','')
    if code_sha and (len(code_sha)!=40 or any(ch not in '0123456789abcdef' for ch in code_sha)):
        raise ValueError('Invalid pinned research source SHA')
    core={'version':VERSION,
          'research_code_sha':code_sha or 'UNSET_OFFLINE_TEST_ONLY',
          'mode':'PAPER_CANDIDATE_SNAPSHOT_ONLY_NO_ORDER',
          'as_of':str(asof),'captured_at_shanghai':now.astimezone(
            ZoneInfo('Asia/Shanghai')).isoformat(),
          'gold_manifest_sha256':manifest_sha,
          'gold_as_of':manifest_asof,
          'strategy_rules':{
            'selection':'V2_LOOSE THEN LOWEST_PRIOR5_GAIN',
            'market_above_ma20_fraction_min':.55,
            'target_order_cny':40000,'minimum_order_cny':30000,
            'maximum_order_cny':50000,
            'max_simultaneous_positions':1,'T_plus_1':True,
            'max_open_gap_up':.02,'stop_loss':.03,'take_profit':.05,
            'hold_sessions_planned':[3,5], 'paper_slippage_per_side':.003},
          'candidate_count':len(ranked.get(pd.Timestamp(asof),[])),
          'top10_candidates_in_priority_order':selected,
          'decision':'WAIT' if not selected else 'CANDIDATES_ONLY_VERIFY_NEXT_OPEN',
          'disclosure':[
            'No buy order, no live advice and no next-day execution presumed.',
            'Prior existing position and actual open/limit/suspension/risk gates must be checked before shadow BUY.',
            'Next open gap, T+1, queues, bid-ask and exit slippage cannot be known at signal close.',
            'Only this timestamped immutable record captured before next open can count as prospective evidence.',
            'Morning capture is valid only before the next business-day market opening; weekday holidays without exchange calendar must fail closed.',
            '30+ full prospective independent trades needed before strategy qualification.']}
    digest=hashlib.sha256(json.dumps(core,ensure_ascii=False,
                                      sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()
    return {'sha256_of_receipt_fields':digest,**core}

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--market-root',type=Path,required=True)
    p.add_argument('--risk-root',type=Path,required=True)
    p.add_argument('--as-of',required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    day=date.fromisoformat(a.as_of)
    now=datetime.now(ZoneInfo('Asia/Shanghai'))
    require_true_forward_clock(day,now)
    panel,manifest,sha=load_gold(a.market_root,'2026-01-01',a.as_of)
    from gold_pit_risk_guards import GoldPITRisk
    risk=GoldPITRisk(a.risk_root)
    if not risk.limits or not risk.names:
        raise RuntimeError('Absent PIT risk evidence')
    result=receipt(panel,risk,day,sha,manifest['as_of_date'],now)
    if a.output.exists():
        raise FileExistsError('Do not overwrite timestamped forward receipt')
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'result':result['mode'],'as_of':a.as_of,
                      'candidate_count':result['candidate_count'],
                      'sha256':result['sha256_of_receipt_fields']},
                     ensure_ascii=False))
if __name__=='__main__':main()
