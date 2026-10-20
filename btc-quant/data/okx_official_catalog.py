"""Bounded, read-only discovery of OKX official historical L2 archive metadata.

No downloads of potentially huge L2 ZIPs, no trading authentication, and
no claim of event-level completeness. Distinct from the last-three-months REST
history-trades sampler.
"""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import urllib.parse
import urllib.request

BASE = "https://www.okx.com/api/v5/public/market-data-history"
EARLIEST = dt.date(2023, 3, 1)
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def query_parameters(day, module=4):
    if not isinstance(day, dt.date) or not EARLIEST <= day <= dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=4):
        raise ValueError("Date must be available historical UTC day, excluding recent T+3 window")
    if module not in (4, 5):
        raise ValueError("Only official 400- and 5000-level book modules are cataloged")
    if module == 5 and day < dt.date(2025, 11, 1):
        raise ValueError("OKX 5000-level history starts 2025-11-01")
    day_ms = str(int(dt.datetime.combine(day, dt.time(), tzinfo=dt.timezone.utc).timestamp() * 1000))
    return {
        "module": str(module), "instType": "SWAP",
        "instFamilyList": "BTC-USDT", "dateAggrType": "daily",
        "begin": day_ms, "end": day_ms,
    }


def fetch_catalog(day, module=4, opener=urllib.request.urlopen):
    params = query_parameters(day, module)
    uri = BASE + "?" + urllib.parse.urlencode(params)
    with opener(urllib.request.Request(uri, headers={"User-Agent": "BTC-quant-public-research"}), timeout=30) as response:
        payload = response.read(MAX_RESPONSE_BYTES + 1)
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ValueError("Historical metadata unexpectedly exceeds size limit")
    result = json.loads(payload)
    if not isinstance(result, dict) or result.get("code") != "0":
        raise ValueError("Official historical catalog API did not accept request: " +
                         str(result.get("code") if isinstance(result, dict) else "not-json"))
    entries = result.get("data")
    if not isinstance(entries, list) or any(not isinstance(x, dict) for x in entries):
        raise ValueError("Unknown OKX official archive response shape; fail closed")
    envelope = {
        "checked_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source": BASE, "request": params,
        "coverage_status": "METADATA_ONLY_NO_FILE_DOWNLOADED",
        "file_inventory_verified": False,
        "entries_count": len(entries),
        "data_keys": sorted(entries[0]) if entries else [],
        "response_sha256": hashlib.sha256(payload).hexdigest(),
        "raw_response": result,
    }
    return envelope


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--utc-day", default="2026-10-10")
    parser.add_argument("--module", type=int, choices=[4, 5], default=4)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    day = dt.date.fromisoformat(args.utc_day)
    envelope = fetch_catalog(day, args.module)
    directory = Path(args.out)
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / f"okx_official_L2_catalog_module{args.module}_{day.isoformat()}.json"
    output.write_text(json.dumps(envelope, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"file": output.name, "status": envelope["coverage_status"],
                      "entries": envelope["entries_count"],
                      "keys": envelope["data_keys"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
