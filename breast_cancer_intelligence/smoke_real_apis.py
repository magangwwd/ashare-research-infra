"""Network smoke test for public research APIs. No patient data and no credentials."""
import hashlib
import json
import pathlib
import sys
import urllib.request
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from collect_pubmed import build_url as pubmed_url
from collect_trials import build_url as trials_url

def read_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "BreastCancerIntelligenceResearch/0.1", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as response:
        raw = response.read(2_000_001)
        status = response.status
    if len(raw) > 2_000_000:
        raise ValueError("API response exceeds 2 MB test limit")
    return json.loads(raw), {"http_status": status, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}

def main():
    report = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "scope": "live_public_api_smoke"}
    try:
        payload, proof = read_json(pubmed_url(retmax=3))
        ids = payload.get("esearchresult", {}).get("idlist")
        if not isinstance(ids, list) or not ids:
            raise ValueError("PubMed: no usable PMID list in response")
        report["pubmed"] = {**proof, "pmid_samples": ids[:3], "result": "pass"}
        payload, proof = read_json(trials_url(page_size=3))
        studies = payload.get("studies")
        if not isinstance(studies, list) or not studies:
            raise ValueError("ClinicalTrials.gov: no study list in response")
        ncts = [s.get("protocolSection", {}).get("identificationModule", {}).get("nctId") for s in studies]
        if not all(ncts):
            raise ValueError("ClinicalTrials.gov: missing NCT identifier")
        report["clinical_trials"] = {**proof, "nct_samples": ncts[:3], "result": "pass"}
        report["result"] = "pass"
    except Exception as exc:
        report["result"] = "fail"
        report["error"] = type(exc).__name__ + ": " + str(exc)[:500]
        raise
    finally:
        path = ROOT / "smoke_report.json"
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
