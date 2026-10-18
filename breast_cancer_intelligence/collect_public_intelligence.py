"""Collect public breast-cancer research intelligence as timestamped evidence snapshots.
Data are research-only: no patient records or implied clinical recommendations.
"""
from __future__ import annotations
import argparse
import hashlib
import html.parser
import io
import json
import os
from pathlib import Path
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import xml.etree.ElementTree as ET
import zipfile

BASE = Path(__file__).resolve().parent
NCBI = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
CT = "https://clinicaltrials.gov/api/v2/studies"
FDA_INDEX = "https://www.fda.gov/drugs/resources-information-approved-drugs/oncology-cancerhematologic-malignancies-approval-notifications"
MONITOR = [
    {"id":"fda_oncology_approvals", "section":"regulatory", "url":FDA_INDEX, "type":"regulator_approval_index"},
    {"id":"nci_breast_treatment", "section":"guidelines", "url":"https://www.cancer.gov/types/breast/hp/breast-treatment-pdq", "type":"reviewed_evidence_summary_not_guideline"},
    {"id":"nci_breast_prevention", "section":"guidelines", "url":"https://www.cancer.gov/types/breast/hp/breast-prevention-pdq", "type":"reviewed_evidence_summary_not_guideline"},
    {"id":"nci_follow_up", "section":"rehabilitation", "url":"https://www.cancer.gov/about-cancer/coping/survivorship/follow-up-care", "type":"patient_education_not_personal_medical_advice"},
    {"id":"caca_2026_breast_guideline", "section":"guidelines", "url":"https://www.china-oncology.com/zh/article/doi/10.19401/j.cnki.1007-3639.2025.12.009/", "type":"clinical_guideline_2026"},
    {"id":"esmo_guideline_news", "section":"guidelines", "url":"https://www.esmo.org/guidelines/guidelines-news", "type":"society_guideline_index"}
]
TOPICS = {
    "recent_articles": {"term": '("Breast Neoplasms"[MeSH Terms] OR "breast cancer"[Title/Abstract])', "days":30, "limit":45},
    "case_reports": {"term": '("breast cancer"[Title/Abstract] OR "Breast Neoplasms"[MeSH Terms]) AND "Case Reports"[Publication Type]', "days":365, "limit":20},
    "survivorship_reviews": {"term": '("breast cancer"[Title/Abstract] OR "Breast Neoplasms"[MeSH Terms]) AND (survivorship[Title/Abstract] OR rehabilitation[Title/Abstract] OR lymphedema[Title/Abstract] OR "follow-up care"[Title/Abstract]) AND ("Systematic Review"[Publication Type] OR "Review"[Publication Type])', "days":365, "limit":25},
}
HEADERS = {"User-Agent": "BreastCancerIntelligenceResearch/0.2 (public metadata academic pilot)", "Accept": "application/json, application/xml, text/html;q=0.8"}
def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def request(url: str, limit=6_000_000):
    last = None
    for trial in range(3):
        try:
            with urlopen(Request(url, headers=HEADERS), timeout=35) as resp:
                data = resp.read(limit + 1)
                if len(data) > limit:
                    raise ValueError("Response exceeded size guard")
                return data, {"http_status":resp.status, "sha256":sha(data), "byte_count":len(data), "etag":resp.headers.get("ETag"), "last_modified":resp.headers.get("Last-Modified")}
        except (HTTPError, URLError, TimeoutError) as exc:
            last = exc
            if isinstance(exc, HTTPError) and exc.code not in (429,500,502,503,504):
                break
            time.sleep(1 + trial * 2)
    raise RuntimeError("Source request failed: " + type(last).__name__ + " " + str(getattr(last, "code", "")))

def text_at(element, path):
    found = element.find(path)
    return "".join(found.itertext()).strip() if found is not None else None

def articles_xml(raw: bytes, ids: list[str], topic: str, fetched_at: str):
    root = ET.fromstring(raw)
    records=[]
    for article in root.findall(".//PubmedArticle"):
        pmid = text_at(article,"./MedlineCitation/PMID")
        if not pmid: continue
        journal=text_at(article,"./MedlineCitation/Article/Journal/Title")
        title=text_at(article,"./MedlineCitation/Article/ArticleTitle")
        abst=article.findall("./MedlineCitation/Article/Abstract/AbstractText")
        pieces=[]
        for a in abst:
            string="".join(a.itertext()).strip()
            if string:
                label=a.attrib.get("Label")
                pieces.append((label + ": " if label else "") + string)
        article_ids = article.findall("./PubmedData/ArticleIdList/ArticleId")
        doi=next((a.text for a in article_ids if a.attrib.get("IdType")=="doi" and a.text),None)
        pub_types=[x.text for x in article.findall("./MedlineCitation/Article/PublicationTypeList/PublicationType") if x.text]
        year=text_at(article,"./MedlineCitation/Article/Journal/JournalIssue/PubDate/Year")
        medline_date=text_at(article,"./MedlineCitation/Article/Journal/JournalIssue/PubDate/MedlineDate")
        records.append({"pmid":pmid,"title":title,"journal":journal,"published_year":year,
            "published_medline_date":medline_date,"doi":doi,"publication_types":pub_types,
            "abstract_for_internal_research_only":"\n".join(pieces) if topic != "case_reports" else None,
            "abstract_reproduced": topic != "case_reports" and bool(pieces),
            "source_url":"https://pubmed.ncbi.nlm.nih.gov/"+pmid+"/",
            "topic":topic,"fetched_at_utc":fetched_at,"review_status":"unreviewed",
            "rights_note":"Research-only index; original abstract may be copyrighted; do not redistribute without license."})
    expected=set(ids); actual={x["pmid"] for x in records}
    missing = sorted(expected - actual)
    if len(missing) > max(2, len(ids)//5):
        raise ValueError("EFetch coverage insufficient; missing="+str(missing[:10]))
    return records, missing

def collect_pubmed(topic, spec, timestamp):
    base_params={"db":"pubmed","retmode":"json","term":spec["term"],"retmax":spec["limit"],
       "sort":"pub date","datetype":"edat","reldate":spec["days"],"tool":"BreastCancerIntelligenceResearch"}
    url=NCBI+"esearch.fcgi?"+urlencode(base_params)
    raw, proof=request(url)
    obj=json.loads(raw)
    result=obj.get("esearchresult",{})
    ids=result.get("idlist",[])
    if not isinstance(ids,list): raise ValueError("PubMed esearch malformed")
    count=int(result.get("count",0))
    meta={"source":"NCBI PubMed E-utilities","search_url":url, "search_sha256":proof["sha256"],
       "total_matching":count,"returned":len(ids),"days_back":spec["days"],"limited_sample":True}
    if count and not ids: raise ValueError("PubMed returned zero IDs while count positive")
    if not ids: return [],meta
    time.sleep(0.45)  # stay well under NCBI 3 requests/second
    fetch_url=NCBI+"efetch.fcgi?"+urlencode({"db":"pubmed","id":",".join(ids),"retmode":"xml",
         "tool":"BreastCancerIntelligenceResearch"})
    fetched, p2=request(fetch_url,limit=9_000_000)
    items, missing=articles_xml(fetched,ids,topic,timestamp)
    meta["unresolved_pmids"]=missing
    meta["fetch_coverage"]=round(len(items)/len(ids),4)
    meta["efetch_sha256"]=p2["sha256"]
    meta["efetch_bytes"]=p2["byte_count"]
    return items,meta

def collect_trials(timestamp,limit=75):
    params={"query.cond":"Breast Cancer", "sort":"LastUpdatePostDate:desc",
        "pageSize":limit,"countTotal":"true","format":"json"}
    url=CT+"?"+urlencode(params)
    raw,proof=request(url,limit=9_000_000)
    obj=json.loads(raw)
    studies=obj.get("studies",[])
    if not studies: raise ValueError("No clinical trial records")
    items=[]
    for study in studies:
        p=study.get("protocolSection",{})
        ident=p.get("identificationModule",{})
        status=p.get("statusModule",{})
        design=p.get("designModule",{})
        cond=p.get("conditionsModule",{})
        elig=p.get("eligibilityModule",{})
        contacts=p.get("contactsLocationsModule",{})
        nct=ident.get("nctId")
        if not nct or not re.fullmatch(r"NCT\d{8}",nct):
            raise ValueError("Bad ClinicalTrials.gov NCT id")
        sites=contacts.get("locations",[]) or []
        items.append({"nct_id":nct,"title":ident.get("briefTitle"),
            "last_update_posted":(status.get("lastUpdatePostDateStruct") or {}).get("date"),
            "status":status.get("overallStatus"),"phases":design.get("phases",[]),
            "study_type":design.get("studyType"),
            "enrollment":design.get("enrollmentInfo"),
            "conditions":cond.get("conditions",[]),
            "sex_eligibility":elig.get("sex"),
            "sites_count_in_registry":len(sites),
            "source_url":"https://clinicaltrials.gov/study/"+nct,
            "record_sha256":sha(json.dumps(study,sort_keys=True,ensure_ascii=False).encode()),
            "fetched_at_utc":timestamp,"review_status":"unreviewed",
            "scope_note":"Registry status only; not proof of efficacy or local treatment availability."})
    if len({i["nct_id"] for i in items}) != len(items): raise ValueError("Repeated NCTs")
    return items,{"source":"ClinicalTrials.gov v2","query_url":url,
        "total_matching":obj.get("totalCount"),"returned":len(items),"raw_sha256":proof["sha256"],
        "limited_sample":True,"next_page_exists":bool(obj.get("nextPageToken"))}

class Anchors(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.href=None; self.text=[]; self.entries=[]
    def handle_starttag(self,tag,attrs):
        if tag=="a":
            self.href=dict(attrs).get("href")
            self.text=[]
    def handle_data(self,data):
        if self.href is not None: self.text.append(data)
    def handle_endtag(self,tag):
        if tag=="a" and self.href is not None:
            label=" ".join("".join(self.text).split())
            if "breast cancer" in label.lower() and ("approv" in label.lower()):
                self.entries.append((label,urljoin(FDA_INDEX,self.href)))
            self.href=None; self.text=[]

def official_sites(timestamp):
    checks=[]; approvals=[]; errors=[]
    for item in MONITOR:
        entry={**item,"fetched_at_utc":timestamp}
        try:
            raw, proof=request(item["url"],limit=5_000_000)
            entry.update(proof)
            entry["status"]="verified_http_response"
            if item["id"]=="fda_oncology_approvals":
                parser=Anchors()
                parser.feed(raw.decode("utf-8",errors="replace"))
                seen=set()
                for label,link in parser.entries:
                    if link in seen: continue
                    seen.add(link)
                    approvals.append({"headline":label,"official_url":link,
                       "source_index":FDA_INDEX,"fetched_at_utc":timestamp,
                       "date_of_approval":None,"geography":"US FDA",
                       "review_status":"official_link_identified_indication_pending_review",
                       "warning":"Do not equate FDA approval with NMPA China approval or individual suitability."})
                entry["matched_breast_approval_links"]=len(approvals)
            checks.append(entry)
        except Exception as exc:
            entry["status"]="fetch_failed"
            entry["error_class"]=type(exc).__name__
            checks.append(entry)
            errors.append(item["id"])
    return approvals,checks,errors

def dump_jsonl(path,items):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("w",encoding="utf-8",newline="\n") as f:
        for item in items: f.write(json.dumps(item,sort_keys=True,ensure_ascii=False)+"\n")
    return {"path":str(path),"count":len(items),"bytes":path.stat().st_size,"sha256":sha(path.read_bytes())}

def run(base_folder):
    now=datetime.now(timezone.utc)
    ts=now.isoformat()
    name=now.strftime("%Y%m%dT%H%M%SZ")+"_"+os.getenv("GITHUB_RUN_ID","manual")
    out=Path(base_folder)/name
    if out.exists(): raise FileExistsError("Refusing to overwrite previous snapshot")
    out.mkdir(parents=True)
    all_files=[]; sources={}; failures={}
    for topic,spec in TOPICS.items():
        try:
            rec,meta=collect_pubmed(topic,spec,ts)
            if not rec: raise ValueError("No PubMed articles in query time window")
            all_files.append(dump_jsonl(out/"pubmed"/(topic+".jsonl"),rec))
            sources["pubmed_"+topic]=meta
        except Exception as exc:
            failures["pubmed_"+topic]=type(exc).__name__ + ": " + str(exc)[:160]
    try:
        rec,meta=collect_trials(ts)
        all_files.append(dump_jsonl(out/"clinical_trials"/"recently_updated.jsonl",rec))
        sources["clinical_trials"]=meta
    except Exception as exc:
        failures["clinical_trials"]=type(exc).__name__ + ": "+str(exc)[:160]
    approvals,checks,errors=official_sites(ts)
    all_files.append(dump_jsonl(out/"official_sources"/"source_checks.jsonl",checks))
    all_files.append(dump_jsonl(out/"official_sources"/"fda_breast_approval_links.jsonl",approvals))
    check_map={x["id"]:x for x in checks}
    guidelines=[{"id":"caca_2026_breast_guideline","title":"中国抗癌协会乳腺癌诊治指南与规范（2026年版）","issued_by":"中国抗癌协会乳腺癌专业委员会；中华医学会肿瘤学分会乳腺肿瘤学组","published_in":"中国癌症杂志 2025;35(12):1157-1255","doi":"10.19401/j.cnki.1007-3639.2025.12.009","url":"https://www.china-oncology.com/zh/article/doi/10.19401/j.cnki.1007-3639.2025.12.009/","reference_kind":"published_guideline_bibliographic_metadata","fetched_at_utc":ts,"source_http_status":check_map.get("caca_2026_breast_guideline",{}).get("status"),"note":"Record of publication only; no full PDF copied, no clinical interpretation"}]
    all_files.append(dump_jsonl(out/"guidelines"/"verified_publication_index.jsonl",guidelines))
    if not approvals: failures["fda_breast_approval_links"]="No verified breast approval links; do not classify approval results."
    for source in errors: failures["source_"+source]="HTTP check failed"
    required=("pubmed_recent_articles","clinical_trials")
    status="pass_core" if all(x in sources for x in required) else "failed_core"
    manifest={"schema_version":1,"snapshot_id":name,"captured_at_utc":ts,"status":status,
        "sources":sources,"files":all_files,"failures":failures,
        "rights":"Research-only metadata. No open license is assumed for article abstracts.",
        "medical_safety":"Unreviewed research data, no medical recommendations or hospital quality ranking.",
        "excluded":["hospital_patient_volumes_unverified","physician_skill_rankings_unverified","identifiable_patient_cases"],
        "scope":"Recent limited sample only, NOT complete historical coverage"}
    content=json.dumps(manifest,ensure_ascii=False,sort_keys=True,indent=2)+"\n"
    (out/"manifest.json").write_text(content,encoding="utf-8")
    archive=Path(base_folder)/(name+".zip")
    with zipfile.ZipFile(archive,"w",compression=zipfile.ZIP_DEFLATED) as z:
        for path in sorted(out.rglob("*")):
            if path.is_file(): z.write(path,path.relative_to(out))
    manifest["archive_path"]=str(archive)
    manifest["archive_sha256"]=sha(archive.read_bytes())
    print(json.dumps(manifest,ensure_ascii=False,indent=2))
    if status != "pass_core": raise SystemExit(2)
    return manifest

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--out",default=str(BASE/"data_snapshots"))
    args=p.parse_args()
    run(args.out)
