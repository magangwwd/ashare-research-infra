#!/usr/bin/env python3
"""Create daily immutable public finance careers snapshot from curated and monitored sources."""
from __future__ import annotations
import argparse,datetime,hashlib,json,os,pathlib
from update_sources import validate_curated


WATCH_FIELDS = (
    "title","company","type","regions","roles","deadline","published",
    "work_city","graduate_batch","min_degree","majors_exact","vacancies",
    "application_open","application_deadline_time","assessment",
    "overseas","overseas_details","qs_threshold","qs_details",
    "role_confirmed","posting_level","source_url","apply_url","critical"
)

def change_ledger(current, previous, current_scan, timestamp):
    """Compare independently captured snapshots, never claim a source diff is a new vacancy."""
    last=(previous or {}).get("listings",[])
    prior={x["id"]:x for x in last if isinstance(x,dict) and x.get("id")}
    fresh={x["id"]:x for x in current}
    events=[]
    for job_id,job in fresh.items():
        if job_id not in prior:
            events.append({"kind":"new_curated_record","id":job_id,"title":job["title"],
                           "source":job["source_url"],"verified_role":bool(job.get("role_confirmed")),
                           "note":"新入库档案；不代表岗位是当天新发布，也不代表仍可投递"})
            continue
        changed=[k for k in WATCH_FIELDS if job.get(k)!=prior[job_id].get(k)]
        if changed:
            events.append({"kind":"curated_field_change","id":job_id,"title":job["title"],
                           "changed_fields":changed,"source":job["source_url"],
                           "note":"人工维护的招聘字段发生变化；需以原始招聘公告逐项核实"})
    for job_id,job in prior.items():
        if job_id not in fresh:
            events.append({"kind":"removed_from_directory","id":job_id,"title":job.get("title"),
                           "note":"档案不再显示，不能仅凭删除推断职位撤回"})
    old_monitor=(previous or {}).get("source_monitor") or {}
    old_src={x["id"]:x for x in old_monitor.get("sources",[]) if x.get("id")}
    for src in current_scan.get("sources",[]):
        prior_scan=old_src.get(src["id"])
        if not prior_scan:continue
        if prior_scan.get("digest") and src.get("digest") and prior_scan["digest"]!=src["digest"]:
            events.append({"kind":"official_page_changed_review_needed","id":src["id"],
                           "title":src.get("name"),"source":src["url"],
                           "note":"网页内容指纹变化，可能是页面动态内容；不是岗位变更或正在报名的证据"})
        elif prior_scan.get("http_status") != src.get("http_status"):
            events.append({"kind":"source_access_status_changed","id":src["id"],
                           "title":src.get("name"),"source":src["url"],
                           "previous_http_status":prior_scan.get("http_status"),
                           "current_http_status":src.get("http_status"),
                           "note":"来源访问状态变化；不能据此认定招聘已撤回"})
    old_urls={x.get("url") for x in old_monitor.get("potential_new_announcements",[])}
    for lead in current_scan.get("potential_new_announcements",[]):
        if lead.get("url") and lead["url"] not in old_urls:
            events.append({"kind":"unreviewed_link_discovered","id":lead.get("source_id"),
                           "title":lead.get("title"),"source":lead["url"],
                           "note":"自动发现的链接未经岗位、专业、地点、截止日人工核实"})
    counts={}
    for row in events:counts[row["kind"]]=counts.get(row["kind"],0)+1
    return {"compared_at_utc":timestamp.isoformat().replace("+00:00","Z"),
            "previous_collected_at":(previous or {}).get("collected_at"),
            "baseline":previous is None,
            "events":events[:500],"counts":counts,"total_events":len(events),
            "disclaimer":"仅有curated_field_change表明入库字段被修改；网页哈希变化和新抓取链接都是待核对线索，绝不自动确认为开放岗位。"}

def make(config,monitor,outdir,run_id,previous=None,qq_feed=None):
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
    qq=None
    if qq_feed:
        qq=json.loads(pathlib.Path(qq_feed).read_text("utf-8"))
        if qq.get("schema")!=1 or qq.get("source_id")!="qq_2027_master":
            raise ValueError("Invalid QQ recruitment source data")
        if len(qq.get("leads",[]))>300:raise ValueError("Too many third-party leads")
        for lead in qq.get("leads",[]):
            if lead.get("role_confirmed") or lead.get("review_state")!="未核实官方招聘资格与开放状态":
                raise ValueError("Unverified third-party job must remain unverified")
    earlier=None
    if previous:
        earlier=json.loads(pathlib.Path(previous).read_text("utf-8"))
        if earlier.get("schema")!=1 or not isinstance(earlier.get("listings"),list):
            raise ValueError("Previous Drive source snapshot is not a verified baseline")
    ledger=change_ledger(curated["listings"],earlier,m,now)
    snap=dict(curated)
    snap.update({
        "change_log":ledger,
        "qq_sheet":qq,
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
            "changes":ledger["total_events"],
            "sha256":hashlib.sha256(out.read_bytes()).hexdigest(),
            "listings":len(snap["listings"]),"as_of":today}

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--config",required=True);p.add_argument("--monitor",required=True)
    p.add_argument("--outdir",required=True);p.add_argument("--run-id",required=True)
    p.add_argument("--previous",default=None)
    p.add_argument("--qq-feed",default=None)
    opt=p.parse_args()
    proof=make(opt.config,opt.monitor,opt.outdir,opt.run_id,opt.previous,opt.qq_feed)
    print("FINANCE_SNAPSHOT_OK file="+proof["name"]+" records="+str(proof["listings"])+" changes="+str(proof["changes"])+" sha256="+proof["sha256"])
