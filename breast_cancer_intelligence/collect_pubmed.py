"""PubMed metadata pilot: explicit live opt-in; no fabricated records."""
import argparse
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
QUERY = '("Breast Neoplasms"[MeSH Terms] OR breast cancer[Title/Abstract]) AND ("last 7 days"[Date - Publication])'

def build_url(retmax=20):
    return BASE + "?" + urllib.parse.urlencode({"db":"pubmed","term":QUERY,"retmode":"json","retmax":retmax,"sort":"pub date"})

def fetch():
    req=urllib.request.Request(build_url(),headers={"User-Agent":"BreastCancerIntelligenceResearch/0.1"})
    with urllib.request.urlopen(req,timeout=30) as r:
        obj=json.load(r)
    ids=obj.get("esearchresult",{}).get("idlist",[])
    return {"retrieved_at":datetime.now(timezone.utc).isoformat(),"source":"PubMed E-utilities","query":QUERY,"pmids":ids,"status":"unreviewed"}

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--dry-run",action="store_true")
    a=p.parse_args()
    print(json.dumps({"mode":"dry_run","url":build_url()} if a.dry_run else fetch(),ensure_ascii=False,indent=2))
