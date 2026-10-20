"""Download ONE metadata-verified OKX official L2 daily archive with strict bounds.

Do not claim continuous completeness without inspecting actual event timing.
This is a bounded single-day research archive (not trading execution data).
"""
import argparse
import datetime as dt
import hashlib
import gzip
import json
import os
from pathlib import Path
import re
import tarfile
from urllib.parse import urlparse
import urllib.request

CHUNK_SIZE = 8 * 1024 * 1024
MAX_BYTES = 200 * 1024 * 1024
MAX_UNCOMPRESSED = 4 * 1024 * 1024 * 1024
PREFIX = "/cdn/okx/match/orderbook/pro/L2/400lv/daily/"


def select_file(catalog):
    if catalog.get("coverage_status") != "METADATA_ONLY_NO_FILE_DOWNLOADED":
        raise ValueError("Not a verified official metadata-only catalog")
    req = catalog.get("request") or {}
    if any(req.get(k) != v for k, v in
           {"module": "4", "instType": "SWAP", "instFamilyList": "BTC-USDT",
            "dateAggrType": "daily"}.items()):
        raise ValueError("Unexpected historical market-data selection")
    begin = req.get("begin")
    if begin != req.get("end"):
        raise ValueError("Only a single UTC day is allowed")
    day = dt.datetime.fromtimestamp(int(begin) / 1000, tz=dt.timezone.utc)
    if day.time() != dt.time() or day.date().isoformat() != "2026-10-10":
        raise ValueError("This pilot only permits 2026-10-10 UTC")
    entries = catalog.get("raw_response", {}).get("data", [])
    if len(entries) != 1:
        raise ValueError("Expected precisely one daily summary")
    details = entries[0].get("details", [])
    if len(details) != 1:
        raise ValueError("Expected exactly one BTC-USDT-SWAP group")
    group = details[0]
    if group.get("instType") != "SWAP" or group.get("instFamily") != "BTC-USDT":
        raise ValueError("Cross-instrument catalog entry")
    files = group.get("groupDetails", [])
    if len(files) != 1:
        raise ValueError("Multiple/unknown files; do not guess")
    item = files[0]
    name = item.get("filename")
    expected = "BTC-USDT-SWAP-L2orderbook-400lv-2026-10-10.tar.gz"
    if name != expected:
        raise ValueError("Unexpected official filename")
    uri = item.get("url", "")
    parsed = urlparse(uri)
    if parsed.scheme != "https" or parsed.hostname != "static.okx.com" or \
            parsed.path != PREFIX + "20261010/" + expected or parsed.query or parsed.fragment:
        raise ValueError("Non-allowlisted official file URL")
    expected_size = float(item["sizeMB"])
    if not 1 < expected_size < 200:
        raise ValueError("Outside approved catalog size range")
    return name, uri, expected_size


def bounded_fetch(url, start, end, *, opener=urllib.request.urlopen):
    req = urllib.request.Request(url, headers={
        "User-Agent": "BTC-quant-research", "Range": f"bytes={start}-{end}",
        "Accept-Encoding": "identity",
    })
    with opener(req, timeout=90) as response:
        if response.status != 206:
            raise ValueError("Server must honor HTTP Range, fail closed")
        if getattr(response, "url", url).split("?")[0] != url:
            raise ValueError("Unexpected redirect from official URL")
        value = response.headers.get("Content-Range", "")
        m = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", value)
        if not m or (int(m[1]), int(m[2])) != (start, end):
            raise ValueError(f"Invalid Content-Range: {value[:80]}")
        total = int(m[3])
        if total <= 0 or total > MAX_BYTES:
            raise ValueError("Official file exceeds approved maximum")
        wanted = end - start + 1
        data = response.read(wanted + 1)
        if len(data) != wanted:
            raise ValueError("Partial or oversized HTTP range payload")
    return data, total


def validate_tar_gz(path):
    expanded = 0
    members = 0
    try:
        with tarfile.open(path, "r|gz") as archive:
            for member in archive:
                members += 1
                if members > 100_000:
                    raise ValueError("Unexpected tar member count")
                if member.isdir():
                    continue
                if not member.isfile():
                    raise ValueError("Unsafe/nonregular tar member")
                if member.name.startswith("/") or ".." in Path(member.name).parts:
                    raise ValueError("Unsafe tar path")
                if member.size < 0 or expanded + member.size > MAX_UNCOMPRESSED:
                    raise ValueError("Uncompressed tar exceeds maximum")
                inp = archive.extractfile(member)
                if inp is None:
                    raise ValueError("Missing file in tar")
                done = 0
                while block := inp.read(CHUNK_SIZE):
                    done += len(block)
                if done != member.size:
                    raise ValueError("Incorrect uncompressed file size")
                expanded += done
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise ValueError(f"Invalid official tar.gz: {exc}") from exc
    if members < 1:
        raise ValueError("Official archive contains no entries")
    # Streaming tar readers may stop at TAR EOF before the underlying gzip
    # checksum trailer. Consume the whole gzip stream to verify CRC/ISIZE.
    uncompressed_stream_bytes = 0
    try:
        with gzip.open(path, "rb") as gz:
            while block := gz.read(CHUNK_SIZE):
                uncompressed_stream_bytes += len(block)
                if uncompressed_stream_bytes > MAX_UNCOMPRESSED + 8 * 1024 * 1024:
                    raise ValueError("Gzip payload exceeds bounded expanded size")
    except (OSError, EOFError) as exc:
        raise ValueError(f"Gzip checksum or trailer failed: {exc}") from exc
    return members, expanded


def download(catalog, out: Path, *, opener=urllib.request.urlopen):
    name, url, expected_mb = select_file(catalog)
    out.mkdir(parents=True, exist_ok=True)
    final = out / name
    part = out / (name + ".part")
    if final.exists():
        raise ValueError("Refuse to overwrite a finalized archive")
    # Query 1 byte: confirm exact supported remote length before any large transfers.
    _, total = bounded_fetch(url, 0, 0, opener=opener)
    # Provider sizeMB may use different units and rounding.
    if not .80 <= total / (expected_mb * 1_000_000) <= 1.25:
        raise ValueError("Official file size inconsistent with catalog")
    offset = part.stat().st_size if part.exists() else 0
    if offset > total or offset > MAX_BYTES or (offset % CHUNK_SIZE and offset != total):
        raise ValueError("Invalid local resume offset")
    with part.open("ab") as fp:
        while offset < total:
            last = min(offset + CHUNK_SIZE, total) - 1
            data, actual_total = bounded_fetch(url, offset, last, opener=opener)
            if actual_total != total:
                raise ValueError("Official file changed during download")
            fp.write(data)
            fp.flush()
            os.fsync(fp.fileno())
            offset += len(data)
            print(f"downloaded {offset}/{total} bytes", flush=True)
    members, expanded = validate_tar_gz(part)
    hasher = hashlib.sha256()
    with part.open("rb") as fh:
        while block := fh.read(CHUNK_SIZE):
            hasher.update(block)
    digest = hasher.hexdigest()
    part.replace(final)
    manifest = {
        "source": "OKX_OFFICIAL_L2_400_LEVEL_DAILY",
        "module": 4, "instId": "BTC-USDT-SWAP", "day_utc": "2026-10-10",
        "filename": name, "bytes": total, "sha256": digest,
        "archive_members": members, "uncompressed_bytes": expanded,
        "tar_gzip_integrity": "PASS", "complete_download": True,
        "event_stream_completeness": "NOT_YET_VERIFIED",
        "data_use_licensing": "NOT_YET_VERIFIED",
        "catalog_response_sha256": catalog["response_sha256"],
    }
    (out / "download_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    catalog = json.loads(Path(args.catalog).read_text())
    result = download(catalog, Path(args.out))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
