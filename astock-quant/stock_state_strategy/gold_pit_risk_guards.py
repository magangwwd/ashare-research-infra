"""Strict historical execution-risk barriers, paper replay only.

All joins are (symbol, effective trading date) against records stored in Gold.
Never infer a safe status from a missing limit or historical name interval.
The stock-name ST check may use only a record effective and announced
no later than that execution date; never current-day A-pool membership.
"""
from __future__ import annotations
from collections import defaultdict
from pathlib import Path
import math
import pandas as pd

TICK_BUFFER = 0.006

def _date(x):
    t=pd.to_datetime(x,errors="coerce")
    return None if pd.isna(t) else t.normalize()

class GoldPITRisk:
    def __init__(self, root: Path):
        self.limits={}
        self.suspended=set()
        self.names=defaultdict(list)
        limits=list((root/"daily_limits").rglob("*.parquet"))
        susp=list((root/"suspension_events").rglob("*.parquet"))
        fname=root/"name_history"/"data.parquet"
        if not limits or not susp or not fname.is_file():
            raise RuntimeError("PIT risk history incomplete or not downloaded: fail closed")
        for f in sorted(limits):
            for r in pd.read_parquet(f, columns=["vendor_symbol","trade_date","up_limit","down_limit"]).itertuples(index=False):
                key=(str(r.vendor_symbol),_date(r.trade_date))
                up,down=float(r.up_limit),float(r.down_limit)
                if not (math.isfinite(up) and math.isfinite(down) and 0<down<up):
                    raise ValueError(f"Invalid limit prices: {key}")
                if key in self.limits:
                    raise ValueError(f"Duplicated limit record {key}")
                self.limits[key]=(up,down)
        for f in sorted(susp):
            for r in pd.read_parquet(f,columns=["vendor_symbol","event_date","event_type"]).itertuples(index=False):
                if r.event_type=="S":
                    d=_date(r.event_date)
                    if d is not None: self.suspended.add((str(r.vendor_symbol),d))
        df=pd.read_parquet(fname, columns=["vendor_symbol","effective_date","end_date","announcement_date","is_st"])
        for r in df.itertuples(index=False):
            symbol=str(r.vendor_symbol)
            begin=_date(r.effective_date)
            if begin is None: continue
            end=_date(r.end_date)
            ann=_date(r.announcement_date)
            state=None if pd.isna(r.is_st) else bool(r.is_st)
            self.names[symbol].append((begin,end,ann,state))
        for arr in self.names.values():arr.sort(key=lambda x:x[0])
        self.file_count=len(limits)+len(susp)+1
        self.limit_rows=len(self.limits)
        self.suspended_events=len(self.suspended)
        self.name_rows=sum(map(len,self.names.values()))

    def name_status(self, symbol: str, day: pd.Timestamp)->str:
        """Only choose a name state effective/announced by the decision day."""
        cand=[]
        for begin,end,ann,state in self.names.get(symbol,()):
            if begin<=day and (end is None or end>=day) and ann is not None and ann<=day:
                cand.append((begin,state))
        if not cand:return "NAME_UNKNOWN"
        latest=max(b for b,_ in cand)
        statuses={state for b,state in cand if b==latest}
        if len(statuses)!=1 or None in statuses:return "NAME_UNKNOWN"
        return "ST" if True in statuses else "NORMAL"

    def entry_reason(self, symbol:str, day:pd.Timestamp, bar)->str|None:
        day=_date(day)
        if (symbol,day) not in self.limits:return "LIMIT_UNKNOWN"
        if (symbol,day) in self.suspended:return "SUSPENDED"
        status=self.name_status(symbol,day)
        if status!="NORMAL":return status
        up,down=self.limits[(symbol,day)]
        op=float(bar["open"])
        if not down-TICK_BUFFER<=op<=up+TICK_BUFFER:return "OPEN_OUTSIDE_LIMIT"
        if op>=up-TICK_BUFFER:return "AT_LIMIT_UP_CANNOT_ASSUME_FILL"
        return None

    def exit_reason(self,symbol:str,day:pd.Timestamp,bar)->str|None:
        day=_date(day)
        if (symbol,day) not in self.limits:return "EXIT_LIMIT_UNKNOWN"
        if (symbol,day) in self.suspended:return "EXIT_SUSPENDED"
        up,down=self.limits[(symbol,day)]
        if float(bar["open"])<down-TICK_BUFFER:return "EXIT_BAR_BELOW_LIMIT"
        one_word=abs(float(bar["high"])-float(bar["low"]))<=TICK_BUFFER
        if one_word and float(bar["open"])<=down+TICK_BUFFER:
            return "EXIT_LOCKED_LIMIT_DOWN"
        return None

    def summary(self)->dict:
        unique_days={day for _,day in self.limits}
        return {
            "limit_rows":self.limit_rows,
            "first_limit_date":str(min(unique_days).date()) if unique_days else None,
            "last_limit_date":str(max(unique_days).date()) if unique_days else None,
            "limit_dates":len(unique_days),
            "suspended_events":self.suspended_events,
            "name_intervals":self.name_rows,
            "unknown_is_rejected":True
        }
