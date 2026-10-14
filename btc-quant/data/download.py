"""Fetch official Binance Vision BTCUSDT Spot 4h monthly archives (NO API key)."""
import argparse
import hashlib
from pathlib import Path
import urllib.error
import urllib.request

BASE = 'https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/4h/'


def months(start, end):
    y, m = map(int, start.split('-'))
    end_key = tuple(map(int, end.split('-')))
    while (y, m) <= end_key:
        yield f'{y:04d}-{m:02d}'
        m += 1
        if m == 13:
            y, m = y + 1, 1


def download(start, end, dest):
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    results = []
    for month in months(start, end):
        name = f'BTCUSDT-4h-{month}.zip'
        path = dest/name
        if not path.exists():
            try:
                with urllib.request.urlopen(urllib.request.Request(BASE+name, headers={'User-Agent':'BTC-Research/1.0'}), timeout=45) as response:
                    content = response.read()
            except urllib.error.HTTPError as exc:
                raise RuntimeError(f'Missing official archive {name} HTTP {exc.code}') from exc
            if not content.startswith(b'PK'):
                raise ValueError(f'Invalid ZIP response for {name}')
            tmp = path.with_suffix('.download')
            tmp.write_bytes(content)
            tmp.rename(path)
        results.append((str(path), hashlib.sha256(path.read_bytes()).hexdigest()))
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', required=True, help='YYYY-MM')
    parser.add_argument('--end', required=True, help='YYYY-MM, use complete months only')
    parser.add_argument('--out', required=True)
    a = parser.parse_args()
    for name, sha in download(a.start, a.end, a.out):
        print(name, sha)
