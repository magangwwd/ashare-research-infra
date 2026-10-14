#!/usr/bin/env python3
"""Audited Binance BTCUSDT Spot native UTC 1d alternative; NO invented 4h bars."""
import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import math
from pathlib import Path
import urllib.request
import zipfile

BASE = 'https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1d/'
FIELDS = ('date', 'open', 'high', 'low', 'close', 'volume')
UTC = dt.timezone.utc

def months(start, end):
    first = tuple(map(int, start.split('-')))
    last = tuple(map(int, end.split('-')))
    if not (2017 <= first[0] <= 2100 and 2017 <= last[0] <= 2100
            and 1 <= first[1] <= 12 and 1 <= last[1] <= 12 and first <= last):
        raise ValueError('invalid YYYY-MM interval')
    year, month = first
    while (year, month) <= last:
        yield f'{year:04d}-{month:02d}'
        month += 1
        if month == 13:
            year, month = year + 1, 1

def fetch_months(start, end, raw):
    raw = Path(raw)
    raw.mkdir(parents=True, exist_ok=True)
    paths = []
    for month in months(start, end):
        name = f'BTCUSDT-1d-{month}.zip'
        path = raw / name
        if not path.is_file():
            request = urllib.request.Request(BASE + name, headers={'User-Agent': 'BTC-Quant-Research/1.0'})
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = response.read()
            if not payload.startswith(b'PK'):
                raise ValueError(f'not a ZIP: {name}')
            tmp = path.with_suffix('.partial')
            tmp.write_bytes(payload)
            tmp.replace(path)
        paths.append(path)
        print(f'Archive: {name}, sha256={hashlib.sha256(path.read_bytes()).hexdigest()}', flush=True)
    return paths

def parse_open(value):
    raw = int(value)
    if raw >= 10**15:
        timestamp = raw / 1_000_000
    elif raw >= 10**12:
        timestamp = raw / 1_000
    else:
        timestamp = raw
    opened = dt.datetime.fromtimestamp(timestamp, UTC)
    if any((opened.hour, opened.minute, opened.second, opened.microsecond)):
        raise ValueError(f'not UTC day open: {value}')
    return opened.date()

def build(paths, output):
    paths = sorted(map(Path, paths))
    if not paths:
        raise ValueError('no native 1d archives')
    days, evidence = {}, []
    for path in paths:
        blob = path.read_bytes()
        sha = hashlib.sha256(blob).hexdigest()
        with zipfile.ZipFile(io.BytesIO(blob)) as handle:
            members = [n for n in handle.namelist() if n.lower().endswith('.csv')]
            if len(members) != 1:
                raise ValueError(f'{path.name}: expected exactly one CSV')
            content = handle.read(members[0]).decode('utf-8-sig')
        n = 0
        for line, values in enumerate(csv.reader(io.StringIO(content)), 1):
            if not values or values[0].strip().lower() in ('open_time', 'opentime'):
                continue
            if len(values) < 6:
                raise ValueError(f'{path.name}:{line}: short row')
            day = parse_open(values[0])
            vals = list(map(float, values[1:6]))
            op, hi, lo, cl, volume = vals
            if not all(map(math.isfinite, vals)) or not (0 < lo <= min(op, cl) <= max(op, cl) <= hi and volume >= 0):
                raise ValueError(f'{path.name}:{line}: invalid OHLCV')
            if day in days:
                raise ValueError(f'duplicate daily candle: {day}')
            days[day] = dict(zip(FIELDS, (day.isoformat(), *vals)))
            n += 1
        evidence.append({'archive': path.name, 'sha256': sha, 'daily_rows': n})
    ordered = sorted(days)
    if len(ordered) < 200:
        raise ValueError(f'need >=200 native daily candles, got {len(ordered)}')
    for before, after in zip(ordered, ordered[1:]):
        if (after-before).days != 1:
            raise ValueError(f'no native daily candle between {before} and {after}; fail closed')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    dst = output/'btc_daily_utc.csv'
    with dst.open('w', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(days[d] for d in ordered)
    audit = {
        'status': 'PASS_NATIVE_1D_NOT_4H_REPAIRED',
        'symbol': 'BTCUSDT Spot',
        'source': 'Binance Vision original monthly 1d klines',
        'source_url': BASE,
        'not_equivalent_to_4h_validated': True,
        'no_synthetic_bars': True,
        'model_limits': [
            'Daily OHLCV cannot prove uninterrupted intraday trading.',
            'Exchange outages can occur within a day; next-open fills can be optimistic.',
            '2018-02-08 primary 4h outage excluded by starting in 2020.'],
        'start': ordered[0].isoformat(),
        'end': ordered[-1].isoformat(),
        'days': len(ordered),
        'utc_daily_csv_sha256': hashlib.sha256(dst.read_bytes()).hexdigest(),
        'raw_archives': evidence,
    }
    (output/'native_daily_validation.json').write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding='utf-8')
    return audit

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--start', default='2020-01')
    p.add_argument('--end', default='2026-09')
    p.add_argument('--raw', required=True)
    p.add_argument('--out', required=True)
    args = p.parse_args()
    audit = build(fetch_months(args.start, args.end, args.raw), args.out)
    print(json.dumps({k:v for k,v in audit.items() if k != 'raw_archives'},
                     ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
