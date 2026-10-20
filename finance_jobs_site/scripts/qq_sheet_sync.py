#!/usr/bin/env python3
"""Read consented Tencent Docs export archived in PRIVATE Google Drive.

The QQ Docs browser page is view-only and does not provide bulk rows to this
GitHub runner. This importer NEVER imitates a logged-in QQ session or bypasses
the owner's export restrictions. Source row data are archival leads, not verified
employer vacancies. Only non-expired, plausible finance-career rows qualify.
"""
from __future__ import annotations
import argparse,csv,datetime,hashlib,io,json,pathlib,re,zipfile
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

ROOT=pathlib.Path(__file__).resolve().parents[1]
SOURCE="https://docs.qq.com/sheet/DS210WHFxVXdHcWJS?tab=BB08J2"
DRIVE_FOLDER="10iZsEMdHB_YvK3rZNsrih6DC7hntpihK"
XLSX_NS="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL="http://schemas.openxmlformats.org/package/2006/relationships"
MIME_FILE_TYPES={"text/csv",".csv","application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",".xlsx"}
COMPANY=re.compile(r"公司|单位|企业|银行|机构|雇主|单位名称|企业名称")
ROLE=re.compile(r"岗位|职位|招聘方向|招聘专业|岗位名称|需求专业|职位名称")
CITY=re.compile(r"地区|城市|地点|工作地|区域|工作地点|省份")
DATE=re.compile(r"截止|结束日期|截止时间|网申时间|报名结束")
LINK=re.compile(r"链接|原文|官网|报名|申请|投递|详情")
FINANCE=re.compile(r"财务|财会|会计|审计|税务|税收|预算|核算|资金管理|经营分析|财务BP|内控|风控|银行|金融|财经|经管|投融资|成本管理|经济")
NOT_SUITABLE=re.compile(r"仅限电气|仅招电工|限电气|只招电气|限境内高校|仅限国内院校|博士专属|仅招博士")
REGION=re.compile(r"上海|江苏|南京|苏州|无锡|常州|南通|镇江|扬州|泰州|盐城|徐州|连云港|宿迁|淮安|浙江|杭州|宁波|嘉兴|湖州|绍兴|金华|温州|台州|衢州|舟山|丽水|深圳|广东深圳|安徽|马鞍山|合肥|芜湖|滁州|铜陵|巢湖|全国")
DIRECT=re.compile(r"财务|会计|审计|税务|预算|核算|内控|经营分析|资金管理|财会")
DATE_MATCH=re.compile(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})")
ALLOWED_SHEETS={"总表","央国企","外企"}
MAX_BYTES=12_000_000
MAX_ROWS=12000
MAX_LEADS=300

def safe_http(url):
    try:
        u=urlparse(str(url))
        return u.scheme=="https" and bool(u.hostname) and not u.username and not u.password
    except Exception:return False

def normalized_date(raw):
    if not raw:return None
    m=DATE_MATCH.search(str(raw))
    if not m:return None
    try:return datetime.date(*map(int,m.groups())).isoformat()
    except ValueError:return None

def cleanup(raw,limit=260):
    return re.sub(r"\s+"," ",str(raw or "")).strip()[:limit]

def csv_rows(raw):
    try:text=raw.decode("utf-8-sig")
    except UnicodeDecodeError:text=raw.decode("gb18030")
    rows=list(csv.reader(io.StringIO(text)))
    if len(rows)>MAX_ROWS:raise ValueError("too many source rows")
    return [("授权导出CSV",rows,{})]

def col_id(s):
    out=0
    for c in s.upper():
        if not "A"<=c<="Z":break
        out=out*26+ord(c)-64
    return out-1

def xlsx_rows(raw):
    if len(raw)>MAX_BYTES:raise ValueError("export too large")
    output=[]
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names=z.namelist()
        if len(names)>2000:raise ValueError("too many workbook components")
        for inf in z.infolist():
            if inf.file_size>MAX_BYTES or inf.filename.startswith("/") or ".." in inf.filename.split("/"):
                raise ValueError("invalid or huge workbook component")
        shared=[]
        if "xl/sharedStrings.xml" in names:
            root=ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall(f"{{{XLSX_NS}}}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{XLSX_NS}}}t")))
        wb=ET.fromstring(z.read("xl/workbook.xml"))
        rels=ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        targets={r.attrib.get("Id"):r.attrib.get("Target") for r in rels}
        for sh in wb.iter(f"{{{XLSX_NS}}}sheet"):
            name=sh.attrib.get("name","")
            if name not in ALLOWED_SHEETS:continue
            rid=sh.attrib.get(f"{{{REL_NS}}}id")
            target=targets.get(rid,"")
            if not target:continue
            path=("xl/"+target.lstrip("/")).replace("xl/xl/","xl/")
            if path not in names:continue
            doc=ET.fromstring(z.read(path))
            rowdata=[];linkmap={}
            relpath="/".join(path.split("/")[:-1])+"/_rels/"+path.split("/")[-1]+".rels"
            external={}
            if relpath in names:
                rr=ET.fromstring(z.read(relpath))
                external={r.attrib.get("Id"):r.attrib.get("Target") for r in rr if r.attrib.get("TargetMode")=="External"}
            for l in doc.iter(f"{{{XLSX_NS}}}hyperlink"):
                cell=l.attrib.get("ref","")
                rid=l.attrib.get(f"{{{REL_NS}}}id")
                url=external.get(rid,l.attrib.get("location",""))
                if safe_http(url):linkmap[cell]=url
            for row in doc.iter(f"{{{XLSX_NS}}}row"):
                cells={}
                for c in row.findall(f"{{{XLSX_NS}}}c"):
                    ref=c.attrib.get("r","")
                    col=re.match(r"[A-Z]+",ref)
                    if not col:continue
                    idx=col_id(col.group())
                    if idx<0 or idx>80:continue
                    kind=c.attrib.get("t")
                    v=c.find(f"{{{XLSX_NS}}}v")
                    inline=c.find(f"{{{XLSX_NS}}}is")
                    value=v.text if v is not None and v.text else ""
                    if kind=="s" and value.isdigit() and int(value)<len(shared):
                        value=shared[int(value)]
                    elif kind=="inlineStr" and inline is not None:
                        value="".join(t.text or "" for t in inline.iter(f"{{{XLSX_NS}}}t"))
                    cells[idx]=cleanup(value,400)
                if cells:
                    highest=min(max(cells)+1,81)
                    rowdata.append([cells.get(i,"") for i in range(highest)])
                if len(rowdata)>MAX_ROWS:raise ValueError("too many data rows")
            output.append((name,rowdata,linkmap))
    return output

def header_idx(row):
    keys={"company":COMPANY,"role":ROLE,"city":CITY,"deadline":DATE,"link":LINK}
    result={}
    for idx,col in enumerate(row):
        for name,regex in keys.items():
            if name not in result and regex.search(str(col)):result[name]=idx
    return result

def parse_leads(sheets,now=None):
    now=now or (datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).date())
    result=[];seen=set();checked=0;reason_counts={}
    def skip(reason):reason_counts[reason]=reason_counts.get(reason,0)+1
    for sheet,rows,hyperlinks in sheets:
        if sheet not in ALLOWED_SHEETS and sheet!="授权导出CSV":continue
        hdr=None
        for ridx,row in enumerate(rows):
            if ridx>MAX_ROWS:break
            keys=header_idx(row)
            if ("company" in keys and "role" in keys) or (len(keys)>=3 and "role" in keys):
                hdr=(ridx,keys);break
        if hdr is None:
            skip("unrecognized_header");continue
        start,cols=hdr
        for rowno,row in enumerate(rows[start+1:],start+2):
            checked+=1
            def value(field):
                i=cols.get(field)
                return cleanup(row[i]) if i is not None and i<len(row) else ""
            company=value("company");role=value("role");city=value("city")
            if not company or not role:skip("missing_company_or_role");continue
            combined=cleanup(" ".join(row),900)
            if not FINANCE.search(role+" "+combined):skip("unrelated_to_finance");continue
            if NOT_SUITABLE.search(combined):skip("ineligible_explicit");continue
            if city and not REGION.search(city) and "全国" not in combined:
                skip("outside_target_regions");continue
            deadline=normalized_date(value("deadline"))
            if deadline and deadline<now.isoformat():skip("expired");continue
            # An export cell link is a lead only; raw employers' URLs are not treated as verified vacancies.
            link=value("link")
            if not safe_http(link):link=""
            for i,col in enumerate(row):
                if link:break
                if safe_http(col) and re.search(r"招聘|job|career|campus|zhaopin",str(col),re.I):link=cleanup(col,400)
            if not link:
                # Cell hyperlinks may be captured by the xlsx export but aren't a prerequisite for a lead.
                for col,header in cols.items():
                    pass
                for ref,url in hyperlinks.items():
                    m=re.match(r"([A-Z]+)(\d+)$",ref)
                    if m and int(m.group(2))==rowno and safe_http(url):
                        link=url;break
            fingerprint=hashlib.sha256((company+"|"+role+"|"+city+"|"+link).encode()).hexdigest()[:16]
            if fingerprint in seen:skip("duplicate");continue
            seen.add(fingerprint)
            reasons=["匹配财会/审计/银行金融相关方向"]
            if REGION.search(city):reasons.append("工作地属于关注地区")
            if DIRECT.search(role):reasons.append("职位名称包含财会相关关键词")
            if deadline:reasons.append("截止日期未过（依据第三方表格，官网待核）")
            result.append({
              "id":"qq-"+fingerprint,"title":role[:150],"company":company[:120],
              "work_city":city or "表格未明确具体城市","deadline":deadline,
              "source_sheet":sheet,"source_row":rowno,"third_party_url":SOURCE,
              "original_link":link or None,"match_reasons":reasons,
              "review_state":"未核实官方招聘资格与开放状态",
              "role_confirmed":False,"permission":"authorized-export-only",
              "evidence":"腾讯文档授权导出；来源真实性需向用人单位确认"
            })
            if len(result)>=MAX_LEADS:break
        if len(result)>=MAX_LEADS:break
    return result,{"rows_examined":checked,"matched":len(result),"skipped":reason_counts}

def drive_export():
    from publish_drive import auth,API
    with auth() as s:
        q=f"'{DRIVE_FOLDER}' in parents and trashed = false"
        r=s.get(API,params={"q":q,"fields":"nextPageToken,files(id,name,mimeType,size,modifiedTime)",
            "pageSize":100,"orderBy":"modifiedTime desc"},timeout=30)
        if r.status_code!=200:raise RuntimeError("Cannot list private QQ export folder")
        files=r.json().get("files",[])
        choices=[v for v in files if str(v.get("name","")).lower().endswith((".csv",".xlsx"))
          and int(v.get("size") or 0)<=MAX_BYTES and int(v.get("size") or 0)>50]
        if not choices:return None
        latest=max(choices,key=lambda x:x.get("modifiedTime") or "")
        response=s.get(API+"/"+latest["id"],params={"alt":"media"},timeout=80)
        if response.status_code!=200 or len(response.content)>MAX_BYTES:
            raise RuntimeError("QQ export incomplete or oversized")
        return {"name":latest["name"],"modified_at":latest.get("modifiedTime"),
            "sha256":hashlib.sha256(response.content).hexdigest(),"bytes":response.content}

def run(out_path):
    stamp=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    result={"schema":1,"source_id":"qq_2027_master","source_url":SOURCE,
      "checked_at_utc":stamp,"status":"export_required","raw_download_permitted":False,
      "source":"Private Google Drive import folder, not Tencent live cells",
      "private_export_folder_id":DRIVE_FOLDER,"rows_examined":0,"qualified_count":0,
      "leads":[],"skip_reasons":{},
      "warning":"腾讯文档只能查看不能保证允许导出，未读取单元格前绝不声称自动同步或岗位匹配。"}
    try:
        export=drive_export()
        if export:
            name=export["name"]
            sheets=csv_rows(export["bytes"]) if name.lower().endswith(".csv") else xlsx_rows(export["bytes"])
            leads,stats=parse_leads(sheets)
            result.update(status="authorized_export_imported",raw_download_permitted=True,
                archived_name=name,archived_sha256=export["sha256"],
                archived_modified_at=export["modified_at"],
                rows_examined=stats["rows_examined"],qualified_count=stats["matched"],
                leads=leads,skip_reasons=stats["skipped"],
                warning="已按过期、专业、地区筛选已授权的表格副本；未人工核验的线索不是正式财会岗位。")
    except Exception as exc:
        result["status"]="import_error"
        result["warning"]="云盘导出归档无法自动读取/解析，未导入岗位；错误类型："+type(exc).__name__
    out=pathlib.Path(out_path);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n","utf-8")
    print("QQ_RECRUITMENT_IMPORT status="+result["status"]+
          " matched="+str(result["qualified_count"])+" examined="+str(result["rows_examined"]))
    return result

if __name__=="__main__":
    a=argparse.ArgumentParser();a.add_argument("--output",required=True)
    run(a.parse_args().output)
