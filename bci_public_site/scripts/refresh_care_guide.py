"""Download and validate the editorial care/hospital guide stored in Google Drive.

Only publishes vetted public-source directory metadata and general educational prompts.
No patient identifiers, no individual treatment recommendations, no unsupported hospital ranking.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit
from datetime import datetime, timezone
from refresh_drive import get_session

DOC_ID = "1YPYjBWGa9BMqG8qQ7AFqommHDSiiywMONh8LcVvwF5s"
MAX_BYTES = 250_000
ALLOWED_HOSTS = {
    "www.shca.org.cn", "www.cicams.ac.cn", "www.bjcancer.org",
    "bjcancer.org", "www.sysucc.org.cn", "xuebao.301hospital.com.cn",
    "www.cancer.gov", "www.china-oncology.com"
}
def safe_url(value):
    u=urlsplit(str(value or ""))
    return u.scheme=="https" and (u.hostname or "") in ALLOWED_HOSTS and not u.username and not u.password

def validate(data):
    if not isinstance(data,dict) or data.get("schema_version")!=1:
        raise ValueError("Unsupported editorial guide schema")
    if data.get("review_status")!="editorial_reference_only_not_medically_reviewed":
        raise ValueError("Editorial evidence state must be explicit")
    if not all(isinstance(data.get(k),list) for k in ("hospitals","journeys","frontiers")):
        raise ValueError("Missing care navigation collections")
    if not 1<=len(data["hospitals"])<=100 or not 1<=len(data["journeys"])<=30 or not 1<=len(data["frontiers"])<=40:
        raise ValueError("Unexpected care navigation size")
    for key in ("hospitals","journeys","frontiers"):
        ids=set()
        for row in data[key]:
            if not isinstance(row,dict):
                raise ValueError("Expected structured records")
            if not isinstance(row.get("id"),str) or not row["id"].isalnum():
                raise ValueError("Invalid record id")
            if row["id"] in ids:
                raise ValueError("Duplicated editorial record id")
            ids.add(row["id"])
            link=row.get("url" if key=="hospitals" else "source_url")
            if not safe_url(link):
                raise ValueError("Care reference URL is not in the reviewed source allowlist")
            if key=="hospitals":
                for field in ("name","city","type","highlight","caution","evidence","verified"):
                    if not isinstance(row.get(field),str) or not row[field].strip():
                        raise ValueError("Incomplete hospital description: "+field)
                if not isinstance(row.get("focus"),list) or not row["focus"]:
                    raise ValueError("Hospital focus must be sourced")
                if row.get("secondary_url") and not safe_url(row["secondary_url"]):
                    raise ValueError("Invalid secondary URL")
            if key=="journeys" and (not isinstance(row.get("steps"),list) or not isinstance(row.get("ask"),list)):
                raise ValueError("Inadequate pathway data")
            if key=="frontiers" and not all(isinstance(row.get(t),str) for t in ("topic","kind","scope","limits")):
                raise ValueError("Inadequate frontier evidence qualifications")
    blob=json.dumps(data,ensure_ascii=False)
    for suspicious in ("github.com", "GDRIVE_OAUTH_JSON", "refresh_token", "client_secret", "patient_name", "patient_id"):
        if suspicious.lower() in blob.lower():
            raise ValueError("Prohibited data detected in editorial guide")
    return data

def pull(output):
    session=get_session()
    try:
        url=f"https://www.googleapis.com/drive/v3/files/{DOC_ID}/export"
        with session:
            response=session.get(url,params={"mimeType":"text/plain"},timeout=40)
            if response.status_code!=200 or not response.content or len(response.content)>MAX_BYTES:
                raise RuntimeError("Could not export bounded public editorial Google Doc")
            raw=response.content.decode("utf-8-sig")
            data=validate(json.loads(raw))
    except (RuntimeError,ValueError):
        raise
    except Exception:
        raise RuntimeError("Google Drive editorial guide could not be retrieved and validated") from None
    path=Path(output)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return {
        "result":"verified_public_editorial_guide",
        "downloaded_at_utc":datetime.now(timezone.utc).isoformat(),
        "document_id":DOC_ID,
        "hospitals":len(data["hospitals"]),
        "journeys":len(data["journeys"]),
        "frontiers":len(data["frontiers"]),
        "sha256":hashlib.sha256(path.read_bytes()).hexdigest()
    }

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--output",required=True)
    p.add_argument("--report",required=True)
    a=p.parse_args()
    try:
        proof=pull(a.output)
        Path(a.report).write_text(json.dumps(proof,indent=2)+"\n",encoding="utf-8")
        print("CARE_GUIDE_OK hospitals="+str(proof["hospitals"])+" pathways="+str(proof["journeys"])+" frontiers="+str(proof["frontiers"])+" sha256="+proof["sha256"])
    except Exception as err:
        print("CARE_GUIDE_FAILED "+type(err).__name__)
        raise SystemExit(1)
