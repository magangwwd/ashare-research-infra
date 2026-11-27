"""Read-only backward-year stress test of already frozen 600031 rules.

Usage: python skills/stock-personality-analysis/backward_stress_v06.py --daily 2024_five_stock_daily.csv --out stress.json
2024 is retrospective and must never be described as prospective holdout.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import stock_personality as personality
import execution_backtest as execution

KEYS = [('D_breakout', 5), ('G_upper_wick', 3), ('B_breakdown', 5), ('F_tight_lowvol', 5)]
PEERS = ['000157.SZ', '000425.SZ', '600761.SH', '601100.SH']
TARGET = '600031.SH'


def audit(panel, year=2024):
    """Require an aligned five-stock calendar; no missing OHLCV."""
    codes = sorted(panel.ts_code.unique())
    if codes != sorted([TARGET, *PEERS]):
        raise ValueError(f'Expected target + 4 frozen peers; got {codes}')
    if panel.trade_date.dt.year.nunique() != 1 or panel.trade_date.dt.year.iloc[0] != year:
        raise ValueError('Supply one complete calendar year only; no mixed-year labels')
    opens = panel.pivot(index='trade_date', columns='ts_code', values='open').sort_index()
    if opens.isna().any().any():
        raise ValueError('Missing peer observations; do not substitute nearby dates')
    target = panel[panel.ts_code == TARGET].sort_values('trade_date').reset_index(drop=True)
    output = {'scope': f'{year} backward retrospective stress test, NOT prospective holdout',
              'year': year, 'start': str(opens.index.min().date()),
              'end': str(opens.index.max().date()), 'sessions': len(opens),
              'rows': len(panel), 'pre_close_nulls': int(panel.pre_close.isna().sum()),
              'target': TARGET, 'peers': PEERS, 'results': []}
    for signal, hold in KEYS:
        feature = personality.features(target, hold)
        days = int((feature.ready & feature[signal]).sum())
        trades = execution.trades(target, signal, hold)
        matched = []
        for trade in trades:
            start = pd.Timestamp(trade['entry_date'])
            end = pd.Timestamp(trade['exit_date'])
            peer_gross = 100 * (opens.loc[end, PEERS] / opens.loc[start, PEERS] - 1).mean()
            matched.append({'signal_date': trade['signal_date'],
                            'entry_date': trade['entry_date'], 'exit_date': trade['exit_date'],
                            'target_gross_pct': trade['gross_return_pct'],
                            'target_net_pct': trade['net_return_pct'],
                            'peer_gross_pct': round(float(peer_gross), 4),
                            'alpha_gross_pp': round(float(trade['gross_return_pct'] - peer_gross), 4),
                            'ex_factor_during_holding': trade['ex_factor_during_holding']})
        n = len(matched)
        def mean(key):
            return round(float(np.mean([t[key] for t in matched])), 3) if n else None
        output['results'].append({'signal': signal, 'hold_sessions': hold,
            'signal_days': days, 'independent_trades': n,
            'mean_target_gross_pct': mean('target_gross_pct'),
            'mean_target_net_pct': mean('target_net_pct'),
            'mean_peer_gross_pct': mean('peer_gross_pct'),
            'mean_target_minus_peer_gross_pp': mean('alpha_gross_pp'),
            'underperform_peer_n': sum(t['alpha_gross_pp'] < 0 for t in matched),
            'possible_ex_action_n': sum(abs(t['ex_factor_during_holding'] - 1) > 1e-6 for t in matched),
            'trades': matched})
    output['limitations'] = [
        'Rules were selected using other historical years; backward stress is not prospective holdout.',
        'Gross target-minus-peer return is not fee-adjusted alpha.',
        'No verified dividends, price-limit/suspension fills, or minimum commissions.',
        'Small episode counts cannot establish significance.']
    return output


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--daily', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--year', type=int, default=2024)
    a = p.parse_args()
    panel = personality.load_panel(a.daily)
    result = audit(panel, a.year)
    result['input_sha256'] = hashlib.sha256(Path(a.daily).read_bytes()).hexdigest()
    Path(a.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('sessions', result['sessions'], 'results', a.out)


if __name__ == '__main__':
    main()
