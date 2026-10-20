#!/usr/bin/env python3
"""Read the newest immutable finance recruiting JSON from Google Drive. Fail closed."""
import argparse,datetime,hashlib,json,pathlib,re
from publish_drive import auth,FOLDER_ID,API
NAME=re.compile(r"^finance_(\d{8}T\d{6}Z)_(\d+)\.json$")
MAX_BYTES=8_000_000

def choose(files):
    rows=[x for x in files if NAME.fullmatch(x.get("name","")) and x.get("mimeType") in ("application/json","text/plain","application/octet-stream")]
    rows.sort(key=lambda x:NAME.fullmatch(x["name"]).group(1),reverse=True)
    return rows[0] if rows else None

def fetch_latest(output):
    with auth() as s:
        check=s.get(API+"/"+FOLDER_ID,params={"fields":"id,mimeType","supportsAllDrives":"true"},timeout=30)
        if check.status_code!=200 or check.json().get("mimeType")!="application/vnd.google-apps.folder":
            raise RuntimeError("Drive finance folder missing")
        files=[];token=None
        for _ in range(10):
            params={"q":f"'{FOLDER_ID}' in parents and trashed = false",
                    "fields":"nextPageToken,files(id,name,mimeType,size,modifiedTime)",
                    "pageSize":100,"orderBy":"name desc"}
            if token:params["pageToken"]=token
            r=s.get(API,params=params,timeout=30)
            if r.status_code!=200:raise RuntimeError("Cannot list Drive recruitment snapshots")
            data=r.json();files.extend(data.get("files",[]));token=data.get("nextPageToken")
            if not token:break
        selected=choose(files)
        if not selected:raise RuntimeError("No verified recruiting snapshot is stored in Drive")
        size=int(selected.get("size") or 0)
        if size<=100 or size>MAX_BYTES:raise RuntimeError("Unexpected snapshot size")
        response=s.get(API+"/"+selected["id"],params={"alt":"media"},timeout=80)
        if response.status_code!=200 or len(response.content)!=size:
            raise RuntimeError("Incomplete recruitment snapshot read")
        raw=response.content
        doc=json.loads(raw)
        if doc.get("schema")!=1 or len(doc.get("listings",[]))<8:
            raise RuntimeError("Drive recruitment JSON schema/record count invalid")
        output=pathlib.Path(output);output.parent.mkdir(parents=True,exist_ok=True)
        output.write_bytes(raw)
        return {"readback_at_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "drive_file_id":selected["id"],"filename":selected["name"],
                "snapshot_collected_at":doc.get("collected_at"),
                "sha256":hashlib.sha256(raw).hexdigest(),"bytes":size,
                "listings":len(doc["listings"])}

if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--output",required=True);p.add_argument("--receipt",required=True)
    opts=p.parse_args()
    try:
        r=fetch_latest(opts.output)
        receipt=pathlib.Path(opts.receipt);receipt.parent.mkdir(parents=True,exist_ok=True)
        receipt.write_text(json.dumps(r,ensure_ascii=False,indent=2)+"\n","utf-8")
        print("FINANCE_DRIVE_READ_OK snapshot="+r["filename"]+" sha256="+r["sha256"])
    except Exception as e:
        print("FINANCE_DRIVE_READ_FAILED "+type(e).__name__)
        raise SystemExit(1)
