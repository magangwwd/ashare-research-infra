"""5-case 15m Baostock evidence probe, NO orders/backtesting/Drive secrets.

AUDIT SAMPLING ONLY. Five frozen V2-LOOSE historical cases were picked *after*
knowing their outcomes. They do not constitute unbiased strategy performance.
15m OHLC bars can never prove fillability, auction price or intrabar sequencing.
"""
from __future__ import annotations
import argparse
import csv
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path

EXPECTED = ('09:45','10:00','10:15','10:30','10:45','11:00','11:15','11:30',
            '13:15','13:30','13:45','14:00','14:15','14:30','14:45','15:00')
CASES = (
    ('002688.SZ','2026-01-27','2026-01-30','STOP_INTRADAY_ASSUMED'),
    ('600115.SH','2025-10-16','2025-10-20','TARGET_INTRADAY_ASSUMED'),
    ('603186.SH','2024-05-10','2024-05-13','DEFERRED_STOP_OR_TIME'),
    ('000969.SZ','2026-01-08','2026-01-12','TARGET_GAP'),
    ('603196.SH','2026-04-27','2026-04-28','DEFERRED_STOP_OR_TIME'),
)
FIELDS='date,time,code,open,high,low,close,volume,amount'
HEADERS=FIELDS.split(',')


def normalize(bar):
    if not set(HEADERS).issubset(bar):
        raise ValueError('Missing 15m provider fields')
    day=str(bar['date']); stamp=str(bar['time'])
    if len(day)!=10 or len(stamp)<12 or not stamp[:12].isdigit():
        raise ValueError('Nonstandard date/time')
    dt=datetime.strptime(stamp[:12],'%Y%m%d%H%M')
    if dt.strftime('%Y-%m-%d')!=day:
        raise ValueError('15m timestamp/date disagreement')
    vals=[float(bar[k]) for k in ('open','high','low','close','volume','amount')]
    o,h,l,c,vol,amount=vals
    if not (0<l<=min(o,c)<=max(o,c)<=h and vol>=0 and amount>=0):
        raise ValueError('Invalid OHLCV ranges')
    return {**{k:str(bar[k]) for k in HEADERS}, 'bar_end_hhmm':dt.strftime('%H:%M')}


def summarize_rows(rows, entry_date, exit_date):
    byday={}
    for r in rows:
        byday.setdefault(r['date'],[]).append(r)
    days=[]
    for day,rs in sorted(byday.items()):
        ts=[x['bar_end_hhmm'] for x in rs]
        complete=(len(ts)==16 and ts==list(EXPECTED) and len(ts)==len(set(ts)))
        days.append({'date':day,'bars':len(ts),'standard_session_complete':complete,
                     'first_time':ts[0] if ts else None,'last_time':ts[-1] if ts else None})
    required={entry_date,exit_date}
    have={v['date'] for v in days if v['standard_session_complete']}
    status='COMPLETE_REQUESTED_DAYS' if required<=have else (
           'PARTIAL_OR_MISSING_SESSION' if rows else 'NO_BARS_RETURNED')
    return {'status':status,'total_rows':len(rows),'daily_quality':days,
            'entry_and_exit_days_complete':required<=have,
            'note':'No auction 09:30 evidence, actual order fill or within-bar OHLC path validated'}


def fetch_baostock():
    import baostock as bs
    login=bs.login()
    if login.error_code!='0':
        raise RuntimeError('baostock login refused: '+str(login.error_msg))
    try:
        collected=[]
        for symbol,entry,exit,reason in CASES:
            market,code=symbol.split('.')
            bs_code=('sh.' if market=='SH' else 'sz.')+code
            q=bs.query_history_k_data_plus(bs_code,FIELDS,start_date=entry,end_date=exit,
                                           frequency='15',adjustflag='3')
            if q.error_code!='0':
                raise RuntimeError(symbol+': '+str(q.error_msg))
            bars=[]
            while q.next():
                raw=dict(zip(q.fields,q.get_row_data()))
                norm=normalize(raw)
                if raw['code'] != bs_code:
                    raise ValueError('Wrong symbol from provider')
                norm['vendor_symbol']=symbol
                bars.append(norm)
            collected.append({'symbol':symbol,'entry_date':entry,'exit_date':exit,
                              'historical_v2_exit_reason':reason,'bars':bars,
                              'quality':summarize_rows(bars,entry,exit)})
        return collected
    finally:
        bs.logout()


def save_probe(outdir,results):
    if outdir.exists():
        raise FileExistsError('Never overwrite immutable research evidence')
    outdir.mkdir(parents=True)
    rows=[r for x in results for r in x['bars']]
    table=outdir/'sample_bars.csv'
    with table.open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['vendor_symbol']+HEADERS+['bar_end_hhmm'])
        w.writeheader()
        w.writerows(rows)
    manifest={
        'version':'BAOSTOCK_5_V2_LOOSE_TRADES_15M_PROBE_V1',
        'mode':'RESEARCH_ONLY_NOT_EXECUTION_VERIFICATION',
        'selected_after_outcomes':True,
        'source_description':'Baostock alternative, unadjusted 15m OHLCV',
        'source_gold_equivalence':'NOT_TESTED',
        'rows':len(rows),
        'csv_sha256':sha256(table.read_bytes()).hexdigest(),
        'cases':[{'symbol':x['symbol'],'entry_date':x['entry_date'],
                  'exit_date':x['exit_date'],'exit_reason':x['historical_v2_exit_reason'],
                  'quality':x['quality']} for x in results],
        'disclosure':'15m bars do not prove fill prices, 09:30 auction, or intrabar order sequence'
    }
    (outdir/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'result':'V4_MINUTE_SAMPLE_PROBE_COMPLETED_NOT_FILL_VERIFIED',
                      'cases':[(x['symbol'],x['quality']['status']) for x in results],
                      'rows':len(rows)},ensure_ascii=False))
    return manifest


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--output-dir',required=True,type=Path)
    args=p.parse_args()
    save_probe(args.output_dir,fetch_baostock())


if __name__=='__main__':main()
