"""Parquet is authoritative. JSON indexes are disposable and atomically written."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from . import clean


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, default=str)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {} if default is None else default


def request_id(dataset: str, params: dict) -> str:
    return hashlib.sha256(json.dumps([dataset, params], sort_keys=True).encode()).hexdigest()


def read_parquet(path: Path) -> pd.DataFrame:
    # ParquetFile avoids adding Hive columns or conflicting dictionary types.
    return pq.ParquetFile(path).read().to_pandas()


def read_dataset(root: Path, layer: str, dataset: str) -> pd.DataFrame:
    files = sorted(p for p in (root / layer / dataset).rglob("*.parquet") if not any(s.endswith(".tmp") for s in p.parts))
    return pd.concat([read_parquet(p) for p in files], ignore_index=True) if files else pd.DataFrame()


def read_session(root: Path, dataset: str, day) -> pd.DataFrame:
    path = root / "clean" / dataset / f"year={day.year}/month={day.month:02d}"
    files = sorted(path.glob("*.parquet"))
    frame = pd.concat([read_parquet(p) for p in files], ignore_index=True) if files else pd.DataFrame()
    return frame.loc[frame["trade_date"] == day] if not frame.empty else frame


def partitions(dataset: str, frame: pd.DataFrame, layer: str) -> list[tuple[str, pd.DataFrame]]:
    if frame.empty:
        return []
    if dataset in clean.FINANCIAL:
        column = "ann_date" if layer == "raw" else "report_period"
    else:
        column = next((c for c in ("trade_date", "trade_time", "calendar_date", "cal_date", "ann_date", "change_date", "start_date", "surv_date", "in_date") if c in frame), None)
    if column is None:
        return [("snapshot=current", frame)]
    dates = pd.to_datetime(frame[column], format="mixed", errors="coerce")
    keys = []
    for value in dates:
        if pd.isna(value):
            keys.append("date=unknown")
        elif dataset in clean.FINANCIAL and layer == "clean":
            keys.append(f"report_year={value.year}/report_period={value:%Y%m%d}")
        elif dataset in clean.FINANCIAL:
            keys.append(f"ann_year={value.year}/ann_month={value.month:02d}")
        elif dataset in ("trade_cal",):
            keys.append(f"year={value.year}")
        else:
            keys.append(f"year={value.year}/month={value.month:02d}")
    labels = pd.Series(keys, index=frame.index)
    return [(key, frame.loc[labels == key]) for key in sorted(set(keys))]


def write_compacted(path: Path, frame: pd.DataFrame, dataset: str, layer: str, target_mb: int) -> None:
    """Validate, compact to ZSTD parts, validate again, replace staging partition."""
    from .validate import validate_frame
    if layer == "clean":
        validate_frame(dataset, frame)
    temporary = path.with_name(path.name + ".tmp")
    shutil.rmtree(temporary, ignore_errors=True)
    temporary.mkdir(parents=True)
    schema = clean.arrow_schema(dataset) if layer == "clean" else None
    table = pa.Table.from_pandas(frame, schema=schema, preserve_index=False, safe=True)
    # Compressed size, not in-memory size, determines compaction target.
    probe = temporary / "probe.parquet"
    pq.write_table(table, probe, compression="zstd")
    target = target_mb * 1024 * 1024
    count = max(1, (probe.stat().st_size + target - 1) // target)
    rows_per_file = max(1, (len(frame) + count - 1) // count)
    probe.unlink()
    for i, start in enumerate(range(0, len(frame), rows_per_file)):
        pq.write_table(table.slice(start, rows_per_file), temporary / f"part-{i:03d}.parquet", compression="zstd")
    reread = pd.concat([read_parquet(p) for p in sorted(temporary.glob("*.parquet"))], ignore_index=True)
    if len(reread) != len(frame):
        raise ValueError("Parquet row count changed during compaction")
    if layer == "clean":
        validate_frame(dataset, reread)
    # Only staging is touched. Published partitions are immutable hardlinks.
    shutil.rmtree(path, ignore_errors=True)
    os.replace(temporary, path)


class Store:
    def __init__(self, root: Path, config: dict):
        self.root, self.config = root, config
        self.metadata = root / "metadata/manifest"
        self.receipts = read_json(self.metadata / "requests.json")
        self.gaps = read_json(self.metadata / "gaps.json")
        self.stage: Path | None = None

    def begin(self) -> Path:
        versions = self.root / "data/.versions"
        versions.mkdir(parents=True, exist_ok=True)
        active = read_json(self.metadata / "active.json").get("version")
        current = self.root / "data/current"
        if active and (versions / active).is_dir() and (not current.exists() or current.resolve() != (versions / active).resolve()):
            self.stage = versions / active
            for temporary in self.stage.rglob("*.tmp"):
                if temporary.is_dir():
                    shutil.rmtree(temporary)
            return self.stage
        version = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
        self.stage = versions / version
        if current.exists():
            shutil.copytree(current.resolve(), self.stage, copy_function=os.link)
        else:
            self.stage.mkdir()
        for layer in ("raw", "clean"):
            (self.stage / layer).mkdir(exist_ok=True)
            link = self.root / "data" / layer
            if not link.is_symlink():
                if link.exists():
                    raise ValueError(f"{link} must not be a materialized directory; restore the checkpoint archive")
                link.symlink_to(f"current/{layer}")
        atomic_json(self.metadata / "active.json", {"version": version})
        return self.stage

    def save(self, dataset: str, raw: pd.DataFrame, replace_dates: set | None = None) -> None:
        if raw.empty:
            return
        assert self.stage is not None
        raw = raw.copy()
        raw["_fetched_at"] = utcnow()
        for partition, rows in partitions(dataset, raw, "raw"):
            path = self.stage / "raw" / dataset / partition
            old = [read_parquet(p) for p in sorted(path.glob("*.parquet"))]
            combined = pd.concat([*old, rows], ignore_index=True)
            write_compacted(path, combined, dataset, "raw", self.config["storage"]["compact_target_mb"])
        cleaned = clean.normalize(dataset, raw)
        self.save_clean(dataset, cleaned, replace_dates)

    def save_clean(self, dataset: str, cleaned: pd.DataFrame, replace_dates: set | None = None) -> None:
        assert self.stage is not None
        for partition, rows in partitions(dataset, cleaned, "clean"):
            path = self.stage / "clean" / dataset / partition
            old = [read_parquet(p) for p in sorted(path.glob("*.parquet"))]
            if replace_dates:
                old = [f.loc[~f["trade_date"].isin(replace_dates)] for f in old]
            combined = pd.concat([*old, rows], ignore_index=True)
            if dataset in clean.FINANCIAL:
                combined = combined.sort_values(["ann_date", "actual_ann_date", "update_flag"], kind="stable", na_position="first")
            combined = combined.drop_duplicates(clean.KEYS[dataset], keep="last")
            write_compacted(path, combined, dataset, "clean", self.config["storage"]["compact_target_mb"])

    def receipt(self, dataset: str, params: dict, rows: int, raw: pd.DataFrame | None = None) -> None:
        key = request_id(dataset, params)
        self.receipts[key] = {"dataset": dataset, "params": params, "rows": rows, "updated_at": utcnow(), "status": "complete"}
        if raw is not None and not raw.empty:
            self.receipts[key]["clean_partitions"] = [p for p, _ in partitions(dataset, clean.normalize(dataset, raw), "clean")]
        self.gaps.pop(key, None)
        atomic_json(self.metadata / "requests.json", self.receipts)
        atomic_json(self.metadata / "gaps.json", self.gaps)

    def gap(self, dataset: str, params: dict, reason: str, missing: list[str] | None = None) -> None:
        self.gaps[request_id(dataset, params)] = {"dataset": dataset, "params": params, "reason": reason,
                                                   "missing_ts_codes": missing or [], "updated_at": utcnow(), "status": "failed"}
        atomic_json(self.metadata / "gaps.json", self.gaps)

    def scan(self, location: Path | None = None) -> list[dict]:
        from .validate import validate_frame, validate_session, suspension_codes
        location = location or self.stage
        assert location is not None
        master = read_dataset(location, "clean", "security_master")
        calendar = read_dataset(location, "clean", "trade_cal")
        manifest = []
        for layer in ("raw", "clean"):
            base = location / layer
            paths = sorted({p.parent for p in base.rglob("*.parquet") if not any(s.endswith(".tmp") for s in p.parts)})
            for path in paths:
                files = sorted(path.glob("*.parquet"))
                dataset = path.relative_to(base).parts[0]
                entry = {"dataset": dataset, "layer": layer,
                         "partition": str(path.relative_to(base / dataset)), "updated_at": utcnow(),
                         "start_date": None, "end_date": None, "rows": 0,
                         "status": "complete", "validation_status": "passed", "files": []}
                try:
                    frame = pd.concat([read_parquet(p) for p in files], ignore_index=True)
                    entry["rows"] = len(frame)
                    if layer == "clean":
                        validate_frame(dataset, frame)
                        if dataset in ("daily", "adj_factor", "daily_basic") and not master.empty and not calendar.empty:
                            for day, rows in frame.groupby("trade_date"):
                                opened = set(calendar.loc[(calendar["calendar_date"] == day) & (calendar["is_open"] == 1), "exchange"])
                                validate_session(dataset, rows, master, day, self.config, suspension_codes(location, day), opened)
                    col = next((c for c in ("trade_date", "calendar_date", "cal_date", "report_period", "ann_date") if c in frame), None)
                    if col and frame[col].notna().any():
                        dates = pd.to_datetime(frame[col], format="mixed", errors="raise")
                        entry["start_date"], entry["end_date"] = str(dates.min().date()), str(dates.max().date())
                    for file in files:
                        entry["files"].append({"path": str(file.relative_to(location)), "bytes": file.stat().st_size,
                                                "sha256": hashlib.sha256(file.read_bytes()).hexdigest()})
                except Exception as exc:
                    entry.update(status="failed", validation_status="failed", reason=str(exc))
                manifest.append(entry)
        atomic_json(self.metadata / "partitions.json", manifest)
        repaired = {(e["dataset"], e["partition"], e["layer"]) for e in manifest if e["validation_status"] == "passed"}
        for key, gap in list(self.gaps.items()):
            params = gap["params"]
            if "partition" in params and (gap["dataset"], params["partition"], params.get("layer", "clean")) in repaired:
                self.gaps.pop(key)
        atomic_json(self.metadata / "gaps.json", self.gaps)
        return manifest

    def retain_raw(self, reference: datetime) -> None:
        assert self.stage is not None
        market_days = self.config["storage"]["market_raw_retention_days"]
        for dataset in ("daily", "daily_basic", "adj_factor", "stk_mins"):
            retention_days = (self.config.get("intraday", {}).get("raw_retention_days", market_days)
                              if dataset == "stk_mins" else market_days)
            cutoff = pd.Timestamp(reference, tz="UTC") - pd.Timedelta(days=retention_days)
            for year_path in (self.stage / "raw" / dataset).glob("year=*"):
                for month_path in year_path.glob("month=*"):
                    files = sorted(month_path.glob("*.parquet"))
                    if not files:
                        continue
                    raw = pd.concat([read_parquet(p) for p in files], ignore_index=True)
                    if "_fetched_at" not in raw:
                        continue
                    fetched = pd.to_datetime(raw["_fetched_at"], utc=True, errors="raise")
                    retained = raw.loc[fetched.isna() | (fetched >= cutoff)]
                    if retained.empty:
                        shutil.rmtree(month_path)
                    elif len(retained) != len(raw):
                        write_compacted(month_path, retained, dataset, "raw", self.config["storage"]["compact_target_mb"])

    def publish(self, report: dict) -> None:
        if not report.get("passed"):
            raise ValueError("publication requires passed Task 01 acceptance")
        assert self.stage is not None
        atomic_json(self.stage / "acceptance.json", report)
        pointer = self.root / "data/.current.tmp"
        pointer.unlink(missing_ok=True)
        pointer.symlink_to(f".versions/{self.stage.name}")
        os.replace(pointer, self.root / "data/current")  # The sole production commit.
        atomic_json(self.metadata / "current.json", {"version": self.stage.name, "published_at": utcnow()})
        (self.metadata / "active.json").unlink(missing_ok=True)

    def prune_versions(self) -> None:
        current = self.root / "data/current"
        if not current.exists():
            return
        directories = sorted((self.root / "data/.versions").iterdir(), key=lambda p: p.name, reverse=True)
        keep = {current.resolve(), *[p.resolve() for p in directories[:2]]}
        for directory in directories:
            if directory.is_dir() and directory.resolve() not in keep:
                shutil.rmtree(directory)
