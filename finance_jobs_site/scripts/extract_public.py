#!/usr/bin/env python3
"""Validate downloaded Drive snapshot; export public site files without credentials."""
import argparse,hashlib,json,pathlib
from update_sources import validate_curated

def export_snapshot(source,publicdir):
    raw=pathlib.Path(source).read_bytes()
    if not 100<len(raw)<3_000_000:raise ValueError("unexpected snapshot size")
    data=json.loads(raw)
    validate_curated(data)
    monitor=data.get("source_monitor")
    if not monitor or monitor.get("schema")!=1:raise ValueError("No source scan evidence")
    if not data.get("collected_at") or not data.get("data_as_of"):raise ValueError("No truthful timestamps")
    for item in data["listings"]:
        if item["qs_threshold"] is not None and not item.get("qs_details"):
            raise ValueError("Invalid QS evidence")
    publicdir=pathlib.Path(publicdir);publicdir.mkdir(parents=True,exist_ok=True)
    exposed={k:v for k,v in data.items() if k!="source_monitor"}
    (publicdir/"latest.json").write_text(json.dumps(exposed,ensure_ascii=False,indent=2)+"\n","utf-8")
    (publicdir/"monitor.json").write_text(json.dumps(monitor,ensure_ascii=False,indent=2)+"\n","utf-8")
    return {"archive_sha256":hashlib.sha256(raw).hexdigest(),
        "records":len(data["listings"]),"sources_checked":len(monitor["sources"]),
        "snapshot":data["collected_at"],"data_as_of":data["data_as_of"]}

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--input",required=True);p.add_argument("--output-dir",required=True)
    a=p.parse_args()
    stats=export_snapshot(a.input,a.output_dir)
    print("FINANCE_SITE_DATA_OK records="+str(stats["records"])+" snapshot="+stats["snapshot"]+" sha256="+stats["archive_sha256"])
