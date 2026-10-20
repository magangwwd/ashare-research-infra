"""Bounded OKX public trade-page sample with verified cross-run checkpoint.

The REST API only retains roughly three months and five pages per job
DO NOT represent a complete trade tape or an L2 historical reconstruction.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.parse
import urllib.request


def atomic_bytes(path: Path, payload: bytes):
    temp = path.with_name(path.name + ".tmp")
    temp.unlink(missing_ok=True)  # never touch an existing committed page
    with temp.open("xb") as fp:
        fp.write(payload)
        fp.flush()
        os.fsync(fp.fileno())
    os.replace(temp, path)


def save_state(path: Path, state: dict):
    atomic_bytes(path, (json.dumps(state, indent=2, sort_keys=True) + "\n").encode())


def page_name(instrument: str, number: int) -> str:
    return f"{instrument}_page_{number:06d}.jsonl"


def validate_rows(rows, instrument: str, prior_cursor=None):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
        raise ValueError("Invalid OKX page length")
    ids = []
    for row in rows:
        if not isinstance(row, dict) or row.get("instId") != instrument:
            raise ValueError("Wrong OKX instrument")
        if not all(k in row for k in ("tradeId", "ts", "px", "sz", "side")):
            raise ValueError("Incomplete OKX trade fields")
        tid = int(row["tradeId"])
        timestamp = int(row["ts"])
        if tid <= 0 or timestamp <= 0 or float(row["px"]) <= 0 or float(row["sz"]) <= 0:
            raise ValueError("Invalid OKX trade numbers")
        ids.append(tid)
    if any(ids[i] <= ids[i + 1] for i in range(len(ids) - 1)):
        raise ValueError("Trade IDs not strictly descending")
    if prior_cursor is not None and ids[0] >= int(prior_cursor):
        raise ValueError("Trade-page cursor did not go backward")
    return str(ids[-1])


def read_page(path: Path, instrument: str, cursor=None):
    rows = [json.loads(line) for line in path.read_text().splitlines() if line]
    oldest = validate_rows(rows, instrument, cursor)
    return rows, oldest


def download_pages(out: Path, instrument="BTC-USDT-SWAP", page_limit=5,
                   opener=urllib.request.urlopen, sleeper=time.sleep):
    if not 1 <= page_limit <= 30:
        raise ValueError("pages out of bounds")
    if instrument not in ("BTC-USDT-SWAP", "BTC-USDT"):
        raise ValueError("Instrument not allowlisted")
    out.mkdir(parents=True, exist_ok=True)
    checkpoint = out / (instrument + "_checkpoint.json")
    state = json.loads(checkpoint.read_text()) if checkpoint.exists() else {
        "schema": 1, "instrument": instrument, "after": None,
        "pages": 0, "complete": False, "coverage": "BOUNDED_SPARSE_PAGES_ONLY",
    }
    migrated_legacy = False
    if "schema" not in state and checkpoint.exists():
        # Legacy checkpoint was written by the first verified live archive job.
        # Never reset the cursor: validate every legacy page and checksum below.
        if not isinstance(state.get("pages"), int) or state["pages"] <= 0:
            raise ValueError("Invalid legacy checkpoint page count")
        state = {**state, "schema": 1, "instrument": instrument,
                 "coverage": "BOUNDED_SPARSE_PAGES_ONLY"}
        migrated_legacy = True
    if (state.get("schema") != 1 or state.get("instrument") != instrument
            or not isinstance(state.get("pages"), int) or state["pages"] < 0):
        raise ValueError("Invalid or cross-instrument checkpoint")
    # Validate every committed page before fetching or moving the cursor.
    prior = None
    for i in range(state["pages"]):
        path = out / page_name(instrument, i)
        if not path.is_file():
            raise ValueError(f"Missing committed page: {path.name}")
        _, prior = read_page(path, instrument, prior)
    if state["pages"]:
        latest = out / page_name(instrument, state["pages"] - 1)
        if state["after"] != prior or state.get("last_sha256") != hashlib.sha256(latest.read_bytes()).hexdigest():
            raise ValueError("Checkpoint/cursor/last-page checksum mismatch")
    elif state["after"] is not None:
        raise ValueError("Nonempty cursor without pages")
    if migrated_legacy:
        save_state(checkpoint, state)

    for _ in range(page_limit):
        if state["complete"]:
            break
        target = out / page_name(instrument, state["pages"])
        if target.exists():
            # Recover a page committed immediately before a process crash.
            rows, oldest = read_page(target, instrument, state["after"])
        else:
            q = {"instId": instrument, "type": "1", "limit": "100"}
            if state["after"] is not None:
                q["after"] = state["after"]
            url = "https://www.okx.com/api/v5/market/history-trades?" + urllib.parse.urlencode(q)
            req = urllib.request.Request(url, headers={"User-Agent": "BTC-quant-research"})
            with opener(req, timeout=30) as response:
                body = json.load(response)
            if body.get("code") != "0":
                raise RuntimeError(f"OKX API rejected historical page: {body.get('code')} {body.get('msg', '')}")
            rows = body.get("data", [])
            if not rows:
                state["complete"] = True
                state["ended_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
                save_state(checkpoint, state)
                break
            oldest = validate_rows(rows, instrument, state["after"])
            payload = ("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows)).encode()
            atomic_bytes(target, payload)
            sleeper(.2)
        state.update({
            "after": oldest, "pages": state["pages"] + 1,
            "last_file": target.name,
            "last_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "last_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        })
        save_state(checkpoint, state)
    return state


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--instrument", default="BTC-USDT-SWAP")
    parser.add_argument("--pages", type=int, default=5)
    args = parser.parse_args()
    print(json.dumps(download_pages(Path(args.out), args.instrument, args.pages)))


if __name__ == "__main__":
    main()
