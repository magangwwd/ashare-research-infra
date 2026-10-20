#!/usr/bin/env python3
"""Publish immutable finance-recruiting snapshot to a dedicated Google Drive folder.

Uses the already configured repository-level GDRIVE_OAUTH_JSON secret.
Uploads only curated public announcement metadata and source status. Never logs credentials.
"""
import argparse, datetime, hashlib, json, os, pathlib, re
from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials

FOLDER_ID="1_-tsriPoUm2KosCuC8xHIuzAwZLvweub"
API="https://www.googleapis.com/drive/v3/files"
NAME=re.compile(r"^finance_\d{8}T\d{6}Z_\d+\.json$")
MAX_BYTES=3_000_000

def auth():
    raw=os.environ.get("GDRIVE_OAUTH_JSON")
    if not raw: raise RuntimeError("Drive secret missing")
    try:
        params=json.loads(raw)
        creds=Credentials(token=None,refresh_token=params["refresh_token"],
             client_id=params["client_id"],client_secret=params["client_secret"],
             token_uri="https://oauth2.googleapis.com/token",
             scopes=["https://www.googleapis.com/auth/drive"])
        creds.refresh(Request())
        return AuthorizedSession(creds)
    except Exception:
        raise RuntimeError("Cannot refresh Google Drive credentials") from None

def publish(src: pathlib.Path):
    if not NAME.fullmatch(src.name):raise ValueError("Unexpected Finance snapshot filename")
    raw=src.read_bytes()
    if not 100<len(raw)<=MAX_BYTES:raise ValueError("Snapshot unexpected size")
    body=json.loads(raw)
    if body.get("schema")!=1 or len(body.get("listings",[]))<8:
        raise ValueError("Invalid published jobs snapshot")
    sha=hashlib.sha256(raw).hexdigest()
    with auth() as s:
        f=s.get(API+"/"+FOLDER_ID,params={"fields":"id,mimeType,capabilities(canAddChildren)","supportsAllDrives":"true"},timeout=25)
        if f.status_code!=200:raise RuntimeError("Finance Drive folder not accessible to Actions OAuth")
        folder=f.json()
        if folder.get("id")!=FOLDER_ID or folder.get("mimeType")!="application/vnd.google-apps.folder" or not folder.get("capabilities",{}).get("canAddChildren"):
            raise RuntimeError("Finance Drive folder write permission missing")
        existing=s.get(API,params={"q":f"name = '{src.name}' and '{FOLDER_ID}' in parents and trashed = false",
            "fields":"files(id,name,size),nextPageToken","pageSize":100},timeout=30)
        if existing.status_code!=200:raise RuntimeError("Cannot check Drive immutable filename")
        if existing.json().get("files"):raise RuntimeError("Filename already exists; do not overwrite snapshot")
        init=s.post("https://www.googleapis.com/upload/drive/v3/files",
            params={"uploadType":"resumable"},
            json={"name":src.name,"mimeType":"application/json","parents":[FOLDER_ID]},
            headers={"X-Upload-Content-Type":"application/json","X-Upload-Content-Length":str(len(raw))},timeout=30)
        location=init.headers.get("Location","")
        if init.status_code not in (200,201) or not location.startswith("https://www.googleapis.com/"):
            raise RuntimeError("Could not initialize Drive upload")
        pushed=s.put(location,data=raw,headers={"Content-Type":"application/json"},timeout=90)
        if pushed.status_code not in (200,201) or not pushed.json().get("id"):
            raise RuntimeError("Drive did not complete upload")
        file_id=pushed.json()["id"]
        read=s.get(API+"/"+file_id,params={"alt":"media"},timeout=90)
        if read.status_code!=200 or hashlib.sha256(read.content).hexdigest()!=sha:
            raise RuntimeError("Drive readback SHA256 mismatch")
    return {"status":"passed_drive_readback","folder_id":FOLDER_ID,
        "file_id":file_id,"file_name":src.name,"sha256":sha,"bytes":len(raw),
        "verified_at_utc":datetime.datetime.now(datetime.timezone.utc).isoformat()}

if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--input",required=True);p.add_argument("--receipt",required=True)
    opts=p.parse_args()
    try:
        result=publish(pathlib.Path(opts.input))
        output=pathlib.Path(opts.receipt);output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n","utf-8")
        print("FINANCE_DRIVE_OK file="+result["file_name"]+" sha256="+result["sha256"])
    except Exception as exc:
        print("FINANCE_DRIVE_FAILED reason="+type(exc).__name__)
        raise SystemExit(1)
