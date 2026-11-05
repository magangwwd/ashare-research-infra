"""Read-only coverage and frozen-signal audit for partial-year five-stock panels.

A partial calendar year must never be labeled a full-year backtest. Intended
for 600031's verified Sep-Dec 2023 source partitions, but reusable for other
partial years. Does not modify input data or select trading parameters.
"""
import argparse
import hashlib
import json
from pathlib import Path
import pandas as pd
from backward_stress_v06 import audit, TARGET, PEERS
from stock_personality import load_panel


def inspect(panel, year, expected_months):
    expected_months = tuple(sorted(set(int(x) for x in expected_months)))
    if not expected_months or any(m < 1 or m > 12 for m in expected_months):
        raise ValueError('Invalid expected months')
    if sorted(panel.ts_code.unique()) != sorted([TARGET, *PEERS]):
        raise ValueError('Expected target and frozen peers')
    if panel.trade_date.dt.year.nunique() != 1 or int(panel.trade_date.dt.year.iloc[0]) != year:
        raise ValueError('Mixed years')
    months = tuple(sorted(panel.trade_date.dt.month.unique().tolist()))
    if months != expected_months:
        raise ValueError(f'Month coverage mismatch: actual {months}, expected {expected_months}')
    calendars = [set(g.trade_date) for _, g in panel.groupby('ts_code')]
    if any(c != calendars[0] for c in calendars[1:]):
        raise ValueError('Five-stock calendars are not aligned')
    if panel.duplicated(['ts_code', 'trade_date']).any():
        raise ValueError('Duplicate stock/date')
    result = audit(panel, year)
    result['coverage_months'] = list(months)
    result['partial_year'] = len(months) < 12
    result['coverage_label'] = (f'{year} partial months {list(months)}' if result['partial_year']
                                else f'{year} all 12 months (session completeness requires exchange calendar)')
    result['warning'] = 'This is retrospective and a partial-year sample; no untouched holdout.'
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--daily', required=True)
    p.add_argument('--year', type=int, required=True)
    p.add_argument('--months', required=True, help='Comma-separated months actually expected, e.g. 9,10,11,12')
    p.add_argument('--out', required=True)
    a = p.parse_args()
    panel = load_panel(a.daily)
    result = inspect(panel, a.year, [int(m) for m in a.months.split(',')])
    result['input_sha256'] = hashlib.sha256(Path(a.daily).read_bytes()).hexdigest()
    Path(a.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(result['coverage_label'], result['sessions'], result['rows'])

if __name__ == '__main__':
    main()
