"""Causal version of the archived six-factor rules; no broker interface."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math

import numpy as np
import pandas as pd


def digest(doc):
    return hashlib.sha256(json.dumps(doc, sort_keys=True, allow_nan=False).encode()).hexdigest()


def zones(g, cfg):
    """A base is known only after all three departure bars have closed."""
    o, h, l, c, v = [g[k].to_numpy(dtype=float) for k in ('o', 'h', 'l', 'c', 'volume_shares')]
    n = len(g)
    if n < 20:
        return []
    rng = h - l
    prev = np.roll(c, 1)
    tr = np.maximum(rng, np.maximum(abs(h - prev), abs(l - prev)))
    tr[0] = rng[0]
    atr = pd.Series(tr).rolling(14).mean().to_numpy()
    narrow = (rng <= cfg['k_base'] * atr) & (abs(c - o) <= .5 * rng) & (rng > 0)
    v20 = pd.Series(v).rolling(20).mean().to_numpy()
    out, i = [], 0
    while i < n:
        if not narrow[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and narrow[j + 1]:
            j += 1
        b = max(i, j - 5)
        if j - b + 1 >= 3 and b >= 3 and j + 3 < n:
            incoming = np.polyfit(range(3), c[b-3:b], 1)[0] > 0
            up = np.polyfit(range(3), c[j+1:j+4], 1)[0] > 0
            prox = float(max(np.maximum(o[b:j+1], c[b:j+1]))) if up else float(min(np.minimum(o[b:j+1], c[b:j+1])))
            distal = float(min(l[b:j+1])) if up else float(max(h[b:j+1]))
            body = float(np.mean(abs(c[j+1:j+4]-o[j+1:j+4]) / np.maximum(rng[j+1:j+4], 1e-9)))
            vr = float(np.mean(v[j+1:j+4]) / v20[j]) if np.isfinite(v20[j]) and v20[j] > 0 else 0
            gap = any(l[k] > h[k-1] if up else h[k] < l[k-1] for k in range(j+1,j+4))
            strength = 2 if (body >= .6 and vr >= 1.5) or gap else (1 if body >= .4 and vr >= 1 else 0)
            out.append(dict(kind='demand' if up else 'supply', pattern=('RBR' if incoming else 'DBR') if up else ('RBD' if incoming else 'DBD'),
                            proximal=prox, distal=distal, base_idx=j, confirmed_idx=j+3,
                            base_date=str(g.iloc[j]['trade_date'])[:10], strength=strength))
        i = j + 1
    return out


def in_zone(g, z):
    return g['c'].between(min(z['proximal'], z['distal']), max(z['proximal'], z['distal'])).to_numpy()


def signal_for_stock(g, cfg):
    """Only today's newly entered retest, using information available today."""
    g = g.sort_values('trade_date').reset_index(drop=True)
    if len(g) < 60 or not bool(g.iloc[-1]['eligible']):
        return []
    t = len(g) - 1
    all_zones = zones(g, cfg)
    weights = np.arange(1,31)
    wma = g['c'].rolling(30).apply(lambda x: np.dot(x, weights)/weights.sum(), raw=True)
    if t < 49 or not np.isfinite(wma.iloc[t]) or not np.isfinite(wma.iloc[t-20]):
        return []
    signals = []
    for dz in all_zones:
        known = dz['confirmed_idx']
        if dz['kind'] != 'demand' or known >= t:
            continue
        inside = in_zone(g, dz)
        if not inside[t] or inside[t-1]:
            continue
        # A broken zone is never revived by a subsequent rally.
        if (g['c'].iloc[known+1:t] < dz['distal']).any():
            continue
        touches = sum(inside[k] and not inside[k-1] for k in range(known+1,t))
        if touches >= 2:
            continue
        targets = []
        for sz in all_zones:
            if sz['kind'] != 'supply' or sz['confirmed_idx'] >= t or sz['proximal'] <= dz['proximal']:
                continue
            # Compute supply touches BEFORE choosing the target, never afterwards.
            tested = in_zone(g, sz)[sz['confirmed_idx']+1:t+1].any()
            broken = (g['c'].iloc[sz['confirmed_idx']+1:t+1] > sz['distal']).any()
            if not tested and not broken:
                targets.append(sz)
        if not targets:
            continue
        target = min(targets, key=lambda z:z['proximal'])
        stop = dz['distal'] * (1-cfg['stop_buffer'])
        risk = dz['proximal'] - stop
        rr = (target['proximal'] - dz['proximal']) / risk if risk > 0 else 0
        if rr < cfg['rr_gate']:
            continue
        picture = 2 if wma.iloc[t] > wma.iloc[t-20] else 1
        arrival = int(not any(z['kind']=='supply' and known < z['confirmed_idx'] < t and
                      dz['proximal'] < z['proximal'] <= target['proximal'] for z in all_zones))
        score = dz['strength'] + 2 + picture + (2 if touches==0 else 1) + 1 + arrival
        if score < cfg['min_score']:
            continue
        row = g.iloc[-1]
        factor = float(row['adjustment_factor'])
        s = dict(code=str(row['vendor_symbol']), instrument_id=str(row['instrument_id']), name=str(row['name_zh']),
                 signal_date=str(row['trade_date'])[:10], base_date=dz['base_date'],
                 confirmed_date=str(g.iloc[known]['trade_date'])[:10], score=int(score), pattern=dz['pattern'],
                 entry_limit=round(dz['proximal']/factor, 2), stop=round(stop/factor, 2),
                 target=round(target['proximal']/factor, 2), reference_factor=factor, rr=float(rr),
                 factor_scores=[dz['strength'],2,picture,2 if touches==0 else 1,1,arrival],
                 mode=cfg['mode'], validation='CORRECTED_RULES_NOT_YET_ECONOMICALLY_VALIDATED')
        if 0 < s['stop'] < s['entry_limit'] < s['target']:
            s['event_id'] = digest({k:s[k] for k in ('code','signal_date','base_date','confirmed_date')})[:24]
            signals.append(s)
    return sorted(signals, key=lambda s:(-s['score'], -s['rr'], s['event_id']))[:1]


def fee(value, cfg, sell=False):
    return max(cfg['minimum_commission'], value*cfg['commission']) + value*(cfg['transfer_fee'] + (cfg['sell_stamp_tax'] if sell else 0))


def initial_state(cfg):
    return dict(schema='sdm_paper_state_v1', version=cfg['version'], cash=float(cfg['initial_cash']),
                positions=[], pending=[], events=[], trades=[], last_date=None, equity=[])


def step(state, day, bars, signals, cfg):
    """One close-to-close paper step, repeatable with no duplicate fills.

    Same-day sale proceeds are conservatively unavailable for opening purchases.
    Daily bars cannot prove order sequencing; ambiguous stop+target takes the stop.
    """
    if state['version'] != cfg['version']:
        raise ValueError('state_strategy_version_mismatch')
    state = deepcopy(state)
    if state['last_date'] and day <= state['last_date']:
        if day == state['last_date']:
            return state, []
        raise ValueError('state_cannot_move_backwards')
    alerts, available = [], state['cash']
    blocked_codes = {p['code'] for p in state['positions']}
    for p in list(state['positions']):
        b = bars.get(p['code'])
        if b is None:
            raise ValueError('held_stock_bar_missing')
        p['age'] += 1
        if b['suspended']:
            alerts.append(dict(kind='HOLD_BLOCKED', code=p['code'], reason='SUSPENDED', date=day))
            continue
        ratio = b['adjustment_factor']/p['reference_factor']
        if not math.isclose(ratio, 1, rel_tol=1e-8):
            # No synthetic dividend/share settlement in this first forward ledger.
            raise ValueError('corporate_action_requires_verified_settlement')
        p['mark'] = b['close']
        reason, px = None, None
        if p.get('exit_pending') or b['low'] <= p['stop']:
            reason, px = 'STOP', min(b['open'], p['stop'])
        elif b['high'] >= p['target']:
            reason, px = 'TARGET', max(b['open'], p['target'])
        elif p['age'] >= cfg['max_hold_sessions']:
            reason, px = 'TIMEOUT', b['close']
        if not reason:
            continue
        if b['one_word_down']:
            p['exit_pending'] = True
            alerts.append(dict(kind='SELL_BLOCKED', code=p['code'], reason='LIMIT_DOWN', date=day))
            continue
        px = min(b['high'], max(b['low'], px*(1-cfg['slippage'])))
        if px <= b['limit_down'] or px > b['limit_up']:
            p['exit_pending'] = True
            alerts.append(dict(kind='SELL_BLOCKED', code=p['code'], reason='PRICE_LIMIT', date=day))
            continue
        value = p['quantity']*px
        net = value-fee(value,cfg,True)
        state['cash'] += net
        state['positions'].remove(p)
        fill = dict(kind='PAPER_SELL',code=p['code'],event_id=p['event_id'],date=day,quantity=p['quantity'],price=px,
                    reason=reason,net_pnl=net-p['cost'],mode=cfg['mode'])
        state['trades'].append(fill)
        alerts.append(fill)
    for s in sorted(state['pending'],key=lambda s:(s['signal_date'],-s['score'],s['code'])):
        reason = None
        b = bars.get(s['code'])
        if len(state['positions']) >= cfg['slots']:
            reason='NO_SLOT'
        elif s['code'] in blocked_codes or any(p['code']==s['code'] for p in state['positions']):
            reason='DUPLICATE_STOCK'
        elif b is None or b['suspended'] or not b['eligible']:
            reason='MISSING_OR_INELIGIBLE'
        elif not math.isclose(b['adjustment_factor'],s['reference_factor'],rel_tol=1e-8):
            reason='CORPORATE_ACTION'
        elif b['one_word_up'] or b['one_word_down']:
            reason='LOCKED_LIMIT'
        elif b['open'] <= s['stop'] or b['open'] > s['entry_limit']:
            reason='OPEN_OUTSIDE_ENTRY_LIMIT'
        if reason is None:
            px = b['open']*(1+cfg['slippage'])
            # Fill only at an observed opening price with all-in cost inside limit.
            if px > s['entry_limit'] or px >= b['limit_up'] or px <= b['limit_down']:
                reason='SLIPPAGE_OR_PRICE_LIMIT'
            else:
                budget = min(cfg['slot_budget'],available-cfg['cash_buffer'])
                q = max(0,int(budget/px)//cfg['lot_size']*cfg['lot_size'])
                while q and q*px+fee(q*px,cfg) > budget:
                    q -= cfg['lot_size']
                if not q:
                    reason='NO_AFFORDABLE_LOT'
                else:
                    cost=q*px+fee(q*px,cfg)
                    state['cash']-=cost
                    available-=cost
                    p=dict(s,quantity=q,cost=cost,entry_date=day,entry_price=px,mark=b['close'],age=0)
                    state['positions'].append(p)
                    fill=dict(kind='PAPER_BUY',code=s['code'],event_id=s['event_id'],date=day,quantity=q,price=px,mode=cfg['mode'])
                    state['trades'].append(fill)
                    alerts.append(fill)
        if reason:
            alerts.append(dict(kind='SKIP',code=s['code'],event_id=s['event_id'],reason=reason,date=day))
    known={s['event_id'] for s in state['events']}
    new=[s for s in signals if s['event_id'] not in known]
    state['events'].extend(new)
    state['pending']=[s for s in new if s.get('action_status','PENDING')=='PENDING']
    for s in new:
        alerts.append(dict(kind='BUY_WATCH' if s.get('action_status','PENDING')=='PENDING' else 'EXPIRED_SIGNAL',code=s['code'],event_id=s['event_id'],date=day,
                           entry_limit=s['entry_limit'],stop=s['stop'],target=s['target'],score=s['score'],
                           reason='NEXT_SESSION_CONDITIONAL_PAPER_SIGNAL',mode=cfg['mode']))
    for p in state['positions']:
        alerts.append(dict(kind='HOLD_WATCH',code=p['code'],date=day,stop=p['stop'],target=p['target'],
                           age=p['age'],mode=cfg['mode']))
    state['last_date']=day
    state['equity'].append(dict(date=day,cash=state['cash'],equity=state['cash']+sum(p['quantity']*p['mark'] for p in state['positions'])))
    if state['cash'] < cfg['cash_buffer']-.01 or len(state['positions']) > cfg['slots']:
        raise ValueError('portfolio_invariant_failed')
    return state,alerts
