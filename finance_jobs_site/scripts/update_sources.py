#!/usr/bin/env python3
"""Conservative daily review of official recruitment source pages.

Never infer a currently open vacancy from HTTP status or a portal headline.
Discovery results are *unverified leads*, NOT verified finance job vacancies.
"""
from __future__ import annotations
import argparse, concurrent.futures, datetime, hashlib, json, pathlib, re, sys
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

ROOT = pathlib.Path(__file__).resolve().parents[1]
MAX_BYTES = 2_000_000
TIMEOUT = (5, 11)
HEADLINE = re.compile(r"(2027|财务|会计|审计|金融|校招|校园招聘)")
RELEVANT = re.compile(r"(招聘|校招|校园|财务|会计|审计|金融)")
ALLOWED = {
    "zhaopin.sgcc.com.cn", "gwjsdl.zhaopin.com", "job2.ccb.cn", "job.ccb.com", "e1.ccb.com",
    "www.boc.cn", "job.icbc.com.cn", "www.chinapost.com.cn", "zhaopin.chng.com.cn",
    "wap.sasac.gov.cn", "jshrss.jiangsu.gov.cn", "rsj.sh.gov.cn",
    "hrss.sz.gov.cn", "jyw.mastc.edu.cn", "picc.zhiye.com", "www.psbc.com",
    "psbc2027.zhaopin.com", "campus.chinahr.com", "chinapost2027.zhaopin.com",
    "job.bankcomm.com", "zhcpa.zhiye.com"
}
def safe_url(url):
    try:
        u = urlparse(url)
        return u.scheme == "https" and u.hostname in ALLOWED and not u.username and not u.password
    except Exception:
        return False

def validate_curated(doc):
    assert doc["schema"] == 1 and len(doc["listings"]) >= 8 and len(doc["sources"]) >= 8
    ids=set()
    for p in doc["listings"]:
        assert p["id"] not in ids
        ids.add(p["id"])
        assert p["title"] and p["company"] and p["regions"] and p["roles"]
        assert safe_url(p["source_url"]) and safe_url(p["apply_url"]),p["id"]
        assert p["evidence"] and p["checked_at"]
        assert p["qs_threshold"] is None or isinstance(p["qs_threshold"],int)
        if p["deadline"]:datetime.date.fromisoformat(p["deadline"])
    for s in doc["sources"]:
        assert safe_url(s["url"]),s["id"]
        assert s["name"] and s["id"]
    return doc

def fetch_one(s):
    row={"id":s["id"],"name":s["name"],"url":s["url"],"type":s["type"],
         "status":"无法自动读取，请打开官网","http_status":None,"digest":None,
         "discovered_count":0}
    leads=[]
    try:
        with requests.Session() as session:
            response=session.get(s["url"],headers={"User-Agent":"FinanceCampusJobSourceMonitor/1.0 (public announcements only)"},
                timeout=TIMEOUT,stream=True,allow_redirects=True)
            row["http_status"]=response.status_code
            if not safe_url(response.url):
                row["status"]="重定向到未核实域名，跳过";return row,leads
            if response.status_code != 200:
                row["status"]="页面访问失败，请到官网核实";return row,leads
            if "html" not in response.headers.get("Content-Type","").lower():
                row["status"]="不是HTML页面，跳过自动识别";return row,leads
            content=b""
            for chunk in response.iter_content(65536):
                content+=chunk
                if len(content)>MAX_BYTES:
                    row["status"]="页面过大，跳过解析";return row,leads
            row["digest"]=hashlib.sha256(content).hexdigest()
            row["status"]="官网页面响应正常（不代表招聘仍开放）"
            if not s.get("discover"):return row,leads
            soup=BeautifulSoup(content,"html.parser")
            found=set()
            for anchor in soup.select("a[href]"):
                text=" ".join(anchor.stripped_strings)
                text=re.sub(r"\s+"," ",text).strip()
                if not 10<=len(text)<=160 or not HEADLINE.search(text) or not RELEVANT.search(text):
                    continue
                link=urljoin(response.url,anchor.get("href",""))
                if not safe_url(link) or link in found or link.rstrip("/") == response.url.rstrip("/"):
                    continue
                found.add(link)
                leads.append({"source_id":s["id"],"title":text[:160],"url":link,
                    "review_state":"待人工核对","role_confirmed":False,
                    "application_confirmed":False})
                if len(leads)>=8:break
            row["discovered_count"]=len(leads)
    except requests.RequestException:
        row["status"]="网络访问不稳定，无法确认"
    return row,leads

def build(config,out_path):
    doc=validate_curated(json.loads(pathlib.Path(config).read_text("utf-8")))
    now=datetime.datetime.now(datetime.timezone.utc)
    rows=[];leads=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        for r,new in pool.map(fetch_one,doc["sources"]):
            rows.append(r);leads.extend(new)
    unique=list({x["url"]:x for x in leads}.values())[:40]
    output={"schema":1,"checked_at":now.isoformat(timespec="seconds"),
        "today_beijing":(now+datetime.timedelta(hours=8)).date().isoformat(),
        "checked_sources":len(rows),
        "reachable_sources":sum(r["http_status"]==200 for r in rows),
        "sources":rows,"potential_new_announcements":unique,
        "warning":"公告线索未经逐岗位人工核实，页面HTTP状态不等于职位开放。官方岗位、留学生要求、报名截止与QS门槛以实际招聘公告为准。"}
    out=pathlib.Path(out_path);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(output,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("FINANCE_SOURCE_SCAN_DONE", "checked="+str(output["checked_sources"]),
          "reachable="+str(output["reachable_sources"]), "unreviewed_leads="+str(len(unique)))
    return output

if __name__=="__main__":
    cli=argparse.ArgumentParser()
    cli.add_argument("--config",default=str(ROOT/"data/opportunities.json"))
    cli.add_argument("--out",default=str(ROOT/"data/monitor.json"))
    args=cli.parse_args()
    build(args.config,args.out)
