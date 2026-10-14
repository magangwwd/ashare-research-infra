"""Reproducible research-only BTC Spot backtester. Does not send any orders.

All daily signals use *completed* UTC candles and fill at the *next* daily
open. V3 UTC Monday 00:00 = Beijing Monday 08:00. Cash earns 0%.
"""
import argparse
import csv
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import platform
import statistics

START_CASH = 1000.0
FEE = 0.001
SLIP = 0.0005


def load_daily(path):
    rows = []
    with Path(path).open(newline='') as f:
        for r in csv.DictReader(f):
            v = {'date': date.fromisoformat(r['date'])}
            for k in ('open', 'high', 'low', 'close', 'volume'):
                v[k] = float(r[k])
                if not math.isfinite(v[k]):
                    raise ValueError('non-finite price/volume')
            if not (0 < v['low'] <= min(v['open'], v['close']) <= max(v['open'], v['close']) <= v['high'] and v['volume'] >= 0):
                raise ValueError(f"bad OHLCV {v['date']}")
            if rows and (v['date']-rows[-1]['date']).days != 1:
                raise ValueError('non-contiguous or duplicate UTC daily bars')
            rows.append(v)
    if len(rows) < 200:
        raise ValueError('needs at least 200 daily bars')
    return rows


def features(rows):
    ema, atr, kal = [], [], []
    state = None
    variance = 1.0
    for i, r in enumerate(rows):
        close = r['close']
        state = close if i == 0 else (close * (2/201) + state * (199/201))
        ema.append(state)
        prev = rows[i-1]['close'] if i else close
        tr = max(r['high']-r['low'], abs(r['high']-prev), abs(r['low']-prev))
        atr.append(tr)
        logp = math.log(close)
        if i == 0:
            kstate = logp
        else:
            predicted = variance + .001  # fixed illustrative q/r, NOT original V1
            gain = predicted / (predicted + .05)
            kstate = kal[-1] + gain * (logp - kal[-1])
            variance = (1-gain)*predicted
        kal.append(kstate)
    return ema, atr, kal


def weekly_weight(rows, i, no_trend=False):
    j = i-1  # i open is executable only after j close
    if j < 180:
        return 0.0
    c = [r['close'] for r in rows]
    score = 1.0 if no_trend else sum(c[j] > c[j-n] for n in (30, 90, 180))/3.0
    returns = [c[k]/c[k-1]-1 for k in range(j-59, j+1)]
    sigma = statistics.stdev(returns) * math.sqrt(365)
    return score * min(.5, .10/sigma if sigma > 0 else .5)


def target_signal(rows, i, strategy, old, feat):
    if i < 1:
        return 0.0
    prev, today = rows[i-1], rows[i]
    ema, trs, kal = feat
    j = i-1
    if strategy == 'cash':
        return 0.0
    if strategy == 'half_monthly':
        return .5 if i == 1 or today['date'].month != prev['date'].month else old
    if strategy in ('v3', 'v3_notrend'):
        if today['date'].weekday() == 0:
            return weekly_weight(rows, i, strategy == 'v3_notrend')
        return old
    if strategy == 'v1_kalman_rebuilt':
        return .5 if j >= 30 and math.log(prev['close']) > kal[j] else 0.0
    if strategy == 'v2_breakout':
        if j < 200:
            return 0.0
        if prev['close'] < min(r['low'] for r in rows[j-20:j]):
            return 0.0
        if prev['close'] > ema[j] and prev['close'] > max(r['high'] for r in rows[j-55:j]):
            return .5
        return old
    if strategy == 'v2_ema_buffer':
        if j < 200:
            return 0.0
        def bound(k):
            tr_avg = statistics.mean(trs[k-19:k+1])
            return ema[k]+tr_avg, ema[k]-tr_avg
        hi0, low0 = bound(j)
        hi1, low1 = bound(j-1)
        if rows[j]['close'] > hi0 and rows[j-1]['close'] > hi1:
            return .5
        if rows[j]['close'] < low0 and rows[j-1]['close'] < low1:
            return 0.0
        return old
    raise ValueError(f'unknown strategy: {strategy}')


def simulate(rows, strategy, protected=False, cost_mult=1):
    if cost_mult <= 0:
        raise ValueError('cost_mult must be positive')
    feat = features(rows)
    cash, btc = START_CASH, 0.0
    peak = START_CASH
    prev_dd = 0.0
    stopped = False
    old_target = 0.0
    trades, equity, total_cost = [], [], 0.0
    cost = (FEE + SLIP)*cost_mult
    for i, day in enumerate(rows):
        op, close = day['open'], day['close']
        previous_target = old_target
        if i:
            old_target = target_signal(rows, i, strategy, old_target, feat)
        target = old_target
        if protected:
            if stopped or prev_dd >= .15:
                stopped = True
                target = 0.0
            elif prev_dd >= .08:
                target = old_target * .5
        value_open = cash + btc*op
        current_w = btc*op/value_open if value_open > 0 else 0
        if strategy.startswith('v3'):
            rebalance = i > 0 and abs(current_w-target) >= .05-1e-12
        elif strategy == 'half_monthly':
            rebalance = i > 0 and (i == 1 or day['date'].month != rows[i-1]['date'].month)
        else:
            rebalance = i > 0 and abs(previous_target-target) > 1e-9
        # A risk cut bypasses the normal 5 percentage point buffer.
        if protected and prev_dd >= .08 and abs(current_w-target) > 1e-9:
            rebalance = True
        if stopped and btc > 1e-14:
            rebalance = True
        if rebalance:
            diff = target*value_open - btc*op
            if diff > 1e-10:
                units = min(diff/(op*(1+cost)), cash/(op*(1+cost)))
                cash -= units*op*(1+cost)
                btc += units
                side = 'BUY'
            else:
                units = min(btc, -diff/op)
                cash += units*op*(1-cost)
                btc -= units
                side = 'SELL'
            expense = units*op*cost
            total_cost += expense
            if units > 1e-14:
                trades.append({'date':day['date'].isoformat(), 'side':side,
                               'btc_qty':round(units,10), 'open_price':op,
                               'friction_usdt':round(expense,8),
                               'target_weight':round(target,6), 'forced_stop':stopped})
        nav = cash + btc*close
        peak = max(peak, nav)
        prev_dd = (peak-nav)/peak
        equity.append({'date':day['date'].isoformat(), 'nav':round(nav,8),
                       'btc_qty':round(btc,10), 'cash_usdt':round(cash,8),
                       'btc_weight':round(btc*close/nav,6) if nav else 0,
                       'drawdown':round(prev_dd,8), 'stopped':stopped})
    return equity, trades, total_cost


def metrics(equity, trades, cost):
    nav = [r['nav'] for r in equity]
    n = len(nav)
    dd = max(r['drawdown'] for r in equity)
    log_r = [math.log(nav[i]/nav[i-1]) for i in range(1,n)]
    vol = statistics.stdev(log_r)*math.sqrt(365) if len(log_r)>1 else 0.0
    sharpe = statistics.mean(log_r)/statistics.stdev(log_r)*math.sqrt(365) if len(log_r)>1 and statistics.stdev(log_r)>0 else None
    cagr = (nav[-1]/nav[0])**(365.25/(n-1))-1 if n>1 and nav[0]>0 else 0
    return {'total_return':nav[-1]/START_CASH-1, 'annualized_return':cagr,
            'max_drawdown':dd, 'annualized_volatility':vol,
            'sharpe_rf_zero':sharpe, 'calmar':cagr/dd if dd else None,
            'mean_btc_weight':statistics.mean(r['btc_weight'] for r in equity),
            'trades':len(trades), 'execution_friction_usdt':cost,
            'final_nav':nav[-1], 'stop_triggered':any(r['stopped'] for r in equity)}


def save_csv(path, data):
    with Path(path).open('w', newline='') as f:
        if data:
            writer = csv.DictWriter(f, fieldnames=list(data[0]))
            writer.writeheader()
            writer.writerows(data)
        else:
            f.write('date,side,btc_qty,open_price,friction_usdt,target_weight,forced_stop\n')


def run(input_csv, out_dir):
    rows = load_daily(input_csv)
    dest = Path(out_dir)
    dest.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256(Path(input_csv).read_bytes()).hexdigest()
    results = {}
    jobs = [('cash',False),('half_monthly',False),('v1_kalman_rebuilt',False),
            ('v2_breakout',False),('v2_ema_buffer',False),
            ('v3',False),('v3',True),('v3_notrend',False),('v3_notrend',True)]
    for name, guard in jobs:
        for multiplier in (1,2):
            tag = f'{name}_{"guarded" if guard else "diagnostic"}_{multiplier}x_cost'
            equity, trades, cost = simulate(rows, name, protected=guard, cost_mult=multiplier)
            save_csv(dest/f'{tag}_equity.csv', equity)
            save_csv(dest/f'{tag}_trades.csv', trades)
            results[tag] = metrics(equity, trades, cost)
    evidence = {'research_only': True, 'live_trading': False,
                'data_start_utc':rows[0]['date'].isoformat(),
                'data_end_utc':rows[-1]['date'].isoformat(),
                'source_sha256':sha, 'python':platform.python_version(),
                'signal_execution':'prior UTC daily close -> next UTC daily open',
                'v3_rebalance':'Monday 00:00 UTC = Monday 08:00 China',
                'initial_usdt':START_CASH, 'one_way_fee':FEE,
                'one_way_slippage':SLIP,
                'warnings':['V1 reconstructed, not historical original',
                            'daily close risk detection / next-open liquidation; 15% drawdown is NOT guaranteed',
                            'No independent out-of-sample certification',
                            'Coin/USDT execution model is simplified, actual liquidity/taxes not modeled'],
                'strategies':results}
    (dest/'summary.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2))
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--input',required=True,help='Validated UTC daily CSV')
    parser.add_argument('--out',required=True)
    a = parser.parse_args()
    r = run(a.input,a.out)
    print(json.dumps({'start':r['data_start_utc'],'end':r['data_end_utc'],
                      'v3_guarded':r['strategies']['v3_guarded_1x_cost']},indent=2))
