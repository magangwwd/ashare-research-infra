#!/usr/bin/env python3
"""Create daily immutable public finance careers snapshot from curated and monitored sources."""
from __future__ import annotations
import argparse,datetime,hashlib,json,os,pathlib
from update_sources import validate_curated

def make(config,monitor,outdir,run_id):
    curated=validate_curated(json.loads(pathlib.Path(config).read_text("utf-8")))
    m=json.loads(pathlib.Path(monitor).read_text("utf-8"))
    if m.get("schema")!=1 or "sources" not in m:
        raise ValueError("Source check schema invalid")
    now=datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    today=(now+datetime.timedelta(hours=8)).date().isoformat()
    # Do not rewrite official announcement dates, verification status, or job posting evidence.
    for j in curated["listings"]:
        if j.get("role_confirmed") and not j.get("verified_from"):
            raise ValueError("Job role stated as verified without source")
        if j.get("deadline"):
            datetime.date.fromisoformat(j["deadline"])
    snap=dict(curated)
    snap.update({
        "collected_at":now.isoformat().replace("+00:00","Z"),
        "source_monitor_checked_at":m["checked_at"],
        "source_monitor":m,
        "data_as_of":curated["as_of"],
        "curated_sha256":hashlib.sha256(pathlib.Path(config).read_bytes()).hexdigest(),
        "source_quality":"Official source metadata plus human-curated verified fields; scraped leads are unverified.",
        "about_dates":"Fresh snapshot only confirms source monitoring at that time; dated job requirements may be older or already closed."
    })
    outdir=pathlib.Path(outdir);outdir.mkdir(parents=True,exist_ok=True)
    name="finance_"+now.strftime("%Y%m%dT%H%M%SZ")+"_"+str(run_id)+".json"
    out=outdir/name
    out.write_text(json.dumps(snap,ensure_ascii=False,indent=2)+"\n","utf-8")
    return {"path":str(out),"name":name,"bytes":out.stat().st_size,
            "sha256":hashlib.sha256(out.read_bytes()).hexdigest(),
            "listings":len(snap["listings"]),"as_of":today}

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--config",required=True);p.add_argument("--monitor",required=True)
    p.add_argument("--outdir",required=True);p.add_argument("--run-id",required=True)
    opt=p.parse_args()
    proof=make(opt.config,opt.monitor,opt.outdir,opt.run_id)
    print("FINANCE_SNAPSHOT_OK file="+proof["name"]+" records="+str(proof["listings"])+" sha256="+proof["sha256"])
