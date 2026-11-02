"""V4 immutable V3 execution-assumption audit; diagnostic only, NOT a re-simulation.

Reads a previously verified V3 JSON. Does NOT change trades, reprice orders,
construct hypothetical NAV paths, or claim that intraday orders would fill.
"""
from __future__ import annotations
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

VERSION='MAINBOARD_SHORT_SWING_V4_EXECUTION_SENSITIVITY_AUDIT'
INTRADAY={'STOP_INTRADAY_ASSUMED','TARGET_INTRADAY_ASSUMED'}
DAILY_OHLC=INTRADAY | {'TIME_5_CLOSE'}
OPEN_PRICE={'STOP_GAP','TARGET_GAP','DEFERRED_STOP_OR_TIME'}
ALL_REASONS=DAILY_OHLC | OPEN_PRICE
STRESS_RATES=(0.0025,0.005,0.01)

def audit_variant(item):
    trades=item['trades']
    orders=item['orders']
    if item['unclosed_position']:
        raise ValueError('Cannot run closed-trade sensitivity against an open position')
    if len(trades)!=item['summary']['closed_trades']:
        raise ValueError('Summary trade count disagrees with ledger')
    buys=[o for o in orders if o.get('side')=='BUY_ALL']
    sells=[o for o in orders if o.get('side')=='SELL_ALL']
    if len(buys)!=len(trades) or len(sells)!=len(trades):
        raise ValueError('Open/close ledger cardinality mismatch')
    for i,(t,b,s) in enumerate(zip(trades,buys,sells)):
        if t['exit_reason'] not in ALL_REASONS:
            raise ValueError('Unknown exit assumption on trade %d'%i)
        if (t['symbol'],t['entry_date'],t['entry_shares'])!=(b['symbol'],b['date'],b['shares']):
            raise ValueError('Buy ledger mismatch at %d'%i)
        if (t['symbol'],t['exit_date'],t['entry_shares'],t['exit_reason'])!=(
              s['symbol'],s['date'],s['shares'],s['reason']):
            raise ValueError('Sell ledger mismatch at %d'%i)
        expected='DAILY_OHLC_ASSUMPTION' if t['exit_reason'] in DAILY_OHLC else 'OPEN'
        if s.get('execution')!=expected:
            raise ValueError('Exit execution tagging mismatch at %d'%i)
        if t['gross_entry_cny']<=0:
            raise ValueError('Bad entry notional on trade %d'%i)
    counts=Counter(t['exit_reason'] for t in trades)
    net=sum(float(t['pnl_cny']) for t in trades)
    if abs(net-item['summary']['realized_pnl_cny'])>0.05:
        raise ValueError('Realized PnL does not reconcile')
    winners=sorted(trades,key=lambda t:t['pnl_cny'],reverse=True)
    denominator=sum(float(t['gross_entry_cny']) for t in trades if t['exit_reason'] in INTRADAY)
    static_stress=[
        {'additional_adverse_price_fraction':rate,
         'mechanical_pnl_haircut_cny':round(denominator*rate,2),
         'approximate_total_pnl_after_haircut_cny':round(net-denominator*rate,2),
         'approximate_return_on_starting_cash':round(
            (net-denominator*rate)/50000,8)}
        for rate in STRESS_RATES
    ]
    return {
        'name':item['name'],'trades':len(trades),
        'exit_reason_counts':dict(sorted(counts.items())),
        'intraday_assumed_exit_count':sum(counts[r] for r in INTRADAY),
        'daily_ohlc_assumption_count':sum(counts[r] for r in DAILY_OHLC),
        'intraday_assumed_exit_fraction':round(
            sum(counts[r] for r in INTRADAY)/len(trades),6) if trades else 0,
        'all_daily_ohlc_exit_fraction':round(
            sum(counts[r] for r in DAILY_OHLC)/len(trades),6) if trades else 0,
        'assumed_intraday_entry_notional_cny':round(denominator,2),
        'recorded_realized_pnl_cny':round(net,2),
        'recorded_total_return':item['summary']['total_return'],
        'recorded_max_drawdown':item['summary']['max_drawdown'],
        'top_3_profitable_trades':[
            {'symbol':t['symbol'],'date':t['exit_date'],'pnl_cny':t['pnl_cny'],
             'reason':t['exit_reason']} for t in winners[:3]],
        'mechanical_pnl_excluding_top_one_cny':round(net-winners[0]['pnl_cny'],2) if winners else 0,
        'mechanical_pnl_excluding_top_three_cny':round(
            net-sum(t['pnl_cny'] for t in winners[:3]),2),
        'stress':static_stress,
        'positive_base_break_even_adverse_fill_fraction':round(net/denominator,8)
             if net>0 and denominator>0 else None,
        'integrity_checks':'PASS',
    }

def audit(data,raw):
    if data.get('version')!='MAINBOARD_SWING_3TO5_STATEFUL_GUARDS_PAPER_V3':
        raise ValueError('Requires immutable V3 output')
    if data.get('mode')!='RESEARCH_ONLY_NO_LIVE_AUTHORIZATION':
        raise ValueError('Research-only source required')
    if not data.get('gold_source',{}).get('sha256'):
        raise ValueError('Gold source provenance missing')
    if data.get('policy',{}).get('initial_cash_cny')!=50000:
        raise ValueError('Fixed 50k initial cash prerequisite is absent')
    if [z['v2_base'] for z in data.get('datasets',[])]!=['BALANCED','LOOSE']:
        raise ValueError('Expected frozen V3 baseline groups')
    out=[]
    for group in data['datasets']:
        if len(group['variants'])!=5 or group['variants'][0]['name']!='NO_GATE_REPRODUCED_V2':
            raise ValueError('Missing frozen V3 strategy variants')
        out.append({'v2_base':group['v2_base'],
                    'variants':[audit_variant(v) for v in group['variants']]})
    return {'version':VERSION,'mode':'POST_HOC_SENSITIVITY_NOT_EXECUTION_SIMULATION',
            'input_sha256':sha256(raw).hexdigest(),'gold_provenance':data['gold_source'],
            'period':data['period'],'datasets':out,
            'disclosures':[
              '0.25%, 0.5% and 1% adverse execution haircuts apply ONLY to the recorded entry notional of intraday stop/target proxy exits.',
              'Haircuts are first-order static accounting scenarios, NOT replayed executions, alternative NAV or drawdown paths.',
              'Largest-winner removal is a static concentration diagnostic; subsequent cash availability/order selection are not recomputed.',
              'Known OPEN executions were also approximated with slippage; they are NOT verified true fills.',
              'T+1, limit-down, locked order books, corporate actions, intraday sequence and survivor bias remain unverified.',
              'No investment recommendation, live-trading capability, scheduler or model selection is created.',
            ]}

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if args.output.exists():raise FileExistsError('Audit output must be immutable')
    raw=args.input.read_bytes()
    report=audit(json.loads(raw),raw)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'result':'V4_SENSITIVITY_AUDIT_PASS',
       'input_sha256':report['input_sha256'],
       'no_gate_summaries':[
           {'v2_base':g['v2_base'],'summary':g['variants'][0]}
           for g in report['datasets']]},ensure_ascii=False))

if __name__=='__main__':main()
