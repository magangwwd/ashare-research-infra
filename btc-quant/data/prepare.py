"""CLI: Binance monthly ZIP archives -> validated daily UTC CSV and audit JSON."""
import argparse
from pathlib import Path
from validate import read_4h, aggregate_daily, save_daily

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--source',required=True,help='Directory of BTCUSDT 4h ZIP archives')
    p.add_argument('--out',required=True)
    a = p.parse_args()
    paths = sorted(list(Path(a.source).glob('*.zip'))+list(Path(a.source).glob('*.csv')))
    bars, digest = read_4h(paths)
    daily, dropped = aggregate_daily(bars)
    print(save_daily(daily,a.out,digest,len(bars),dropped))
