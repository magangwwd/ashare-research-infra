"""Exploratory, read-only leave-one-out and year-shift audit for 600031 peer alpha.

All signals were previously examined. These are NOT out-of-sample results.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

PATTERNS = [('D_breakout', 5), ('G_upper_wick', 3),
            ('B_breakdown', 5), ('F_tight_lowvol', 5)]


def exact_sign_p(negative, n):
    k = min(negative, n - negative)
    return min(1., 2 * sum(math.comb(n, j) for j in range(k + 1)) / (2 ** n))


def analyze(panel, execution, seed=600031, draws=10000):
    if draws < 100:
        raise ValueError('draws must be >= 100')
    p = panel.copy()
    p['trade_date'] = pd.to_datetime(p['trade_date'])
    if p.duplicated(['ts_code', 'trade_date']).any():
        raise ValueError('duplicate stock/date')
    if p[['open', 'high', 'low', 'close', 'vol']].isna().any().any():
        raise ValueError('missing OHLCV')
    if not (p.open.gt(0) & p.high.ge(p[['open', 'close', 'low']].max(axis=1))
            & p.low.le(p[['open', 'close', 'high']].min(axis=1))
            & p.vol.ge(0)).all():
        raise ValueError('invalid OHLCV')
    target = execution['target']
    op = p.pivot(index='trade_date', columns='ts_code', values='open').sort_index()
    if target not in op or op.shape[1] < 5 or op.isna().any().any():
        raise ValueError('target, peers or aligned sessions missing')
    peers = sorted(x for x in op if x != target)
    dates = list(op.index)
    idx = {d.strftime('%Y-%m-%d'): i for i, d in enumerate(dates)}
    rng = np.random.default_rng(seed)
    records = []
    vectors = {}
    for pattern, h in PATTERNS:
        for year in (2025, 2026):
            trades = [t for t in execution['stocks'][target][str(h)][pattern]['target_trades']
                      if t['year'] == year]
            values = []
            months = []
            for t in trades:
                signal, entry, end = (t[k] for k in ('signal_date', 'entry_date', 'exit_date'))
                if any(d not in idx for d in (signal, entry, end)):
                    raise ValueError('unmatched date')
                i = idx[signal]
                if (i + h + 1 >= len(dates)
                        or dates[i + 1].strftime('%Y-%m-%d') != entry
                        or dates[i + h + 1].strftime('%Y-%m-%d') != end):
                    raise ValueError('entry/exit calendar mismatch')
                target_ret = 100 * (op.loc[end, target] / op.loc[entry, target] - 1)
                if abs(target_ret - t['gross_return_pct']) > .0002:
                    raise ValueError('gross return mismatch')
                peer_ret = 100 * (op.loc[end, peers] / op.loc[entry, peers] - 1).mean()
                values.append(float(target_ret - peer_ret))
                months.append(signal[:7])
            a = np.asarray(values, dtype=float)
            n = len(a)
            vectors[(pattern, year)] = a
            row = {'pattern': pattern, 'hold_sessions': h, 'year': year, 'n': n,
                   'mean_alpha_pp': round(float(a.mean()), 4) if n else None,
                   'median_alpha_pp': round(float(np.median(a)), 4) if n else None,
                   'negative_alpha_n': int((a < 0).sum()),
                   'distinct_signal_months': len(set(months)),
                   'exact_sign_test_two_sided_p_exploratory':
                       round(exact_sign_p(int((a < 0).sum()), n), 5) if n >= 5 else None,
                   'bootstrap_95_pct_interval_pp': None,
                   'bootstrap_bonferroni_8_tests_95_family_interval_pp': None,
                   'leave_one_trade_out_mean_range_pp': None,
                   'leave_one_month_out_mean_range_pp': None}
            if n > 1:
                loo = [(a.sum() - v) / (n - 1) for v in a]
                row['leave_one_trade_out_mean_range_pp'] = [
                    round(float(min(loo)), 4), round(float(max(loo)), 4)]
            if len(set(months)) > 1:
                lmo = [float(a[[m != excluded for m in months]].mean())
                       for excluded in set(months)]
                row['leave_one_month_out_mean_range_pp'] = [
                    round(min(lmo), 4), round(max(lmo), 4)]
            if n >= 5:
                boots = rng.choice(a, size=(draws, n), replace=True).mean(axis=1)
                row['bootstrap_95_pct_interval_pp'] = [
                    round(float(v), 4) for v in np.quantile(boots, [.025, .975])]
                row['bootstrap_bonferroni_8_tests_95_family_interval_pp'] = [
                    round(float(v), 4) for v in np.quantile(boots, [.05 / 16, 1 - .05 / 16])]
            records.append(row)
    b25, b26 = vectors[('D_breakout', 2025)], vectors[('D_breakout', 2026)]
    if len(b25) >= 5 and len(b26) >= 5:
        delta = (rng.choice(b26, size=(draws, len(b26)), replace=True).mean(axis=1)
                 - rng.choice(b25, size=(draws, len(b25)), replace=True).mean(axis=1))
        regime = {'pattern': 'D_breakout', 'metric': '2026_minus_2025_peer_alpha_pp',
                  'difference_pp': round(float(b26.mean() - b25.mean()), 4),
                  'bootstrap_95_pct_interval_pp': [
                      round(float(v), 4) for v in np.quantile(delta, [.025, .975])]}
    else:
        regime = None
    return {'scope': 'Already explored 2025-2026 data; NOT blind holdout',
            'target': target, 'peers': peers, 'rows': len(p), 'days': len(op),
            'draws': draws, 'seed': seed, 'groups': records,
            'breakout_year_shift': regime,
            'warnings': ['post-selected rules and years; intervals/p-values exploratory',
                         'IID bootstrap ignores clustering and data snooping',
                         'raw peer returns not dividend-adjusted or fill-verified',
                         'eight-group Bonferroni omits unreported exploratory search']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--daily', required=True)
    parser.add_argument('--execution', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--draws', type=int, default=10000)
    args = parser.parse_args()
    output = analyze(pd.read_csv(args.daily),
                     json.loads(Path(args.execution).read_text()), draws=args.draws)
    output['input_sha256'] = {
        k: hashlib.sha256(Path(v).read_bytes()).hexdigest()
        for k, v in [('daily', args.daily), ('execution', args.execution)]}
    Path(args.out).write_text(json.dumps(output, indent=2, ensure_ascii=False),
                              encoding='utf-8')
    print('saved', args.out)
