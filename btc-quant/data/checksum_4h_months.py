"""Verify Binance Vision monthly ZIP bytes against each official .CHECKSUM.

Only read official HTTPS URLs. Fail closed on missing or mismatched checkfile.
Save evidence for every archive; do not mutate or infer missing price data.
"""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import urllib.request

BASE = "https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/4h/"


def verify_one(path, opener=urllib.request.urlopen):
    path = Path(path)
    name = path.name
    if not re.fullmatch(r"BTCUSDT-4h-\d{4}-\d{2}\.zip", name):
        raise ValueError("Unexpected archive filename")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    uri = BASE + name + ".CHECKSUM"
    req = urllib.request.Request(uri, headers={"User-Agent": "BTC-4h-source-audit/1.0"})
    with opener(req, timeout=30) as response:
        data = response.read(4097)
    if len(data) > 4096:
        raise ValueError("Official checksum body too large")
    rows = data.decode("ascii").strip().splitlines()
    if len(rows) != 1:
        raise ValueError("Unrecognized official checksum file")
    match = re.fullmatch(r"([a-fA-F0-9]{64})\s+\*?([A-Za-z0-9_.-]+)", rows[0].strip())
    if not match or match[2] != name:
        raise ValueError("Malformed official checksum name/hash")
    if digest.lower() != match[1].lower():
        raise ValueError(f"Mismatch against official checksum for {name}")
    return {"file":name,"url":BASE+name, "sha256":digest, "official_match":True}


def verify_many(paths):
    result=[]
    for path in paths:
        result.append(verify_one(path))
    if not result:
        raise ValueError("No ZIPs to verify")
    return {"checked_utc":dt.datetime.now(dt.timezone.utc).isoformat(),
            "source":"Binance Vision official archive .CHECKSUM",
            "files_verified":len(result),
            "official_sha256_match_all":True,
            "results":result}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--source",required=True)
    parser.add_argument("--out",required=True)
    a=parser.parse_args()
    paths=sorted(Path(a.source).glob("BTCUSDT-4h-*.zip"))
    output=Path(a.out)
    output.parent.mkdir(parents=True,exist_ok=True)
    result=verify_many(paths)
    output.write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps({"files_verified":result["files_verified"],
                      "official_sha256_match_all":True}))


if __name__=="__main__":
    main()
