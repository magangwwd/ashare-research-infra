"""Validate Binance Spot 4h CSV/ZIP archives and aggregate complete UTC days.

Input: one or more Binance Vision 4h archives (each row has 12 standard kline
fields, optionally a header). Timestamps may use milliseconds or microseconds.
Missing/duplicate 4h bars and invalid OHLCV cause a hard failure, not imputation.
"""
import csv
import datetime as dt
import hashlib
import io
import json
from pathlib import Path
import zipfile

UTC = dt.timezone.utc
STEP = 4 * 60 * 60


def timestamp(value):
    raw = int(value)
    sec = raw / (1_000_000 if raw >= 10**15 else 1_000 if raw >= 10**12 else 1)
    d = dt.datetime.fromtimestamp(sec, UTC)
    if d.minute or d.second or d.microsecond or d.hour % 4:
        raise ValueError(f"not UTC 4h open: {value}")
    return d


def read_4h(paths):
    result, digest = {}, hashlib.sha256()
    for path in sorted(Path(p) for p in paths):
        blob = path.read_bytes()
        digest.update(path.name.encode() + b'\0' + blob)
        if path.suffix.lower() == '.zip':
            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                names = sorted(n for n in z.namelist() if n.lower().endswith('.csv'))
                if not names:
                    raise ValueError(f'no CSV in {path}')
                sources = [(n, z.read(n).decode('utf-8-sig')) for n in names]
        else:
            sources = [(path.name, blob.decode('utf-8-sig'))]
        for name, content in sources:
            for line_num, row in enumerate(csv.reader(io.StringIO(content)), 1):
                if not row or row[0].strip().lower() in ('open_time', 'opentime'):
                    continue
                if len(row) < 6:
                    raise ValueError(f'{name}:{line_num} short kline')
                try:
                    t = timestamp(row[0])
                    op, high, low, close, vol = map(float, row[1:6])
                except (ValueError, OverflowError) as exc:
                    raise ValueError(f'{name}:{line_num} invalid numeric: {exc}') from exc
                if not (0 < low <= min(op, close) <= max(op, close) <= high and vol >= 0):
                    raise ValueError(f'{name}:{line_num} invalid OHLCV')
                if t in result:
                    raise ValueError(f'duplicate 4h bar: {t.isoformat()}')
                result[t] = (op, high, low, close, vol)
    if not result:
        raise ValueError('no 4h bars')
    times = sorted(result)
    for before, after in zip(times, times[1:]):
        if round((after-before).total_seconds()) != STEP:
            raise ValueError(f'missing 4h candles between {before} and {after}')
    return result, digest.hexdigest()


def aggregate_daily(bars):
    groups = {}
    for t in sorted(bars):
        groups.setdefault(t.date(), []).append((t, bars[t]))
    result, dropped = [], []
    keys = sorted(groups)
    for day in keys:
        data = groups[day]
        if len(data) != 6:
            if day not in (keys[0], keys[-1]):
                raise ValueError(f'incomplete interior UTC day: {day}')
            dropped.append(str(day))
            continue
        if [p[0].hour for p in data] != [0, 4, 8, 12, 16, 20]:
            raise ValueError(f'wrong UTC day start: {day}')
        vals = [v for _, v in data]
        result.append({'date': day.isoformat(), 'open': vals[0][0],
                       'high': max(v[1] for v in vals),
                       'low': min(v[2] for v in vals),
                       'close': vals[-1][3],
                       'volume': sum(v[4] for v in vals)})
    if len(result) < 200:
        raise ValueError(f'insufficient complete daily candles: {len(result)} (<200)')
    return result, dropped


def save_daily(rows, folder, sources_sha256, n_4h, dropped):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder/'btc_daily_utc.csv').open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['date', 'open', 'high', 'low', 'close', 'volume'])
        w.writeheader()
        w.writerows(rows)
    report = {'symbol': 'BTCUSDT Spot', 'interval': '4h -> 1d UTC',
              'start': rows[0]['date'], 'end': rows[-1]['date'],
              'bars_4h': n_4h, 'bars_daily': len(rows),
              'dropped_partial_edge_days': dropped,
              'missing_4h': 0, 'duplicate_4h': 0,
              'input_sha256': sources_sha256,
              'status': 'PASS'}
    (folder/'data_validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report
