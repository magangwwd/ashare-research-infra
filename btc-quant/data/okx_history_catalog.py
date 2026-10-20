import datetime as dt,json,pathlib,sys,urllib.request
out=pathlib.Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
url="https://www.okx.com/historical-data"
status="UNVERIFIED"
try:
 with urllib.request.urlopen(urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"}),timeout=20) as r: status="HTTP_"+str(r.status)
except Exception as e: status="ERROR_"+str(e)[:150]
(out/"historical_catalog.json").write_text(json.dumps({"checked_utc":dt.datetime.now(dt.timezone.utc).isoformat(),"official_portal":url,"portal_status":status,"historical_files_downloaded":0,"note":"Historical archive URL, licensing and actual files must be verified before backfill can be claimed"},indent=2))
