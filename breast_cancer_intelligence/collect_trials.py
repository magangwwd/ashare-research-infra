"""ClinicalTrials.gov v2 public studies collector. No private patient records."""
import argparse
import hashlib
import json
import pathlib
import urllib.parse
import urllib.request
from datetime import datetime, timezone

ENDPOINT="https://clinicaltrials.gov/api/v2/studies"
ROOT=pathlib.Path(__file__).resolve().parent
def build_url(page_size=20):
    return ENDPOINT+"?"+urllib.parse.urlencode({"query.cond":"Breast Cancer","pageSize":page_size,"format":"json","countTotal":"true"})

def normalize(study):
    p=study.get("protocolSection",{})
    i=p.get("identificationModule",{})
    s=p.get("statusModule",{})
    return {"nct_id":i.get("nctId"),"title":i.get("briefTitle"),"overall_status":s.get("overallStatus"),"last_update_posted":s.get("lastUpdatePostDateStruct",{}).get("date"),"source_url":"https://clinicaltrials.gov/study/"+i["nctId"] if i.get("nctId") else None,"verification_state":"unreviewed"}

def run(output=None):
    with urllib.request.urlopen(urllib.request.Request(build_url(),headers={"User-Agent":"BreastCancerIntelligenceResearch/0.1"}),timeout=30) as r:
        payload=r.read(10_000_001)
    if len(payload)>10_000_000: raise ValueError("API payload over limit")
    obj=json.loads(payload)
    records=[normalize(s) for s in obj.get("studies",[])]
    result={"source":ENDPOINT,"fetched_at":datetime.now(timezone.utc).isoformat(),"raw_sha256":hashlib.sha256(payload).hexdigest(),"records":records,"count":len(records)}
    if output:
        path=pathlib.Path(output)
        if path.exists(): raise FileExistsError("Refuse overwrite: "+str(path))
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    return result

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--dry-run",action="store_true")
    p.add_argument("--output")
    args=p.parse_args()
    print(json.dumps({"mode":"dry_run","url":build_url()} if args.dry_run else run(args.output),ensure_ascii=False,indent=2))
