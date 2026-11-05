"""Read existing Gold only. Unknown state or incomplete prices fail closed."""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


def sha(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1048576),b''):
            h.update(b)
    return h.hexdigest()


def partition(root, name, start):
    paths=[p for p in sorted((root/name).glob('year=*/month=*/data.parquet')) if int(p.parts[-3][5:]) >= int(start[:4])]
    if not paths:
        raise ValueError('missing_partition_'+name)
    df=pd.concat([pd.read_parquet(p) for p in paths],ignore_index=True)
    df['trade_date']=pd.to_datetime(df['trade_date']).astype('datetime64[ns]')
    df['instrument_id']=df['instrument_id'].astype(str)
    if df.duplicated(['instrument_id','trade_date']).any():
        raise ValueError('duplicate_keys_'+name)
    return df


def load_gold(root, cfg, now=None):
    root=Path(root)
    manifest=json.loads((root/'manifest.json').read_text())
    if any(manifest.get('validation',{}).get(k) is not True for k in ('primary_keys_unique','ohlc_valid')):
        raise ValueError('gold_manifest_not_validated')
    for name,meta in manifest['files'].items():
        if '/' in name or '\\' in name or name.startswith('.'):
            raise ValueError('unsafe_manifest_path')
        if meta.get('sha256') and sha(root/name)!=meta['sha256']:
            raise ValueError('gold_manifest_hash_mismatch')
    calendar=pd.read_parquet(root/'trading_calendar.parquet')
    calendar['calendar_date']=pd.to_datetime(calendar['calendar_date']).astype('datetime64[ns]')
    calendar=calendar[calendar['exchange']=='SSE'].sort_values('calendar_date')
    now=now or datetime.now(ZoneInfo('Asia/Shanghai'))
    cutoff=pd.Timestamp(now.date() if now.hour>=17 else now.date()-timedelta(days=1))
    if calendar.empty or calendar['calendar_date'].max()<pd.Timestamp(now.date()):
        raise ValueError('calendar_does_not_cover_today')
    opens=calendar.loc[calendar['is_open'].eq(True),'calendar_date'].drop_duplicates().sort_values()
    expected=opens[opens<=cutoff].max()
    asof=pd.Timestamp(manifest['as_of_date'])
    if asof!=expected:
        raise ValueError('stale_or_future_gold_'+str(asof.date())+'_expected_'+str(expected.date()))
    instruments=pd.read_parquet(root/'instruments.parquet')
    names=pd.read_parquet(root/'name_history.parquet')
    if instruments['instrument_id'].duplicated().any():
        raise ValueError('duplicate_instruments')
    names['effective_date']=pd.to_datetime(names['effective_date']).astype('datetime64[ns]')
    names['instrument_id']=names['instrument_id'].astype(str)
    instruments['instrument_id']=instruments['instrument_id'].astype(str)
    if names['effective_date'].isna().any() or names.duplicated(['instrument_id','effective_date']).any():
        raise ValueError('invalid_name_history')
    prices=partition(root,'daily_prices',cfg['history_start'])
    prices=prices[(prices['trade_date']>=cfg['history_start']) & (prices['trade_date']<=asof)].copy()
    adj=partition(root,'adjustment_factors',cfg['history_start'])
    limits=partition(root,'daily_limits',cfg['history_start'])
    # Source Gold uses vendor-neutral names; allow the documented TuShare aliases.
    limits=limits.rename(columns={'up_limit':'limit_up','down_limit':'limit_down',
                                   'limit_up_price':'limit_up','limit_down_price':'limit_down'})
    if not {'limit_up','limit_down'}.issubset(limits.columns):
        raise ValueError('unsupported_daily_limits_schema_'+','.join(limits.columns))
    wanted=['instrument_id','trade_date','adjustment_factor']
    prices=prices.merge(adj[wanted],on=['instrument_id','trade_date'],how='left',validate='one_to_one')
    prices=prices.merge(limits[['instrument_id','trade_date','limit_up','limit_down']],on=['instrument_id','trade_date'],how='left',validate='one_to_one')
    meta=['instrument_id','board','exchange','list_date','delist_date','name_zh']
    # vendor_symbol already exists in daily_prices on the current Gold schema.
    if 'vendor_symbol' not in prices:
        meta.append('vendor_symbol')
    prices=prices.merge(instruments[meta],on='instrument_id',how='left',validate='many_to_one')
    prices['list_date']=pd.to_datetime(prices['list_date'])
    prices['delist_date']=pd.to_datetime(prices['delist_date'])
    prices=prices.sort_values(['trade_date','instrument_id']).reset_index(drop=True)
    hist=names[names['effective_date']<=asof].sort_values(['effective_date','instrument_id'])
    st=pd.merge_asof(prices[['instrument_id','trade_date']],hist[['instrument_id','effective_date','is_st']],
                     by='instrument_id',left_on='trade_date',right_on='effective_date',direction='backward')
    prices['st_known']=st['is_st'].notna()
    prices['is_st']=st['is_st'].eq(True)
    prices=prices.sort_values(['instrument_id','trade_date']).reset_index(drop=True)
    for raw,short in [('open','o'),('high','h'),('low','l'),('close','c')]:
        prices[short]=prices[raw]*prices['adjustment_factor']
    numeric=['open','high','low','close','turnover_cny','volume_shares','adjustment_factor']
    valid=np.isfinite(prices[numeric].to_numpy(dtype=float)).all(axis=1)
    valid &= (prices[['open','high','low','close','adjustment_factor']]>0).all(axis=1)
    valid &= prices['high'].ge(prices[['open','close','low']].max(axis=1)) & prices['low'].le(prices[['open','close']].min(axis=1))
    valid &= prices['turnover_cny'].ge(0) & prices['volume_shares'].ge(0)
    if not valid.all():
        raise ValueError('invalid_price_or_adjustment_rows')
    prices['amt20']=prices.groupby('instrument_id')['turnover_cny'].transform(lambda s:s.rolling(20).mean())
    open_values=opens.to_numpy()
    prices['listing_sessions']=[int(np.searchsorted(open_values,d.to_datetime64(),side='right')-np.searchsorted(open_values,l.to_datetime64(),side='left')) if pd.notna(l) else 0 for d,l in zip(prices['trade_date'],prices['list_date'])]
    common=prices['board'].eq('MAIN_BOARD') & prices['exchange'].isin(['SSE','SZSE']) & prices['st_known'] & ~prices['is_st']
    common &= prices['listing_sessions'].ge(cfg['min_listing_sessions']) & prices['list_date'].le(prices['trade_date'])
    common &= prices['delist_date'].isna() | prices['delist_date'].gt(prices['trade_date'])
    prices['suspended']=prices['volume_shares'].le(0)
    prices['one_word_up']=prices['high'].eq(prices['low']) & prices['close'].ge(prices['limit_up']-.005)
    prices['one_word_down']=prices['high'].eq(prices['low']) & prices['close'].le(prices['limit_down']+.005)
    prices['eligible']=common & prices['amt20'].ge(cfg['min_turnover_cny']) & ~prices['suspended'] & ~prices['one_word_up'] & ~prices['one_word_down']
    # Opening paper fills use only the prior session's liquidity, not today's volume.
    prices['entry_eligible']=common & prices.groupby('instrument_id')['amt20'].shift(1).ge(cfg['min_turnover_cny'])
    prices['eligible'] &= prices['limit_up'].notna() & prices['limit_down'].notna()
    prices['entry_eligible'] &= prices['limit_up'].notna() & prices['limit_down'].notna()
    prices=prices[prices['board']=='MAIN_BOARD'].copy()
    latest=prices[prices['trade_date']==asof]
    if len(latest)<500 or len(latest[latest['eligible']])<cfg['slots']:
        raise ValueError('latest_main_board_coverage_too_low')
    inventory={str(p.relative_to(root)):dict(sha256=sha(p),bytes=p.stat().st_size) for p in sorted(root.rglob('*')) if p.is_file()}
    return prices,opens,dict(as_of=str(asof.date()),expected_as_of=str(expected.date()),
                           rows=len(prices),latest_rows=len(latest),eligible_rows=int(latest['eligible'].sum()),
                           status='PASSED_FOR_RESEARCH',files=inventory,
                           limitation='Gold effective_date is used; original announcement timestamps are not independently certified.')


def day_bars(prices, day, required=()):
    today=prices[prices['trade_date']==pd.Timestamp(day)]
    out={}
    for row in today.to_dict('records'):
        row['eligible']=bool(row['entry_eligible'])
        out[str(row['vendor_symbol'])]=row
    missing=set(required)-set(out)
    if missing:
        # A missing holding is never silently valued or closed at a made-up price.
        raise ValueError('held_bar_missing_'+','.join(sorted(missing)))
    return out


def scan(prices, day, cfg):
    from .engine import signal_for_stock
    df=prices[prices['trade_date']<=pd.Timestamp(day)]
    today=df[df['trade_date']==pd.Timestamp(day)]
    selected=today[today['eligible']].sort_values(['amt20','vendor_symbol'],ascending=[False,True]).head(cfg['top_n'])
    out=[]
    for _,g in df[df['instrument_id'].isin(selected['instrument_id'])].groupby('instrument_id',sort=True):
        if g.iloc[-1]['trade_date']==pd.Timestamp(day):
            out.extend(signal_for_stock(g,cfg))
    return sorted(out,key=lambda s:(-s['score'],-s['rr'],s['code'],s['event_id']))


def track_events(events,prices,opens,asof):
    result=[]
    for e in events:
        future=opens[opens>pd.Timestamp(e['signal_date'])]
        g=prices[prices['vendor_symbol']==e['code']].set_index('trade_date')
        row=dict(event_id=e['event_id'],code=e['code'],signal_date=e['signal_date'],kind='EVENT_RETURN_NOT_ACCOUNT_RETURN')
        for n in (1,3,5,20):
            key=str(n)+'_session'
            if len(future)<n or future.iloc[n-1]>pd.Timestamp(asof):
                row[key]=dict(status='NOT_MATURED',return_pct=None)
            elif future.iloc[0] not in g.index or future.iloc[n-1] not in g.index:
                row[key]=dict(status='MISSING_PRICE',return_pct=None)
            else:
                start,end=g.loc[future.iloc[0]],g.loc[future.iloc[n-1]]
                row[key]=dict(status='OBSERVED_NOT_EXECUTABLE_PNL',return_pct=float((end['c']/start['o']-1)*100))
        result.append(row)
    return result
