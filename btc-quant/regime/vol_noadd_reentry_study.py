#!/usr/bin/env python3
"""BTC VOL risk-on re-entry study: research only, no live orders.

Replays existing 2026-10-17 VOL_BASE/VOL_DD5 daily simulation exactly, then tests
pre-registered no-add and staged-re-entry ablations on the SAME 1d UTC OHLCV.
No hyper-parameter tuning, no intraday execution claims, no V3 modifications.
Inputs and fixed ablation rules in RESEARCH_PROTOCOL.md.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import math
import statistics
from collections import defaultdict
from datetime import date
from pathlib import Path

STUDY = 'BTC_VOL_REDUCED_NOADD_REENTRY_20261017'
NAMES = ('VOL_BASE','VOL_DD5','VOL_NOADD_REDUCED','VOL_DD5_NOADD',
         'VOL_DD5_STAGE_RECOVERY','VOL_NOADD_STAGE_RECOVERY',
         'VOL_NOADD_GLOBAL_REENTRY','VOL_DD5_GLOBAL_REENTRY')
DD5 = {'VOL_DD5','VOL_DD5_NOADD','VOL_DD5_STAGE_RECOVERY','VOL_NOADD_STAGE_RECOVERY','VOL_DD5_GLOBAL_REENTRY'}
STAGED = {'VOL_DD5_STAGE_RECOVERY','VOL_NOADD_STAGE_RECOVERY','VOL_DD5_GLOBAL_REENTRY'}
NOADD = {'VOL_NOADD_REDUCED','VOL_DD5_NOADD','VOL_NOADD_STAGE_RECOVERY','VOL_NOADD_GLOBAL_REENTRY','VOL_DD5_GLOBAL_REENTRY'}
GLOBAL_REENTRY = {'VOL_NOADD_GLOBAL_REENTRY','VOL_DD5_GLOBAL_REENTRY'}
INIT = 1000.0
ONE_WAY = .0015


def load_rows(path):
    with open(path,encoding='utf8',newline='') as fh:
        rows = list(csv.DictReader(fh))
    out=[]
    for row in rows:
        out.append({'date':date.fromisoformat(row['date']),
                    'open':float(row['open']),'high':float(row['high']),
                    'low':float(row['low']),'close':float(row['close']),
                    'volume':float(row['volume'])})
    if len(out)<1001:
        raise ValueError('insufficient 180+730+90 BTC complete candles')
    from datetime import timedelta
    for a,b in zip(out,out[1:]):
        if (b['date']-a['date'])!=timedelta(days=1):
            raise ValueError(f'gap or duplicated BTC UTC candle {a["date"]}/{b["date"]}')
        if min(a['open'],a['high'],a['low'],a['close'])<=0 or a['high']<a['low']:
            raise ValueError('invalid source BTC candle')
    return out


def annual_vol(x):
    return statistics.stdev(x)*math.sqrt(365) if len(x)>1 else 0.0


def features(rows):
    close=[r['close'] for r in rows]
    ret=[0.0]+[math.log(close[i]/close[i-1]) for i in range(1,len(rows))]
    s60,s20,vol=[],[],[]
    for i in range(len(rows)):
        j=i-1
        if j<180:
            s60.append(0.0);s20.append(0.0);vol.append(0.0);continue
        x=annual_vol(ret[j-59:j+1]);y=annual_vol(ret[j-19:j+1])
        w=min(.5,.10/x) if x>0 and math.isfinite(x) else 0.0
        s60.append(x);s20.append(y);vol.append(w)
    return {'close':close,'ret':ret,'sigma60':s60,'sigma20':s20,'vol_weight':vol}


def folds_for(rows):
    folds=[]
    for start in range(180+730,len(rows),90):
        end=min(len(rows),start+90)
        folds.append({'fold':len(folds)+1,'start_i':start,'end_i':end,
                      'train_start':rows[start-730]['date'].isoformat(),
                      'train_end':rows[start-1]['date'].isoformat(),
                      'test_start':rows[start]['date'].isoformat(),
                      'test_end':rows[end-1]['date'].isoformat()})
    if not folds:
        raise ValueError('no 730/90 folds')
    return folds


def recovery_signal(rows,f,i):
    """Only fully completed prior UTC candles; use on next Monday open."""
    if i<181 or f['sigma60'][i]<=0:
        return False
    prior=rows[i-1]['close']
    sma60=sum(r['close'] for r in rows[i-60:i])/60
    past30=rows[i-31]['close']
    ratio=f['sigma20'][i]/f['sigma60'][i]
    return prior>sma60 and prior>past30 and ratio<=1.40


def simulate(rows,folds,f,name,cost_multiple):
    if name not in NAMES or cost_multiple not in (1,2):
        raise ValueError('invalid variant or costs')
    fee=ONE_WAY*cost_multiple
    fold_at={i:fold for fold in folds for i in range(fold['start_i'],fold['end_i'])}
    cash,btc,highwater,risk=INIT,0.0,INIT,'NORMAL'
    soft_dd=False
    confirm_weeks,stage=0,0
    global_weeks,global_stage=0,0
    trades,equity,fold_stats,events=[],[],[],[]
    previous_fold=None
    for i in range(folds[0]['start_i'],len(rows)):
        fold=fold_at.get(i)
        if fold is None:break
        prev=rows[i-1];day=rows[i];asof=prev['date'].isoformat();now=day['date'].isoformat()
        px=day['open'];monday=day['date'].weekday()==0
        if previous_fold!=fold['fold']:
            if fold_stats:
                fold_stats[-1]['end_nav']=equity[-1]['nav']
            fold_stats.append({'fold':fold['fold'],'test_start':fold['test_start'],
                               'test_end':fold['test_end'],'train_end':fold['train_end'],
                               'start_nav':equity[-1]['nav'] if equity else INIT})
            previous_fold=fold['fold']
        def nav(price):
            return cash+btc*price
        def execute(side,quantity,reason,override=False):
            nonlocal cash,btc
            if quantity<=1e-13:return
            if side=='BUY':
                quantity=min(quantity,cash/(px*(1+fee)))
                if quantity<=1e-13:return
                cash-=quantity*px*(1+fee)
                btc+=quantity
            else:
                quantity=min(quantity,btc)
                if quantity<=1e-13:return
                cash+=quantity*px*(1-fee)
                btc-=quantity
                if btc<1e-13:btc=0.0
            trades.append({'signal_asof':asof,'fill_date':now,'side':side,
                           'btc_qty':quantity,'price':px,'friction_usdt':quantity*px*fee,
                           'reason':reason,'variant':name,'cost_multiple':cost_multiple,
                           'emergency':override,'fold':fold['fold']})
        observed_nav=nav(prev['close'])
        highwater=max(highwater,observed_nav)
        dd=(highwater-observed_nav)/highwater if highwater>0 else 0.0
        reduced_now=False
        if risk!='HALTED':
            if dd>=.15:
                risk='HALTED';execute('SELL',btc,'HALT_15PCT',True)
                events.append({'signal_asof':asof,'execution_date':now,'event':'HALT','drawdown':dd})
            elif risk=='NORMAL' and dd>=.08:
                risk='REDUCED';reduced_now=True
                execute('SELL',btc*.5,'REDUCE_8PCT',True)
                events.append({'signal_asof':asof,'execution_date':now,'event':'REDUCE','drawdown':dd})
            elif risk=='REDUCED' and monday and dd<=.04:
                risk='NORMAL'
                events.append({'signal_asof':asof,'execution_date':now,'event':'RECOVER_GLOBAL','drawdown':dd})
        if name in DD5 and risk!='HALTED':
            if not soft_dd and dd>=.05:
                soft_dd=True
                confirm_weeks=0;stage=0
                events.append({'signal_asof':asof,'execution_date':now,'event':'SOFT_DD_ENTER','drawdown':dd})
            elif soft_dd and monday and dd<=.03:
                soft_dd=False;confirm_weeks=0;stage=0
                events.append({'signal_asof':asof,'execution_date':now,'event':'SOFT_DD_RECOVER','drawdown':dd})
        if name in STAGED and soft_dd and risk!='HALTED' and monday:
            old_stage=stage
            if recovery_signal(rows,f,i):
                confirm_weeks+=1
                stage=2 if confirm_weeks>=4 else 1 if confirm_weeks>=2 else 0
            else:
                confirm_weeks=0;stage=0
            if stage!=old_stage:
                events.append({'signal_asof':asof,'execution_date':now,
                               'event':'STAGE_'+str(old_stage)+'_TO_'+str(stage),
                               'drawdown':dd,'confirmed_mondays':confirm_weeks,
                               'stage':stage})
        if name in GLOBAL_REENTRY and risk=='REDUCED' and monday:
            previous_global=global_stage
            if recovery_signal(rows,f,i):
                global_weeks+=1
                global_stage=2 if global_weeks>=4 else 1 if global_weeks>=2 else 0
            else:
                global_weeks=0;global_stage=0
            if previous_global!=global_stage:
                events.append({'signal_asof':asof,'execution_date':now,
                               'event':'GLOBAL_STAGE_'+str(previous_global)+'_TO_'+str(global_stage),
                               'drawdown':dd,'confirmation_weeks':global_weeks})
        elif risk!='REDUCED':
            global_weeks=0;global_stage=0
        if risk=='HALTED':
            if btc:execute('SELL',btc,'HALT_REMAINDER',True)
        else:
            if name in DD5 and soft_dd and btc:
                soft_cap=({0:.25,1:.35,2:.50}[stage] if name in STAGED else .25)
                if btc*px/nav(px)>soft_cap+1e-12:
                    execute('SELL',(btc*px-soft_cap*nav(px))/px,'SOFT_DD_CAP',True)
            if monday and not reduced_now:
                target=f['vol_weight'][i]
                if name in DD5 and soft_dd:
                    target=min(target,({0:.25,1:.35,2:.50}[stage] if name in STAGED else .25))
                if risk=='REDUCED':
                    factor=({0:.5,1:.75,2:1.0}[global_stage] if name in GLOBAL_REENTRY else .5)
                    target=min(target*factor,.25)
                open_nav=nav(px)
                diff=target*open_nav-btc*px
                current_weight=btc*px/open_nav if open_nav>0 else 0.0
                if abs(current_weight-target)>=.05-1e-12 or (target==0 and btc>1e-13):
                    if diff>0 and name in NOADD and risk=='REDUCED' and dd>=.08 and not ((name=='VOL_NOADD_STAGE_RECOVERY' and stage>=1) or (name in GLOBAL_REENTRY and global_stage>=1)):
                        events.append({'signal_asof':asof,'execution_date':now,
                                       'event':'VETO_BUY_DEEP_REDUCED','drawdown':dd,
                                       'target_weight':target,'prior_weight':current_weight,
                                       'btc_close':prev['close'],'recovery_stage':stage,
                                       'would_buy_usdt':diff})
                    else:
                        qty=diff/(px*(1+fee)) if diff>0 else -diff/px
                        execute('BUY' if diff>0 else 'SELL',abs(qty),'WEEKLY_VOL_REBALANCE')
        endnav=nav(day['close']);highwater=max(highwater,endnav)
        enddd=(highwater-endnav)/highwater if highwater>0 else 0.0
        equity.append({'date':now,'nav':endnav,'cash_usdt':cash,
                       'btc_qty':btc,'btc_weight':btc*day['close']/endnav if endnav>0 else 0,
                       'drawdown':enddd,'high_water_mark':highwater,
                       'risk_state':risk,'soft_drawdown_guard':soft_dd,
                       'reentry_stage':stage,'confirmed_positive_mondays':confirm_weeks,
                       'global_reentry_stage':global_stage,'global_confirmation_weeks':global_weeks,
                       'fold':fold['fold'],'variant':name,'cost_multiple':cost_multiple})
    if fold_stats:fold_stats[-1]['end_nav']=equity[-1]['nav']
    for fold in fold_stats:
        fold['net_return']=fold['end_nav']/fold['start_nav']-1
        group=[r for r in equity if r['fold']==fold['fold']]
        fold['max_global_dd_in_fold']=max(x['drawdown'] for x in group)
        fold['friction_usdt']=sum(t['friction_usdt'] for t in trades if t['fold']==fold['fold'])
    navs=[x['nav'] for x in equity]
    rets=[math.log(navs[k]/navs[k-1]) for k in range(1,len(navs))]
    v=annual_vol(rets)
    cagr=(navs[-1]/INIT)**(365.25/len(navs))-1
    dds=[x['drawdown'] for x in equity]
    summary={'variant':name,'cost_multiple':cost_multiple,'initial_cash':INIT,
             'test_start':equity[0]['date'],'test_end':equity[-1]['date'],
             'ending_nav':navs[-1],'net_return':navs[-1]/INIT-1,
             'cagr':cagr,'max_drawdown':max(dds),
             'annualized_volatility':v,
             'sharpe_rf_zero':(statistics.mean(rets)*365/v) if v>0 else None,
             'trades':len(trades),'total_friction_usdt':sum(x['friction_usdt'] for x in trades),
             'mean_btc_weight':statistics.mean(x['btc_weight'] for x in equity),
             'halted':any(x['risk_state']=='HALTED' for x in equity),
             'buy_veto_events':sum(x['event']=='VETO_BUY_DEEP_REDUCED' for x in events),
             'stage_up_events':sum('STAGE_' in x['event'] and x['event'].endswith(('_TO_1','_TO_2')) for x in events),
             'max_reentry_stage':max(x['reentry_stage'] for x in equity),
             'max_global_reentry_stage':max(x['global_reentry_stage'] for x in equity),
             'live_trading':False}
    return summary,equity,trades,events,fold_stats


def write_csv(path,rows,fallback):
    fields=list(dict.fromkeys(k for r in rows for k in r)) or fallback
    with path.open('w',newline='',encoding='utf8') as fh:
        writer=csv.DictWriter(fh,fieldnames=fields)
        writer.writeheader();writer.writerows(rows)


def verify_baseline(name,curve,trades,reference_dir,cost_multiple):
    prefix=name.lower()+'_'+str(cost_multiple)+'x'
    # GitHub Actions can compare against current freshly generated 
    # VOL_STATIC reference; offline local validation can compare archived run IDs.
    filepath=reference_dir/(prefix+'__equity.csv')
    tpath=reference_dir/(prefix+'__trades.csv')
    if not (filepath.is_file() and tpath.is_file()):
        prefix='btc_router_2026-10-16__run_38994013829__VOL_RISK__'+prefix
        filepath=reference_dir/(prefix+'__equity.csv')
        tpath=reference_dir/(prefix+'__trades.csv')
    if not filepath.is_file() or not tpath.is_file():
        raise ValueError('missing current or immutable reference equity/trade ledger for '+name)
    with filepath.open('r',encoding='utf8',newline='') as f:old=list(csv.DictReader(f))
    with tpath.open('r',encoding='utf8',newline='') as f:oldtrades=list(csv.DictReader(f))
    if len(old)!=len(curve):
        raise AssertionError(f'{name}: different evaluation-day counts')
    m=max(abs(float(a['nav'])-b['nav']) for a,b in zip(old,curve))
    if m>1e-6 or len(oldtrades)!=len(trades):
        raise AssertionError(f'{name}: baseline drift m={m}, original trades={len(oldtrades)}, new={len(trades)}')
    for before,after in zip(oldtrades,trades):
        if before['fill_date']!=after['fill_date'] or before['side']!=after['side'] or abs(float(before['btc_qty'])-after['btc_qty'])>1e-10:
            raise AssertionError(f'{name}: baseline trades not the same: {before} vs {after}')
    return {'pass':True,'max_daily_nav_difference_usdt':m,'old_trades':len(oldtrades),
            'replayed_trades':len(trades),'days':len(curve)}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--input',required=True)
    ap.add_argument('--out',required=True)
    ap.add_argument('--reference-dir',default='')
    args=ap.parse_args()
    rows=load_rows(args.input)
    f=features(rows);folds=folds_for(rows)
    output=Path(args.out);output.mkdir(parents=True,exist_ok=True)
    results,curves,alltrades,events,allfolds={},{},{},{},{}
    parity={}
    for mult in (1,2):
        for name in NAMES:
            result,curve,trades,event,fold_metric=simulate(rows,folds,f,name,mult)
            key=name.lower()+'_'+str(mult)+'x'
            results[key]=result;curves[key]=curve;alltrades[key]=trades;events[key]=event;allfolds[key]=fold_metric
            write_csv(output/(key+'__equity.csv'),curve,['date','nav'])
            write_csv(output/(key+'__trades.csv'),trades,['signal_asof','fill_date'])
            write_csv(output/(key+'__events.csv'),event,['signal_asof','event'])
            write_csv(output/(key+'__folds.csv'),fold_metric,['fold'])
            if args.reference_dir and name in ('VOL_BASE','VOL_DD5'):
                parity[name+'_'+str(mult)+'x']=verify_baseline(name,curve,trades,Path(args.reference_dir),mult)
    write_csv(output/'fold_boundaries.csv',folds,['fold'])
    comparisons={}
    for mult in (1,2):
        b=results['vol_base_'+str(mult)+'x'];g=results['vol_dd5_'+str(mult)+'x']
        for name in NAMES:
            k=name.lower()+'_'+str(mult)+'x';x=results[k]
            keep=x['net_return']/b['net_return'] if b['net_return']>0 else None
            reduce=1-x['max_drawdown']/b['max_drawdown'] if b['max_drawdown']>0 else None
            comparisons[k]={'profit_retention_vs_vol_base':keep,
                            'relative_dd_reduction_vs_vol_base':reduce,
                            'profit_vs_dd5':x['net_return']-g['net_return'],
                            'dd_vs_dd5':x['max_drawdown']-g['max_drawdown'],
                            'exploratory_gate_85pct_profit_and_25pct_dd_both': bool(keep is not None and reduce is not None and keep>=.85 and reduce>=.25 and x['net_return']>0)}
    # Fixed prior-observed problem event, not a tuned test.
    date_samples={}
    for day in ('2025-10-06','2025-11-04','2025-11-20','2026-05-31','2026-06-01','2026-06-30','2026-07-27','2026-08-10','2026-08-24','2026-10-16'):
        date_samples[day]={}
        for name in NAMES:
            k=name.lower()+'_1x'
            row=next((x for x in curves[k] if x['date']==day),None)
            date_samples[day][name]={'nav':row['nav'],'weight':row['btc_weight'],'risk':row['risk_state'],
                                      'soft':row['soft_drawdown_guard'],'stage':row['reentry_stage'],
                                      'global_stage':row['global_reentry_stage']} if row else None
    report={'study':STUDY,'research_only':True,'live_trading':False,
            'input_sha256':hashlib.sha256(Path(args.input).read_bytes()).hexdigest(),
            'asof':rows[-1]['date'].isoformat(),'test_start':folds[0]['test_start'],
            'test_end':folds[-1]['test_end'],'folds':len(folds),
            'noadd_rule':'Global REDUCED and prior completed close DD>=8%: veto new BTC buys from weekly vol target',
            'staged_reentry_rule':'After soft DD5, two consecutive positive prior-close Monday confirmations (close>SMA60, 30d price return>0, sigma20/sigma60<=1.4) -> soft cap35%, four -> soft cap50%; any failed Monday reverts to 25%; GLOBAL 8/4/15 unchanged',
            'combined_rule':'Deep DD buy veto lifted only after two consecutively positive completed-candle Mondays. Weak confirmation returns to no-add and original reduced exposure.',
            'global_recovery_rule':'Additional research candidate ONLY: remain globally REDUCED, but allow 75% of base VOL target with 25% absolute cap after two Mondays and 100% target with 25% absolute cap after four; reset to 50% after weak Monday. Original GLOBAL stop and <=4pct recovery unaffected.',
            'original_5pct_guard':'DD>=5% -> soft cap25%; clears only at Monday DD<=3%; staged variants keep soft flag but relax cap on confirmation',
            'note':'2022-2026 old historical data already studied; not independent prospective OOS. May only claim conditional retrospective outcomes; no live execution.',
            'baseline_parity':parity,'results':results,'vs_vol_base':comparisons,'snapshots':date_samples}
    (output/'noadd_reentry_summary.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf8')
    print(json.dumps({'baseline_parity':parity,'results':{k:dict(net_return=v['net_return'],max_drawdown=v['max_drawdown'],veto=v['buy_veto_events'],stage=v['max_reentry_stage']) for k,v in results.items()},'gate':{k:v['exploratory_gate_85pct_profit_and_25pct_dd_both'] for k,v in comparisons.items()}},ensure_ascii=False,indent=2))

if __name__=='__main__':
    main()