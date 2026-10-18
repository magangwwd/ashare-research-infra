"""Restricted publisher: GitHub Actions on main only; immutable archive + Drive round-trip."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timezone

FOLDER_ID = "1cXlP5QNatkjiz7tLDjXKgKry5Jklz3A5"

def verify_file(path):
    data=path.read_bytes()
    if not data or not data[:2]==b"PK":
        raise ValueError("Expected a nonempty ZIP snapshot")
    return data,hashlib.sha256(data).hexdigest()

def publish(snapshot_path):
    import google.auth.transport.requests
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import AuthorizedSession
    secret=os.getenv("GDRIVE_OAUTH_JSON")
    if not secret:
        raise RuntimeError("Drive OAuth secret unavailable; refusing to mark published")
    try:
        info=json.loads(secret)
        creds=Credentials(token=None,refresh_token=info["refresh_token"],
            token_uri="https://oauth2.googleapis.com/token",client_id=info["client_id"],
            client_secret=info["client_secret"],scopes=["https://www.googleapis.com/auth/drive"])
        creds.refresh(google.auth.transport.requests.Request())
    except Exception:
        raise RuntimeError("Drive credentials invalid or refresh failed") from None
    content,sha=verify_file(snapshot_path)
    safe_name=snapshot_path.name
    if not safe_name.startswith("20") or not safe_name.endswith(".zip"):
        raise RuntimeError("Refusing unexpected artifact filename")
    with AuthorizedSession(creds) as s:
        check=s.get("https://www.googleapis.com/drive/v3/files/"+FOLDER_ID,
            params={"fields":"id,name,mimeType,capabilities(canAddChildren)","supportsAllDrives":"true"},timeout=25)
        if check.status_code!=200:
            raise RuntimeError("Target Drive folder unavailable to OAuth identity")
        f=check.json()
        if f.get("id")!=FOLDER_ID or f.get("mimeType")!="application/vnd.google-apps.folder" or not f.get("capabilities",{}).get("canAddChildren"):
            raise RuntimeError("OAuth identity lacks target folder write permission")
        # Search before creating to prevent accidental duplicates after retry.
        escaped=safe_name.replace("'","\\'")
        lookup=s.get("https://www.googleapis.com/drive/v3/files",params={
            "q":f"name = '{escaped}' and '{FOLDER_ID}' in parents and trashed = false",
            "fields":"files(id,name,size,md5Checksum),nextPageToken","pageSize":10},timeout=25)
        if lookup.status_code!=200: raise RuntimeError("Drive duplicate check failed")
        if lookup.json().get("files"):
            raise RuntimeError("Snapshot filename already exists; refusing overwrite")
        init=s.post("https://www.googleapis.com/upload/drive/v3/files",params={"uploadType":"resumable"},
            json={"name":safe_name,"mimeType":"application/zip","parents":[FOLDER_ID]},
            headers={"X-Upload-Content-Type":"application/zip",
                     "X-Upload-Content-Length":str(len(content))},timeout=30)
        location=init.headers.get("Location")
        if init.status_code not in (200,201) or not location or not location.startswith("https://www.googleapis.com/"):
            raise RuntimeError("Drive resumable-upload initialization failed")
        put=s.put(location,data=content,headers={"Content-Type":"application/zip"},timeout=90)
        if put.status_code not in (200,201):
            raise RuntimeError("Drive upload failed")
        uploaded=put.json()
        file_id=uploaded.get("id")
        if not file_id or not isinstance(file_id,str):
            raise RuntimeError("Drive returned no file id")
        get=s.get("https://www.googleapis.com/drive/v3/files/"+file_id,
            params={"alt":"media"},timeout=90)
        if get.status_code!=200 or hashlib.sha256(get.content).hexdigest()!=sha:
            raise RuntimeError("Drive download/checksum mismatch; uploaded item requires investigation")
    return {"result":"passed_drive_readback","file_id":file_id,"filename":safe_name,
            "folder_id":FOLDER_ID,"sha256":sha,"bytes":len(content),
            "checked_at_utc":datetime.now(timezone.utc).isoformat()}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--input",required=True)
    parser.add_argument("--report",required=True)
    args=parser.parse_args()
    report_path=Path(args.report)
    try:
        results=publish(Path(args.input))
        report_path.parent.mkdir(parents=True,exist_ok=True)
        report_path.write_text(json.dumps(results,indent=2)+"\n",encoding="utf-8")
        print("SUCCESS Drive archive written and readback SHA-256 verified: "+results["sha256"]+" file="+results["file_id"])
    except Exception as exc:
        # Never include OAuth credentials, bearer strings, raw HTTP responses or URL tokens in logs.
        print("FAILED Drive archive roundtrip: "+type(exc).__name__)
        raise SystemExit(1)

if __name__=="__main__":
    main()
