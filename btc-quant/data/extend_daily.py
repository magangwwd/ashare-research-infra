#!/usr/bin/env python3
"""Append only genuine Binance Spot UTC 1d archive candles to pinned 2020-Sep26 data.

NEVER invent, interpolate or resample a missing day. History before and
including 2026-10-14 is a BACKFILL, not prospective paper evidence.
"""
from __future__ import annotations
import argparse
import csv
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import time
import urllib.request
import zipfile

BASE_URL = "https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1d/"
EXPECTED_SHA = "67acc682f6430e16a721747a59dba0d51e913ab9730d30275ab89787251bc076"
COLUMNS = ("date","open","high","low","close","volume")
BASE_END = date(2026,9,30)
FIRST_APPEND = date(2026,10,1)
FORWARD_START = date(2026,10,15)

def parse_utc_date(value):
    raw = int(value)
    denom = 1_000_000 if raw >= 10**15 else 1_000 if raw >= 10**12 else 1
    timestamp = datetime.fromtimestamp(raw/denom,timezone.utc)
    if timestamp.time().isoformat() != "00:00:00":
        raise ValueError("not UTC midnight: "+str(value))
    return timestamp.date()

def read_pinned_base(path, expected_sha=EXPECTED_SHA):
    payload=Path(path).read_bytes()
    digest=hashlib.sha256(payload).hexdigest()
    if digest != expected_sha:
        raise ValueError("FROZEN BASE HASH MISMATCH: "+digest)
    rows=list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))
    if not rows or date.fromisoformat(rows[-1]["date"]) != BASE_END:
        raise ValueError("frozen daily base not ending on 2026-09-30")
    if len(rows)!=2465:
        raise ValueError("frozen historical base rowcount mismatch")
    return rows,digest

def archive_bytes(day, cache):
    cache=Path(cache)
    cache.mkdir(parents=True,exist_ok=True)
    name="BTCUSDT-1d-"+day.isoformat()+".zip"
    path=cache/name
    if path.is_file():
        return path.read_bytes(),name
    last_exc=None
    for attempt in range(3):
        try:
            request=urllib.request.Request(BASE_URL+name,headers={"User-Agent":"BTC-frozen-research/1.0"})
            with urllib.request.urlopen(request,timeout=45) as response:
                data=response.read()
            if not data.startswith(b"PK"):
                raise ValueError("not ZIP")
            tmp=path.with_suffix(".partial")
            tmp.write_bytes(data)
            tmp.replace(path)
            return data,name
        except Exception as e:
            last_exc=e
            if attempt!=2:
                time.sleep(2**attempt)
    raise RuntimeError("cannot retrieve original daily archive "+name) from last_exc

def unpack_day(blob,day):
    with zipfile.ZipFile(io.BytesIO(blob)) as zipped:
        csv_files=[x for x in zipped.namelist() if x.lower().endswith(".csv")]
        if len(csv_files)!=1:
            raise ValueError("invalid archive CSV member count")
        raw=zipped.read(csv_files[0]).decode("utf-8-sig")
    entries=[x for x in csv.reader(io.StringIO(raw)) if x and x[0].strip().lower()!="open_time"]
    if len(entries)!=1:
        raise ValueError("expected exactly one daily OHLCV bar: "+str(day))
    r=entries[0]
    if parse_utc_date(r[0])!=day or len(r)<6:
        raise ValueError("daily archive timestamp/shape mismatch: "+str(day))
    vals=[float(v) for v in r[1:6]]
    op,high,low,cl,vol=vals
    if not all(math.isfinite(x) for x in vals) or not (0<low<=min(op,cl)<=max(op,cl)<=high and vol>=0):
        raise ValueError("invalid daily OHLCV "+str(day))
    return dict(zip(COLUMNS,[day.isoformat(),*vals]))

def verify(rows):
    previous=None
    for r in rows:
        d=date.fromisoformat(r["date"])
        if previous and (d-previous).days!=1:
            raise ValueError("MISSING/DUPLICATE UTC DAILY CANDLE: "+str(previous)+" -> "+str(d))
        op,high,low,cl,vol=[float(r[x]) for x in COLUMNS[1:]]
        if not all(math.isfinite(x) for x in (op,high,low,cl,vol)) or not(0<low<=min(op,cl)<=max(op,cl)<=high and vol>=0):
            raise ValueError("invalid price "+str(d))
        previous=d

def extend(base,out,end,cache):
    if end < BASE_END or end > datetime.now(timezone.utc).date()-timedelta(days=1):
        raise ValueError("end must not exceed LAST COMPLETE UTC DAY")
    rows,base_sha=read_pinned_base(base)
    evidence=[]
    day=FIRST_APPEND
    while day<=end:
        blob,name=archive_bytes(day,cache)
        rows.append(unpack_day(blob,day))
        evidence.append({"date":day.isoformat(),"archive":name,
                         "source_url":BASE_URL+name,
                         "archive_sha256":hashlib.sha256(blob).hexdigest()})
        day+=timedelta(days=1)
    verify(rows)
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    dst=out/"btc_daily_utc.csv"
    with dst.open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    manifest={
      "source":"Binance Vision original BTCUSDT Spot daily 1d UTC ZIP",
      "baseline_end":BASE_END.isoformat(),"baseline_sha256":base_sha,
      "end":end.isoformat(),"rows":len(rows),"archive_count":len(evidence),
      "utc_candle_gaps":0,"utc_candle_duplicates":0,"no_interpolation":True,
      "cutoff_backfill_through":"2026-10-14",
      "forward_first_utc":"2026-10-15",
      "data_is_prospective_only_after_freeze":end>=FORWARD_START,
      "csv_sha256":hashlib.sha256(dst.read_bytes()).hexdigest(),
      "source_archives":evidence,
    }
    (out/"data_provenance.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf8")
    print(json.dumps({k:v for k,v in manifest.items() if k!="source_archives"},ensure_ascii=False,indent=2))
    return manifest

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--base",required=True)
    p.add_argument("--out",required=True)
    p.add_argument("--cache",required=True)
    p.add_argument("--end",required=True)
    a=p.parse_args()
    extend(a.base,a.out,date.fromisoformat(a.end),a.cache)
