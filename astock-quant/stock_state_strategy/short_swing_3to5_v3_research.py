"""V3 PAPER risk overlay: source-pinned V1 execution, entry-only risk gates.

No frozen V1/V2 source modification, no scheduler, no broker actions.
"""
from __future__ import annotations
import argparse
import hashlib
import inspect
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
import short_swing_3to5_v1 as v1
from short_swing_3to5_v2_research import SPECS, signal_day_features, filter_signals

V3_VERSION='MAINBOARD_SWING_3TO5_STATEFUL_GUARDS_PAPER_V3'
PINNED_V1_GIT_BLOB_SHA='b99b95e3b7ca1c98f42c7b6176819fe8412b5b85'

@dataclass(frozen=True)
class GateSpec:
    name: str
    dd_halt_fraction: float | None = None
    consecutive_losses: int | None = None
    cooldown_sessions: int = 0
    def __post_init__(self):
        if self.dd_halt_fraction is not None and not (.05 <= self.dd_halt_fraction <= .30):
            raise ValueError('Drawdown halt trigger must be between 5% and 30%')
        if self.consecutive_losses is None and self.cooldown_sessions:
            raise ValueError('Cannot cooldown without consecutive loss threshold')
        if self.consecutive_losses is not None and not (2 <= self.consecutive_losses <= 5 and self.cooldown_sessions >= 1):
            raise ValueError('Invalid loss streak or cooldown')

GATES=(
    GateSpec('LOSS2_COOLDOWN_10',consecutive_losses=2,cooldown_sessions=10),
    GateSpec('DD_HALT_18',dd_halt_fraction=.18),
    GateSpec('DD_HALT_22',dd_halt_fraction=.22),
    GateSpec('DD18_AND_LOSS2',dd_halt_fraction=.18,consecutive_losses=2,cooldown_sessions=10),
)

@dataclass
class EntryGate:
    spec: GateSpec
    loss_streak: int = 0
    pause_through_index: int = -1
    halted: bool = False
    events: list[dict] = field(default_factory=list)
    denied_sessions: int = 0

    def on_exit(self,i: int,day: pd.Timestamp,trade: dict)->None:
        self.loss_streak=self.loss_streak+1 if trade['pnl_cny']<0 else 0
        if self.spec.consecutive_losses is not None and self.loss_streak>=self.spec.consecutive_losses:
            self.pause_through_index=max(self.pause_through_index,i+self.spec.cooldown_sessions)
            self.events.append({'date':str(day.date()),'kind':'LOSS_STREAK_PAUSE',
                                'losses':self.loss_streak,'sessions':self.spec.cooldown_sessions})
            self.loss_streak=0

    def permit_entry(self,i: int,day: pd.Timestamp,cash: float,peak: float)->bool:
        dd=cash/peak-1 if peak>0 else 0
        if not self.halted and self.spec.dd_halt_fraction is not None and dd<=-self.spec.dd_halt_fraction:
            self.halted=True
            self.events.append({'date':str(day.date()),'kind':'PERMANENT_NEW_ENTRY_HALT',
                                'cash_drawdown_at_decision':round(dd,8),
                                'threshold':self.spec.dd_halt_fraction})
        allowed=not self.halted and i>self.pause_through_index
        if not allowed:self.denied_sessions+=1
        return allowed

def _instrument_pinned_v1():
    """Fail-closed mirror of unmodified V1; only permits refusal of fresh BUY orders.

    Validate exact source hash and all three string anchors before compilation.
    A no-gate run is independently compared with V1 on each real Gold dataset.
    """
    source=inspect.getsource(v1.simulate)
    blob=Path(inspect.getfile(v1.simulate)).read_bytes()
    git_blob=hashlib.sha1(b'blob '+str(len(blob)).encode()+b'\\0'+blob).hexdigest()
    if git_blob!=PINNED_V1_GIT_BLOB_SHA:
        raise RuntimeError('Frozen V1 Git blob SHA changed; abort V3 risk study')
    patches=[
      ('''def simulate(panel: pd.DataFrame, risk: GoldPITRisk, policy: Policy,
             signals: dict | None = None, start: str | None = None) -> dict:''',
       '''def simulate_with_gate(panel: pd.DataFrame, risk: GoldPITRisk, policy: Policy,
                       signals: dict | None = None, start: str | None = None,
                       gate: EntryGate | None = None) -> dict:'''),
      ('''            position = None
            last_mark = None
            exited_today = True''',
       '''            if gate is not None:
                gate.on_exit(i, day, trades[-1])
            position = None
            last_mark = None
            exited_today = True'''),
      ('''        if position is None and not exited_today and pending is not None and (start is None or day >= pd.Timestamp(start)):
            signal_day, choices = pending''',
       '''        if position is None and not exited_today and pending is not None and (start is None or day >= pd.Timestamp(start)):
            if gate is not None and pending[1] and not gate.permit_entry(i, day, cash, max_nav):
                pending = None
        if position is None and not exited_today and pending is not None and (start is None or day >= pd.Timestamp(start)):
            signal_day, choices = pending'''),
    ]
    for old,new in patches:
        if source.count(old)!=1:raise RuntimeError('V1 execution anchor missing or ambiguous')
        source=source.replace(old,new,1)
    env=dict(v1.simulate.__globals__)
    env['EntryGate']=EntryGate
    exec(compile(source,'<sha-pinned-v1-simulator-entry-gate>','exec'),env)
    return env['simulate_with_gate']

simulate_with_gate=_instrument_pinned_v1()

def slim(name,sim,gate):
    return {'name':name,'summary':sim['summary'],'orders':sim['orders'],
            'trades':sim['trades'],'daily_nav':sim['daily_nav'],
            'events':gate.events if gate else [],
            'entry_blocked_sessions':gate.denied_sessions if gate else 0,
            'unclosed_position':sim['open_position'] is not None}

def research(panel,risk,policy,start,end):
    original=v1.build_signals(panel,policy,risk)
    features=signal_day_features(panel)
    datasets=[]
    for spec in (SPECS[1],SPECS[2]):
        selected,counts=filter_signals(original,features,spec)
        legacy=v1.simulate(panel,risk,policy,signals=selected,start=start)
        reproduction=simulate_with_gate(panel,risk,policy,signals=selected,start=start)
        for key in ('summary','orders','trades','daily_nav','exceptions','open_position'):
            if legacy[key]!=reproduction[key]:
                raise AssertionError('V3 baseline differs from V1: '+spec.name+'/'+key)
        variants=[slim('NO_GATE_REPRODUCED_V2',legacy,None)]
        for gate_spec in GATES:
            gate=EntryGate(gate_spec)
            sim=simulate_with_gate(panel,risk,policy,signals=selected,start=start,gate=gate)
            variants.append(slim(gate_spec.name,sim,gate))
        datasets.append({'v2_base':spec.name,'filter_counts':counts,'variants':variants})
    return {'version':V3_VERSION,'mode':'RESEARCH_ONLY_NO_LIVE_AUTHORIZATION',
            'period':{'start':start,'end':end},'policy':asdict(policy),
            'pinned_v1_git_blob_sha':PINNED_V1_GIT_BLOB_SHA,
            'fixed_gates':[asdict(x) for x in GATES],'datasets':datasets,
            'disclosures':[
              'All 2024–2026 data are previously studied; no untouched out-of-sample claim.',
              'New-entry halt does NOT guarantee drawdown below 25%, due to T+1 and overnight price gaps.',
              'Loss cooldown uses completed historical trades only; exits are never blocked.',
              'Daily OHLC/factor fills remain proxies; no full-market 15m evidence or real broker orders.',
              'No automatic historical-winner selection or scheduler.',
            ]}

def main():
    p=argparse.ArgumentParser(description='V3 PAPER risk research; never sends live orders')
    p.add_argument('--market-root',type=Path,required=True)
    p.add_argument('--risk-root',type=Path,required=True)
    p.add_argument('--start',default='2024-01-01')
    p.add_argument('--end',required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    now=datetime.now(ZoneInfo('Asia/Shanghai'))
    if pd.Timestamp(args.end).date()>now.date() or (pd.Timestamp(args.end).date()==now.date() and now.hour<16):
        p.error('Backtest must end on a fully closed historical session')
    policy=v1.Policy()
    policy.validate()
    panel,manifest,sha=v1.load_gold(args.market_root,args.start,args.end)
    from gold_pit_risk_guards import GoldPITRisk
    risk=GoldPITRisk(args.risk_root)
    if not risk.limits or not risk.names:raise RuntimeError('PIT records missing')
    output=research(panel,risk,policy,args.start,args.end)
    output['gold_source']={'as_of':manifest['as_of_date'],'sha256':sha,'pit':risk.summary()}
    if args.output.exists():raise FileExistsError('Immutable output already exists')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(output,indent=2,ensure_ascii=False,default=str),encoding='utf-8')
    print(json.dumps({'version':V3_VERSION,'datasets':[
      {'base':d['v2_base'],'variants':[{'name':v['name'],'summary':v['summary'],'events':len(v['events'])}
       for v in d['variants']]} for d in output['datasets']]},ensure_ascii=False))

if __name__=='__main__':main()
