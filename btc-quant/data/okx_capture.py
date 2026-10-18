import argparse,datetime as dt,hashlib,json,pathlib,time,urllib.request
p=argparse.ArgumentParser();p.add_argument("--out",required=True);a=p.parse_args()
out=pathlib.Path(a.out);out.mkdir(parents=True,exist_ok=True)
rows=[];errors=[]
for n in range(3):
 for inst in ("BTC-USDT-SWAP","BTC-USDT"):
  for kind,path in (("books5",f"/api/v5/market/books?instId={inst}&sz=5"),("trades",f"/api/v5/market/trades?instId={inst}&limit=100"),("ticker",f"/api/v5/market/ticker?instId={inst}")):
   now=dt.datetime.now(dt.timezone.utc).isoformat()
   try:
    with urllib.request.urlopen(urllib.request.Request("https://www.okx.com"+path,headers={"User-Agent":"BTC-quant-research"}),timeout=20) as resp: data=json.load(resp)
    if data.get("code")!="0": raise ValueError(str(data)[:300])
    rows.append({"received_utc":now,"instrument":inst,"kind":kind,"data":data})
   except Exception as e: errors.append({"time":now,"instrument":inst,"kind":kind,"error":str(e)})
 if n<2: time.sleep(15)
stamp=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
f=out/("snapshot_"+stamp+".jsonl")
f.write_text("".join(json.dumps(r,separators=(",",":"))+"\n" for r in rows))
manifest={"created_utc":stamp,"kind":"SPARSE_REST_NOT_CONTINUOUS_L2","rows":len(rows),"errors":errors,"file":f.name,"sha256":hashlib.sha256(f.read_bytes()).hexdigest(),"history_status":"NOT_DOWNLOADED"}
(out/("manifest_"+stamp+".json")).write_text(json.dumps(manifest,indent=2))
if errors or not rows: raise SystemExit("Incomplete capture")
