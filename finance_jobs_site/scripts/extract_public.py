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
    ledger=data.get("change_log")
    if not isinstance(ledger,dict) or not isinstance(ledger.get("events"),list):
        raise ValueError("Missing audit change journal from the previous Google Drive snapshot")
    for item in data["listings"]:
        if item["qs_threshold"] is not None and not item.get("qs_details"):
            raise ValueError("Invalid QS evidence")
    publicdir=pathlib.Path(publicdir);publicdir.mkdir(parents=True,exist_ok=True)
    qq=data.get("qq_sheet")
    if not isinstance(qq,dict) or qq.get("source_id")!="qq_2027_master":
        raise ValueError("Missing truthful prioritized Tencent Docs source status")
    if len(qq.get("leads",[]))>300:
        raise ValueError("Third-party candidate list too large")
    sanitized={
        "schema":1,"source_url":qq.get("source_url"),
        "checked_at_utc":qq.get("checked_at_utc"),
        "status":qq.get("status"),"qualified_count":qq.get("qualified_count",0),
        "rows_examined":qq.get("rows_examined",0),
        "leads":qq.get("leads",[]),
        "warning":qq.get("warning","第三方招聘线索，必须核实原文")
    }
    exposed={k:v for k,v in data.items() if k not in ("source_monitor","qq_sheet")}
    (publicdir/"latest.json").write_text(json.dumps(exposed,ensure_ascii=False,indent=2)+"\n","utf-8")
    (publicdir/"monitor.json").write_text(json.dumps(monitor,ensure_ascii=False,indent=2)+"\n","utf-8")
    (publicdir/"changes.json").write_text(json.dumps(ledger,ensure_ascii=False,indent=2)+"\n","utf-8")
    (publicdir/"qq_feed.json").write_text(json.dumps(sanitized,ensure_ascii=False,indent=2)+"\n","utf-8")
    return {"archive_sha256":hashlib.sha256(raw).hexdigest(),
        "records":len(data["listings"]),"sources_checked":len(monitor["sources"]),
        "changes":ledger["total_events"],"qq_imported":sanitized["qualified_count"],
        "snapshot":data["collected_at"],"data_as_of":data["data_as_of"]}

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--input",required=True);p.add_argument("--output-dir",required=True)
    a=p.parse_args()
    stats=export_snapshot(a.input,a.output_dir)
    print("FINANCE_SITE_DATA_OK records="+str(stats["records"])+" changes="+str(stats["changes"])+" qq_imported="+str(stats["qq_imported"])+" snapshot="+stats["snapshot"]+" sha256="+stats["archive_sha256"])
