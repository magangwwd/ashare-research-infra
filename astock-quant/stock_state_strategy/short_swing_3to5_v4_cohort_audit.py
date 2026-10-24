"""Retrospective V3 cohort diagnostics, read-only and non-tradable.

Check completed-trade source integrity, 15m symbol overlap, early forced exits,
and entry-time score rank across a fixed 2024/25 -> 2026 calendar partition.
No new portfolio curve, trades, trading signals or model is produced.
"""
from __future__ import annotations
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from statistics import mean, median

PINNED_V3_SHA = '5bfb82656806017c08c38e75bfc3ffbbe50494c3f2261dc9b10c1152cb0ddd43'
PINNED_GOLD_SHA = '56003f3eab3ea4ad405ac692e11cb7ff42bb0b700d177903311b8829cd24ded6'
INTRADAY = {'STOP_INTRADAY_ASSUMED', 'TARGET_INTRADAY_ASSUMED'}


def group(rows):
    return {'n': len(rows), 'pnl_cny': round(sum(float(t['pnl_cny']) for t in rows), 2),
            'two_session_count': sum(t['holding_sessions'] == 2 for t in rows),
            'deferred_count': sum(t['exit_reason'] == 'DEFERRED_STOP_OR_TIME' for t in rows),
            'positive_count': sum(t['pnl_cny'] > 0 for t in rows),
            'mean_trade_net_return': round(mean(t['net_return'] for t in rows), 8) if rows else None}


def inspect_variant(v, minute_symbols):
    t = v['trades']
    b = [o for o in v['orders'] if o.get('side') == 'BUY_ALL']
    s = [o for o in v['orders'] if o.get('side') == 'SELL_ALL']
    if v.get('unclosed_position') or not (len(t) == len(b) == len(s) == v['summary']['closed_trades']):
        raise ValueError('Missing trades or open position')
    if abs(group(t)['pnl_cny'] - v['summary']['realized_pnl_cny']) > .05:
        raise ValueError('P&L mismatch')
    for i, (x, buy, sell) in enumerate(zip(t,b,s)):
        if (x['symbol'],x['entry_date'],x['entry_shares']) != (
               buy['symbol'],buy['date'],buy['shares']):
            raise ValueError('Buy misalignment ' + str(i))
        if (x['symbol'],x['exit_date'],x['entry_shares']) != (
               sell['symbol'],sell['date'],sell['shares']):
            raise ValueError('Sell misalignment ' + str(i))
        if x['entry_date'] > x['exit_date'] or not (0 <= float(buy['score']) < 1):
            raise ValueError('Bad chronology / pre-entry score')
    years = {year:[x for x in t if x['entry_date'][:4] == year]
             for year in ('2024','2025','2026')}
    if sum(len(a) for a in years.values()) != len(t) or not all(years.values()):
        raise ValueError('Historical cohort missing or unexpected date')
    train=[(buy,x) for buy,x in zip(b,t) if x['entry_date'][:4] in ('2024','2025')]
    test=[(buy,x) for buy,x in zip(b,t) if x['entry_date'][:4] == '2026']
    threshold=median(float(buy['score']) for buy,x in train)
    def score_groups(pairs):
        return {'below_train_median':group([x for buy,x in pairs if float(buy['score']) < threshold]),
                'at_or_above_train_median':group([x for buy,x in pairs if float(buy['score']) >= threshold])}
    symbols={x['symbol'] for x in t}
    winners=sorted(t,key=lambda x:x['pnl_cny'],reverse=True)
    return {'baseline':v['name'],'all_trades':group(t),
            'unique_traded_symbols':len(symbols),
            'minute_symbol_overlap':sorted(symbols & minute_symbols),
            'intraday_assumed_exits_missing_minute_symbol':sum(
                 x['exit_reason'] in INTRADAY and x['symbol'] not in minute_symbols for x in t),
            'two_session_trades':group([x for x in t if x['holding_sessions']==2]),
            'deferred_exit_trades':group([x for x in t if x['exit_reason']=='DEFERRED_STOP_OR_TIME']),
            'target_gap_trades':group([x for x in t if x['exit_reason']=='TARGET_GAP']),
            'by_entry_year':{y:{'all_trades':group(a),
                'two_session_trades':group([x for x in a if x['holding_sessions']==2])}
                             for y,a in years.items()},
            'exit_reasons':dict(sorted(Counter(x['exit_reason'] for x in t).items())),
            'largest_winner':{k:winners[0][k] for k in ('symbol','entry_date','exit_date','pnl_cny','exit_reason')},
            'static_pnl_without_largest_winner':round(group(t)['pnl_cny']-winners[0]['pnl_cny'],2),
            'training_score_median_2024_25':threshold,
            'descriptive_score_groups_2024_25':score_groups(train),
            'descriptive_score_groups_2026':score_groups(test),
            'checks':'LEDGER_RECONCILED_NONCAUSAL_ONLY'}


def audit(v3bytes, provider_manifest):
    if sha256(v3bytes).hexdigest() != PINNED_V3_SHA:
        raise ValueError('V3 source JSON hash mismatch')
    d=json.loads(v3bytes)
    if d.get('version')!='MAINBOARD_SWING_3TO5_STATEFUL_GUARDS_PAPER_V3' or (
         d.get('mode')!='RESEARCH_ONLY_NO_LIVE_AUTHORIZATION'):
        raise ValueError('Unexpected V3 provenance')
    if d.get('gold_source',{}).get('sha256')!=PINNED_GOLD_SHA:
        raise ValueError('Gold snapshot changed')
    if provider_manifest.get('version')!='BAOSTOCK_600351_15M_PROBE_V1' or (
        provider_manifest.get('symbol')!='600351.SH') or (
        provider_manifest.get('non_adjusted') is not True) or (
        provider_manifest.get('authorization')!='RESEARCH_ONLY_NO_TRADING'):
        raise ValueError('Unsupported minute provider manifest')
    if [x['v2_base'] for x in d['datasets']] != ['BALANCED','LOOSE']:
        raise ValueError('Wrong V2 comparison source')
    minute_symbols={provider_manifest['symbol']}
    results=[]
    for x in d['datasets']:
        v=next((a for a in x['variants'] if a['name']=='NO_GATE_REPRODUCED_V2'),None)
        if v is None: raise ValueError('No reproduced frozen baseline')
        results.append({'v2_base':x['v2_base'],**inspect_variant(v,minute_symbols)})
    return {'version':'V4_BASELINE_EARLY_STOP_AND_MINUTE_COVERAGE_AUDIT',
            'mode':'POSTHOC_NONCAUSAL_NOT_A_BACKTEST',
            'source_v3_sha256':PINNED_V3_SHA,'gold_sha256':PINNED_GOLD_SHA,
            'minute_provider_version':provider_manifest['version'],
            'minute_provider_symbol':provider_manifest['symbol'],
            'groups':results,
            'limitations':[
             'Symbol overlap does not validate timestamps, intraday fill prices or daily Gold consistency.',
             '2026 calendar holdout is retrospective: upstream V2 parameters were studied on full period.',
             'No retrospective removal of losing or early-exit trades is a legal backtest of a pre-trade rule.',
             'Score groups are labels on previously executed trades only; not portfolio profit forecasts.',
             'Largest winner deletion is static and does not account for freed cash or new stock selection.',
             'No live trading, no signals, no new schedules, no original V1-V3 changes.'
            ]}


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--v3',required=True,type=Path)
    p.add_argument('--minute-provider-manifest',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    a=p.parse_args()
    if a.output.exists(): raise FileExistsError('Do not overwrite historical evidence')
    result=audit(a.v3.read_bytes(),json.loads(a.minute_provider_manifest.read_text()))
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    for x in result['groups']:
        print(x['v2_base'],x['all_trades'],x['two_session_trades'],
              'minute_covered',len(x['minute_symbol_overlap']))
    print('V4_COHORT_AUDIT_PASS')


if __name__=='__main__':
    main()
