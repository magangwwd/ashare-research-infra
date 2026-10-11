"""Download point-in-time ownership disclosures with resumable batch files.

Only public disclosure fields are stored. The Tushare token is read from the
environment and is never written to disk or logs.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from data_source_paths import resolve_project_path
from typing import Callable

import pandas as pd
import pyarrow.parquet as pq
import tushare as ts


ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Endpoint:
    name: str
    columns: tuple[str, ...]


ENDPOINTS = {
    "holder_number": Endpoint(
        "stk_holdernumber", ("ts_code", "ann_date", "end_date", "holder_num")
    ),
    "top10_float": Endpoint(
        "top10_floatholders",
        (
            "ts_code",
            "ann_date",
            "end_date",
            "holder_name",
            "hold_amount",
            "hold_ratio",
            "hold_float_ratio",
            "hold_change",
            "holder_type",
        ),
    ),
}


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def resolve(path_text: str) -> Path:
    return resolve_project_path(ROOT, path_text)


def load_symbols(market_root: Path) -> list[str]:
    table = pq.read_table(
        market_root / "instruments.parquet", columns=["vendor_symbol"]
    ).to_pandas()
    return sorted(table["vendor_symbol"].dropna().astype(str).unique().tolist())


def call_with_retry(
    fn: Callable[..., pd.DataFrame],
    kwargs: dict,
    attempts: int,
    min_interval: float,
) -> pd.DataFrame:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        started = time.monotonic()
        try:
            frame = fn(**kwargs)
            elapsed = time.monotonic() - started
            if elapsed < min_interval:
                time.sleep(min_interval - elapsed)
            return frame
        except Exception as exc:  # upstream exceptions are not typed consistently
            last_error = exc
            if attempt == attempts:
                break
            time.sleep(min(30.0, 1.5**attempt))
    assert last_error is not None
    raise last_error


def download_endpoint(
    pro: object,
    endpoint_key: str,
    symbols: list[str],
    raw_root: Path,
    start_date: str,
    end_date: str,
    batch_size: int,
    attempts: int,
    min_interval: float,
) -> None:
    spec = ENDPOINTS[endpoint_key]
    target = raw_root / endpoint_key
    target.mkdir(parents=True, exist_ok=True)
    fetch = getattr(pro, spec.name)
    total_batches = (len(symbols) + batch_size - 1) // batch_size

    for batch_index, offset in enumerate(range(0, len(symbols), batch_size)):
        output_path = target / f"batch_{batch_index:04d}.parquet"
        meta_path = target / f"batch_{batch_index:04d}.json"
        batch_symbols = symbols[offset : offset + batch_size]
        existing = pd.DataFrame(columns=[*spec.columns, "queried_symbol"])
        if output_path.exists() and meta_path.exists():
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
            failed_symbols = [item["symbol"] for item in metadata.get("failures", [])]
            if not failed_symbols:
                continue
            existing = pd.read_parquet(output_path)
            batch_symbols = failed_symbols

        frames: list[pd.DataFrame] = []
        failures: list[dict[str, str]] = []
        for symbol in batch_symbols:
            try:
                frame = call_with_retry(
                    fetch,
                    {
                        "ts_code": symbol,
                        "start_date": start_date,
                        "end_date": end_date,
                    },
                    attempts,
                    min_interval,
                )
                if not frame.empty:
                    frame = frame.reindex(columns=spec.columns).copy()
                    frame["queried_symbol"] = symbol
                    frames.append(frame)
            except Exception as exc:
                failures.append(
                    {
                        "symbol": symbol,
                        "error_type": type(exc).__name__,
                        "message": str(exc)[:500],
                    }
                )

        columns = [*spec.columns, "queried_symbol"]
        downloaded = (
            pd.concat(frames, ignore_index=True)
            if frames
            else pd.DataFrame(columns=columns)
        )
        combined = pd.concat([existing, downloaded], ignore_index=True)
        if not combined.empty:
            combined = combined.drop_duplicates().reset_index(drop=True)
        combined.to_parquet(output_path, index=False)
        metadata = {
            "endpoint": spec.name,
            "batch_index": batch_index,
            "batch_count": total_batches,
            "symbols": batch_symbols,
            "row_count": int(len(combined)),
            "failures": failures,
            "start_date": start_date,
            "end_date": end_date,
            "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        meta_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            f"{endpoint_key}: batch {batch_index + 1}/{total_batches}, "
            f"rows={len(combined)}, failures={len(failures)}",
            flush=True,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", type=Path, default=ROOT / "configs" / "ownership_rebuild.json"
    )
    parser.add_argument(
        "--endpoints",
        nargs="+",
        choices=sorted(ENDPOINTS),
        default=list(ENDPOINTS),
    )
    parser.add_argument("--limit-symbols", type=int)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(args.config.resolve())
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TUSHARE_TOKEN is missing or empty")

    market_root = resolve(config["market_data_root"])
    raw_root = resolve(config["raw_data_dir"])
    symbols = load_symbols(market_root)
    if args.limit_symbols is not None:
        symbols = symbols[: args.limit_symbols]
    pro = ts.pro_api(token)

    start = pd.Timestamp(config["research_start"]).strftime("%Y%m%d")
    end = pd.Timestamp(config["research_end"]).strftime("%Y%m%d")
    for endpoint_key in args.endpoints:
        download_endpoint(
            pro,
            endpoint_key,
            symbols,
            raw_root,
            start,
            end,
            int(config["download_batch_size"]),
            int(config["download_attempts"]),
            float(config["download_min_interval_seconds"]),
        )


if __name__ == "__main__":
    main()
