#!/usr/bin/env python3
"""Read-only v0.9 audit of overlapping single-stock pattern trades.

Input: archived cross_year_v08 JSON. Does not infer live fill or strategy alpha.
Deterministic priority for simultaneous entry: D_breakout, G_upper_wick,
B_breakdown, F_tight_lowvol. This priority is arbitrary, not optimized.
"""
import argparse
import collections
import hashlib
import itertools
import json
from pathlib import Path

PRIORITY = ('D_breakout', 'G_upper_wick', 'B_breakdown', 'F_tight_lowvol')


def analyze(data, calendar=None):
    events = []
    for result in data['results']:
        signal = result['signal']
        if signal not in PRIORITY:
            raise ValueError(f'Unexpected signal {signal}')
        for t in result['trades']:
            if not t['signal_date'] < t['entry_date'] < t['exit_date']:
                raise ValueError('Invalid trade dates')
            events.append({**t, 'signal': signal, 'hold_sessions': result['hold_sessions']})
    events.sort(key=lambda t: (t['entry_date'], PRIORITY.index(t['signal']), t['exit_date']))
    all_dates = sorted(set(calendar)) if calendar is not None else sorted({d for t in events for d in (t['entry_date'], t['exit_date'])})
    concurrency = {}
    for d in all_dates:
        active = [t for t in events if t['entry_date'] <= d < t['exit_date']]
        concurrency[d] = len(active)
    def choose(order):
        ordered=sorted(events,key=lambda t:(t['entry_date'],order.index(t['signal']),t['exit_date']))
        chosen, blocked=[],[]
        last_exit=''
        for t in ordered:
            if t['entry_date'] < last_exit: blocked.append(t)
            else: chosen.append(t);last_exit=t['exit_date']
        return chosen,blocked
    selected, rejected = choose(PRIORITY)
    priority_sensitivity = [sum(t['net_pnl_yuan'] for t in choose(order)[0]) for order in itertools.permutations(PRIORITY)]
    def summ(ts):
        return {'n':len(ts), 'mean_net_pct':round(sum(t['net_return_pct'] for t in ts)/len(ts),4) if ts else None,
                'sum_net_pnl_yuan':round(sum(t['net_pnl_yuan'] for t in ts),2),
                'years':{str(y):sum(t['year']==y for t in ts) for y in sorted({t['year'] for t in events})}}
    return {'scope':'Retrospective single-stock capital-overlap sensitivity; not a portfolio performance backtest or holdout',
            'priority':list(PRIORITY), 'priority_is_arbitrary':True,
            'input_sessions':data['sessions'], 'input_rows':data['rows'],
            'pattern_independent':summ(events), 'one_position_total':summ(selected),
            'blocked_due_to_capital':summ(rejected),
            'priority_sensitivity_24_orders_pnl_min_max_yuan':[round(min(priority_sensitivity),2),round(max(priority_sensitivity),2)],
            'max_concurrent_pattern_positions':max(concurrency.values(),default=0),
            'calendar_dates_checked':len(all_dates),
            'dates_with_two_or_more_positions':sum(v>=2 for v in concurrency.values()),
            'dates_with_three_or_more_positions':sum(v>=3 for v in concurrency.values()),
            'same_open_multiple_entries':{d:n for d,n in collections.Counter(t['entry_date'] for t in events).items() if n>1},
            'selected_trades':selected, 'blocked_trades':rejected,
            'limitations':['Four patterns can be simultaneous in the prior research; 40k CNY per pattern is not a 40k total-capital portfolio.',
                           'No reinvestment/equity curve, cash drag, dividends, true fill constraints or real order queue.',
                           'Priority is arbitrary and not selected on outcomes; do not infer optimal strategy.',
                           'The signal rules were inspected on these historical years; no blind holdout.']}


def main():
    p=argparse.ArgumentParser();p.add_argument('--input',required=True);p.add_argument('--out',required=True);p.add_argument('--daily',nargs='*',default=[])
    a=p.parse_args(); raw=Path(a.input).read_bytes(); data=json.loads(raw)
    calendar=None
    if a.daily:
        import csv
        calendar=set()
        for path in a.daily:
            with open(path, newline='', encoding='utf-8-sig') as fh:
                for row in csv.DictReader(fh):
                    calendar.add(row['trade_date'])
    result=analyze(data,calendar=calendar);result['source_json_sha256']=hashlib.sha256(raw).hexdigest()
    Path(a.out).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print({k:result[k] for k in ('pattern_independent','one_position_total','blocked_due_to_capital','max_concurrent_pattern_positions','dates_with_two_or_more_positions','dates_with_three_or_more_positions')})

if __name__=='__main__': main()
