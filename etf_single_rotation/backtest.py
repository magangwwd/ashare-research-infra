import csv, sys, pathlib
from collections import defaultdict
from datetime import datetime

p=pathlib.Path(sys.argv[1]) if len(sys.argv)>1 else pathlib.Path('data/prices.csv')
if not p.exists(): raise SystemExit(f'MISSING DATA: {p}; no synthetic results generated')
rows=defaultdict(dict)
with p.open(newline='',encoding='utf-8-sig') as f:
 for r in csv.DictReader(f):
  d=datetime.strptime(r['date'],'%Y-%m-%d').date(); rows[d][r['symbol']]={'open':float(r['open']),'close':float(r['close'])}
dates=sorted(rows); symbols=sorted({s for v in rows.values() for s in v}); history={s:[] for s in symbols}
for d in dates:
 for s in symbols:
  if s in rows[d]: history[s].append((d,rows[d][s]['close']))
# Conservative initial implementation: generate signals only; no fabricated fills or equity curve.
out=pathlib.Path('etf_single_rotation/results/signals.csv');out.parent.mkdir(parents=True,exist_ok=True)
with out.open('w',newline='') as f:
 w=csv.writer(f);w.writerow(['signal_date','symbol','score','next_session_execution_required'])
 for i,d in enumerate(dates):
  if i+1<len(dates) and (dates[i+1].year,dates[i+1].month)==(d.year,d.month):continue
  eligible=[]
  for s in symbols:
   h=[v for day,v in history[s] if day<=d]
   if len(h)<121:continue
   c=h[-1]; sma=sum(h[-120:])/120
   if c<=sma:continue
   score=.2*(c/h[-21]-1)+.5*(c/h[-61]-1)+.3*(c/h[-121]-1)
   eligible.append((score,s))
  eligible.sort(reverse=True)
  w.writerow([d.isoformat(),eligible[0][1] if eligible else 'CASH',f'{eligible[0][0]:.8f}' if eligible else '', 'YES'])
print(f'Wrote monthly signals to {out}. NOT a performance backtest; fills, fees, stops and drawdowns are not yet implemented.')
