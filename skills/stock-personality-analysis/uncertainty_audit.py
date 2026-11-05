#!/usr/bin/env python3
"""Read-only uncertainty and minute completeness audit (v0.3).

Retrospective and already inspected data; bootstrap assumes IID trades, and
Bonferroni across nine signals does not account for all horizons/regimes.
No inference of a blind holdout or live trading edge.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import numpy as np
import pandas as pd

SELECTED = [('D_breakout', 5), ('G_upper_wick', 3),
            ('B_breakdown', 5), ('F_tight_lowvol', 5)]

def wilson(success, n, z=1.959963984540054):
    if not 0 <= success <= n or n < 0:
        raise ValueError('invalid sample')
    if not n:
        return None
    p = success / n
    den = 1 + z*z/n
    ctr = (p + z*z/(2*n))/den
    rad = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))/den
    return [round(100*(ctr-rad), 2), round(100*(ctr+rad), 2)]

def boot_mean_ci(values, seed=600031, draws=10000, alpha=.05):
    vals = np.asarray(values, dtype=float)
    if len(vals) < 5 or not np.isfinite(vals).all():
        return None
    rng = np.random.default_rng(seed)
    means = np.empty(draws)
    for i in range(0, draws, 1000):
        k = min(1000, draws-i)
        means[i:i+k] = rng.choice(vals, size=(k, len(vals)), replace=True).mean(axis=1)
    return [round(float(v), 3) for v in np.percentile(means, [100*alpha/2, 100*(1-alpha/2)])]

def audit_minutes(daily, minutes, target='600031.SH'):
    d = daily[daily.ts_code == target].copy()
    m = minutes[minutes.ts_code == target].copy()
    if d.empty or m.empty:
        raise ValueError('missing target data')
    if m.duplicated(['ts_code', 'trade_time']).any():
        raise ValueError('duplicate minute timestamps')
    m['date'] = pd.to_datetime(m['date']).dt.strftime('%Y-%m-%d')
    d['trade_date'] = pd.to_datetime(d['trade_date']).dt.strftime('%Y-%m-%d')
    g = m.groupby('date').agg(bars=('close','size'), open=('open','first'),
         high=('high','max'), low=('low','min'), close=('close','last'),
         vol=('vol','sum'), amount=('amount','sum'))
    base = d.set_index('trade_date').loc[g.index]
    max_err = max(float(np.abs(g[c].to_numpy()-base[c].to_numpy()).max())
                  for c in ('open','high','low','close'))
    vr = (g.vol/base.vol).to_numpy(dtype=float)
    ar = (g.amount/base.amount).to_numpy(dtype=float)
    month = g.index[0][:7]
    days = d[d.trade_date.str.startswith(month)]
    return {'month': month, 'daily_sessions_in_month_through_snapshot': len(days),
            'minute_sessions': len(g), 'minute_bars': len(m),
            'bars_per_session': g.bars.to_dict(),
            'missing_minute_dates_in_daily_month': sorted(set(days.trade_date)-set(g.index)),
            'ohlc_max_absolute_error': round(max_err,8),
            'volume_ratio_minute_shares_to_daily_lots':
                [round(float(vr.min()),6), round(float(vr.max()),6)],
            'amount_ratio_minute_yuan_to_daily_thousand_yuan':
                [round(float(ar.min()),6), round(float(ar.max()),6)]}

def analyze(execution, daily, minutes, seed=600031):
    code = execution['target']
    records = []
    for pat,h in SELECTED:
        ts = execution['stocks'][code][str(h)][pat]['target_trades']
        for yr in ('2025','2026'):
            trades = [t for t in ts if str(t['year']) == yr]
            a = [t['net_return_pct'] for t in trades]
            wins = sum(x > 0 for x in a)
            ci = boot_mean_ci(a, seed=seed+int(yr)+h)
            ci9 = boot_mean_ci(a, seed=seed+int(yr)+h, alpha=.05/9)
            records.append({'pattern':pat, 'holding_sessions':h, 'year':yr,
                'n':len(trades), 'win_n':wins,
                'win_rate_pct':round(100*wins/len(trades),2) if trades else None,
                'win_rate_wilson_95_pct':wilson(wins,len(trades)),
                'mean_net_pct':round(float(np.mean(a)),3) if trades else None,
                'mean_bootstrap_95_pct':ci,
                'mean_bootstrap_bonferroni_9rules_95_pct':ci9,
                'bootstrap_caveat':'IID trade bootstrap, n<5 omitted; nine-rule correction is incomplete'})
    return {'scope':'v0.3 descriptive robustness, NOT blind holdout',
            'seed':seed, 'bootstrap_draws':10000,
            'minute_audit':audit_minutes(daily,minutes,code),
            'selected_patterns':records,
            'limitations':['2026 previously examined','No verified dividend cashflows',
                           'No limit/suspension fill checks','Small samples, correlated regimes',
                           'Only 2025-2026 daily and nine days of 15-minute data']}

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--daily', required=True)
    p.add_argument('--minutes', required=True)
    p.add_argument('--execution', required=True)
    p.add_argument('--out', required=True)
    a = p.parse_args()
    paths = {'daily':a.daily, 'minutes':a.minutes, 'execution':a.execution}
    execution = json.loads(Path(a.execution).read_text(encoding='utf-8'))
    result = analyze(execution, pd.read_csv(a.daily), pd.read_csv(a.minutes))
    result['input_files'] = {k:{'sha256':hashlib.sha256(Path(v).read_bytes()).hexdigest(),
                               'bytes':Path(v).stat().st_size} for k,v in paths.items()}
    Path(a.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('wrote',a.out)

if __name__ == '__main__':
    main()
