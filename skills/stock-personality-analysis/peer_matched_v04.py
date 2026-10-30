"""Peer-matched retrospective event audit, exploratory only; not blind holdout."""
import argparse, hashlib, json
from pathlib import Path
import numpy as np
import pandas as pd

KEYS=[('D_breakout',5),('G_upper_wick',3),('B_breakdown',5),('F_tight_lowvol',5)]

def audit(panel, execution, seed=600031, draws=10000):
    p=panel.copy()
    p['trade_date']=pd.to_datetime(p.trade_date)
    if p.duplicated(['ts_code','trade_date']).any():
        raise ValueError('duplicate stock/date')
    cols=['open','high','low','close','vol']
    if p[cols].isna().any().any():
        raise ValueError('missing OHLCV')
    if not ((p.open>0)&(p.pre_close.gt(0)|p.pre_close.isna())&(p.vol>=0)&
            (p.high>=p[['open','low','close']].max(axis=1))&
            (p.low<=p[['open','high','close']].min(axis=1))).all():
        raise ValueError('invalid OHLCV')
    target=execution['target']
    codes=sorted(p.ts_code.unique())
    peers=[x for x in codes if x!=target]
    if len(peers)<4:
        raise ValueError('need at least four peers')
    opens=p.pivot(index='trade_date',columns='ts_code',values='open').sort_index()
    if opens.isna().any().any():
        raise ValueError('misaligned sessions')
    dates=opens.index
    dmap={v.strftime('%Y-%m-%d'):i for i,v in enumerate(dates)}
    action_dates={}
    unknown_dates={}
    for code,g in p.groupby('ts_code'):
        g=g.sort_values('trade_date')
        ratio=g.close.shift(1)/g.pre_close
        action_dates[code]=set(g.loc[(ratio-1).abs()>.002,'trade_date'])
        unknown_dates[code]=set(g.loc[g.pre_close.isna(),'trade_date'])
    rows={}
    for h in (3,5):
        v=opens.shift(-(h+1))/opens.shift(-1)-1
        v=v.iloc[:-h-1]
        alpha=100*(v[target]-v[peers].mean(axis=1))
        eligible=np.array([dates[i].year==dates[i+h+1].year for i in range(len(v))])
        rows[h]=alpha.loc[eligible]
    rng=np.random.default_rng(seed)
    result=[]
    for pattern,h in KEYS:
        for year in (2025,2026):
            ts=[t for t in execution['stocks'][target][str(h)][pattern]['target_trades']
                if t['year']==year]
            values=[]; months=[]; peer_returns=[]; target_returns=[]
            flagged=[]; unknown=[]; events=[]
            for t in ts:
                day=t['signal_date'];entry=t['entry_date'];end=t['exit_date']
                if any(d not in dmap for d in (day,entry,end)):
                    raise ValueError('unmatched date')
                i=dmap[day]
                if (dates[i+1].strftime('%Y-%m-%d')!=entry or
                    dates[i+1+h].strftime('%Y-%m-%d')!=end):
                    raise ValueError('invalid entry/exit calendar')
                tr=100*(opens.loc[end,target]/opens.loc[entry,target]-1)
                if abs(tr-t['gross_return_pct'])>.0002:
                    raise ValueError('target gross return mismatch')
                pr=100*((opens.loc[end,peers]/opens.loc[entry,peers]-1).mean())
                alpha=float(tr-pr)
                window=dates[i+1:i+2+h]
                action=any(any(d in action_dates[c] for d in window) for c in codes)
                missing=any(any(d in unknown_dates[c] for d in window) for c in codes)
                values.append(alpha);months.append(day[:7]);peer_returns.append(float(pr))
                target_returns.append(float(tr));flagged.append(action);unknown.append(missing)
                events.append({'signal_date':day,'alpha_pp':round(alpha,3),
                    'peer_gross_pct':round(float(pr),3),'ex_action_window':action,
                    'missing_pre_close_in_window':missing})
            obs=float(np.mean(values)) if ts else None
            placebo=None;pvalue=None
            if ts:
                samples=np.zeros(draws)
                for m in months:
                    pool=rows[h].loc[rows[h].index.strftime('%Y-%m')==m].to_numpy()
                    if len(pool)==0:
                        raise ValueError('empty placebo month')
                    samples+=rng.choice(pool,size=draws,replace=True)
                samples/=len(ts)
                placebo=float(np.mean(samples))
                if len(ts)>=5:
                    pvalue=(1+np.count_nonzero(abs(samples-placebo)>=abs(obs-placebo)))/(draws+1)
            result.append({'pattern':pattern,'hold_sessions':h,'year':year,
                'n':len(ts),'mean_target_gross_pct':round(float(np.mean(target_returns)),3) if ts else None,
                'mean_peer_gross_pct':round(float(np.mean(peer_returns)),3) if ts else None,
                'mean_target_minus_peer_pp':round(obs,3) if ts else None,
                'median_target_minus_peer_pp':round(float(np.median(values)),3) if ts else None,
                'underperform_peer_n':int(sum(v<0 for v in values)),
                'months_with_signals':len(set(months)),
                'same_month_placebo_mean_alpha_pp':round(placebo,3) if ts else None,
                'same_month_placebo_two_sided_p_exploratory':round(pvalue,4) if pvalue is not None else None,
                'any_peer_or_target_ex_action_window_n':sum(flagged),
                'missing_pre_close_in_window_n':sum(unknown),
                'events':events})
    return {'scope':'2025-2026 post-selected retrospective peer audit; NOT blind holdout',
        'target':target,'peers':peers,'rows':len(panel),'dates':len(dates),
        'missing_pre_close_rows':int(p.pre_close.isna().sum()),
        'missing_pre_close_dates':sorted(p.loc[p.pre_close.isna(),'trade_date'].dt.strftime('%Y-%m-%d').unique().tolist()),
        'random_seed':seed,'placebo_draws':draws,
        'placebo_method':'same-month random dates with replacement; overlapping windows allowed',
        'results':result,'limitations':['post-selected signals','raw returns not total returns',
            'placebo is descriptive not significance proof','not fill-verified']}

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--daily',required=True)
    parser.add_argument('--execution',required=True)
    parser.add_argument('--out',required=True)
    args=parser.parse_args()
    panel=pd.read_csv(args.daily)
    execution=json.loads(Path(args.execution).read_text(encoding='utf-8'))
    output=audit(panel,execution)
    output['input_sha256']={k:hashlib.sha256(Path(v).read_bytes()).hexdigest()
        for k,v in [('daily',args.daily),('execution',args.execution)]}
    Path(args.out).write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    print('saved',args.out)
