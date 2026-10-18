"""Bounded resumable OKX public history-trades downloader; 3-month API limit."""
import argparse,datetime as dt,hashlib,json,pathlib,time,urllib.parse,urllib.request
p=argparse.ArgumentParser()
p.add_argument("--out",required=True);p.add_argument("--instrument",default="BTC-USDT-SWAP")
p.add_argument("--pages",type=int,default=5)
a=p.parse_args()
if not 1<=a.pages<=30: raise SystemExit("pages out of bounds")
out=pathlib.Path(a.out);out.mkdir(parents=True,exist_ok=True)
ck=out/(a.instrument+"_checkpoint.json")
state=json.loads(ck.read_text()) if ck.exists() else {"after":None,"pages":0,"complete":False}
for i in range(a.pages):
 if state["complete"]: break
 q={"instId":a.instrument,"limit":"100"}
 if state["after"]: q["after"]=state["after"]
 url="https://www.okx.com/api/v5/market/history-trades?"+urllib.parse.urlencode(q)
 with urllib.request.urlopen(urllib.request.Request(url,headers={"User-Agent":"BTC-quant-research"}),timeout=30) as r: body=json.load(r)
 if body.get("code")!="0": raise RuntimeError(str(body)[:200])
 rows=body.get("data",[])
 if not rows: state["complete"]=True;ck.write_text(json.dumps(state));break
 oldest=rows[-1]["tradeId"]
 if oldest==state["after"]: raise RuntimeError("Pagination cursor did not advance")
 data="".join(json.dumps(x,separators=(",",":"))+"\n" for x in rows)
 name=f"{a.instrument}_page_{state['pages']:06d}.jsonl"
 target=out/name
 if target.exists(): raise RuntimeError("Refusing to overwrite existing page")
 target.write_text(data)
 state.update({"after":oldest,"pages":state["pages"]+1,"last_file":name,"last_sha256":hashlib.sha256(target.read_bytes()).hexdigest(),"last_utc":dt.datetime.now(dt.timezone.utc).isoformat()})
 ck.write_text(json.dumps(state,indent=2))
 time.sleep(.2)
print(json.dumps(state))
