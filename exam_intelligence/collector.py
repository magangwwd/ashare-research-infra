"""Conservative source snapshotter: disabled sources never generate synthetic data."""
import argparse
import hashlib
import json
import pathlib
import urllib.request
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent

def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def run(dry_run=False):
    config = json.loads((ROOT / "config" / "sources.json").read_text(encoding="utf-8"))
    enabled = [s for s in config["sources"] if s["enabled"]]
    if dry_run:
        print(json.dumps({"mode":"dry_run","sources_total":len(config["sources"]),"enabled":len(enabled)},ensure_ascii=False))
        return
    if not enabled:
        raise SystemExit("No verified enabled sources; refusing to produce fake snapshots.")
    now = datetime.now(timezone.utc)
    folder = ROOT / "output" / now.strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)
    for source in enabled:
        req = urllib.request.Request(source["url"],headers={"User-Agent":"ExamIntelligenceResearch/0.1 (contact via repository)"})
        with urllib.request.urlopen(req, timeout=25) as response:
            body = response.read(5_000_001)
            if len(body) > 5_000_000:
                raise ValueError("Response exceeds 5MB limit")
        record = {"source_id":source["id"],"url":source["url"],"fetched_at":now.isoformat(),"sha256":digest(body),"byte_count":len(body),"status":"fetched_unreviewed"}
        name = f'{source["id"]}-{now.strftime("%H%M%S%f")}'
        (folder / (name + ".bin")).write_bytes(body)
        (folder / (name + ".json")).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps(record,ensure_ascii=False))

if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--dry-run",action="store_true")
    run(parser.parse_args().dry_run)
