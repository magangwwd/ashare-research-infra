"""Read-only Parquet decoder readiness and SHA256 check for 600031 research.

GitHub Actions installs pyarrow from the repository requirements.txt. This CLI
does not modify input files, production pointers, or Google Drive data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def audit_one(path: str | Path, symbol: str = "600031.SH",
              expected_sha256: str | None = None) -> dict:
    import pyarrow.parquet as pq

    p = Path(path)
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    if expected_sha256 and digest != expected_sha256:
        raise ValueError(f"SHA256 mismatch: {p}")
    metadata = pq.read_metadata(p)
    columns = set(metadata.schema.names)
    required = {"ts_code", "trade_date"}
    if not required.issubset(columns):
        raise ValueError(f"Missing columns {required - columns}: {p}")
    table = pq.read_table(p, columns=["ts_code", "trade_date"])
    codes = table.column("ts_code").to_pylist()
    dates = table.column("trade_date").to_pylist()
    matching = [str(d) for code, d in zip(codes, dates) if code == symbol]
    return {
        "file": str(p),
        "sha256": digest,
        "bytes": p.stat().st_size,
        "parquet_rows": metadata.num_rows,
        "symbol": symbol,
        "symbol_rows": len(matching),
        "symbol_unique_dates": len(set(matching)),
        "symbol_min_date": min(matching) if matching else None,
        "symbol_max_date": max(matching) if matching else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parquet", required=True)
    parser.add_argument("--symbol", default="600031.SH")
    parser.add_argument("--sha256", default=None,
                        help="Expected SHA256 from pinned production manifest")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    result = audit_one(args.parquet, args.symbol, args.sha256)
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
