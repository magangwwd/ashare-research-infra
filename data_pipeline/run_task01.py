"""Single orchestrator: freshness, extensions, repair, bounded history, acceptance."""
from __future__ import annotations

import argparse
import copy
import hashlib
import os
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

from . import clean
from .download import CRITICAL, DATASETS, DownloadError, TushareClient, dependency_order
from .manifest import Store, atomic_json, read_dataset, read_session, read_json, request_id, utcnow
from .validate import ValidationError, suspension_codes, validate_frame, validate_session, validate_task01


def load_config(path: Path) -> dict:
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if config["storage"]["parquet_compression"] != "zstd":
        raise ValueError("Task 01 requires ZSTD compression")
    if not 0.95 <= config["validation"]["min_coverage"] <= 1:
        raise ValueError("min_coverage must be >= 0.95 and <= 1")
    if any(not config["datasets"].get(n, True) for n in CRITICAL):
        raise ValueError("Critical datasets cannot be disabled")
    if not set(config["optional_whitelist"]) <= {n for n, s in DATASETS.items() if s.tier == "optional"}:
        raise ValueError("Only the frozen Optional whitelist is allowed")
    for name in ("retries", "requests_per_minute", "page_size", "max_pages"):
        if config["download"][name] <= 0:
            raise ValueError(f"download.{name} must be positive")
    return config


def code_fingerprint() -> str:
    return hashlib.sha256(b"".join(p.read_bytes() for p in sorted(Path(__file__).parent.glob("*.py")))).hexdigest()


class Pipeline:
    def __init__(self, root: Path, config: dict, client: TushareClient, as_of: date,
                 config_path: Path | None = None):
        self.root, self.config, self.client, self.as_of = root, config, client, as_of
        self.config_path = config_path
        self.store = Store(root, config)
        self.stage = self.store.begin()
        self.capabilities: dict = {}
        self.master = pd.DataFrame()
        self.sessions: list[date] = []
        self.checked_sessions: set[date] = set()
        self.suspension_probed: set[date] = set()
        self.coverage_cache: dict[tuple, dict] = {}
        self.mode = "MAINTENANCE"

    def available(self, dataset: str) -> bool:
        return dataset == "security_master" or self.capabilities["interfaces"].get(dataset, {}).get("status") == "AVAILABLE"

    def get_save(self, dataset: str, params: dict, critical: bool = False) -> pd.DataFrame:
        try:
            if not self.available(dataset):
                raise DownloadError(f"{dataset}: capability unavailable")
            frame = self.client.fetch(dataset, **params)
            if critical and frame.empty:
                raise ValidationError(f"{dataset}: Critical request returned empty")
            self.store.save(dataset, frame)
            self.store.receipt(dataset, params, len(frame), frame)
            return frame
        except Exception as exc:
            self.store.gap(dataset, params, self.client.redact(exc))
            if critical:
                raise
            print(f"FAILED {dataset}: {self.client.redact(exc)}")
            return pd.DataFrame()

    def bootstrap(self, sample: int | None = None) -> None:
        self.capabilities = self.client.audit(self.root, self.as_of.strftime("%Y%m%d"))
        # Scanning happens before consulting the manifest or planning requests.
        entries = self.store.scan()
        for entry in entries:
            if entry["validation_status"] == "failed":
                self.store.gap(entry["dataset"], {"partition": entry["partition"]}, entry.get("reason", "corrupt partition"))
                # Failed staged files cannot be inputs to merge; publication is untouched.
                import shutil
                shutil.rmtree(self.stage / entry["layer"] / entry["dataset"] / entry["partition"])
                self.store.receipts = {k: v for k, v in self.store.receipts.items() if v["dataset"] != entry["dataset"]}
                atomic_json(self.store.metadata / "requests.json", self.store.receipts)
        basics = []
        for status in ("L", "D", "P"):
            params = {"list_status": status,
                      "fields": "ts_code,symbol,name,area,industry,market,exchange,list_status,list_date,delist_date"}
            basics.append(self.client.fetch("stock_basic", **params))
        raw = pd.concat(basics, ignore_index=True)
        if raw.empty or basics[0].empty:
            raise ValidationError("stock_basic: live stocks missing")
        if sample:
            metadata = clean.normalize("security_master", clean.security_master(raw, utcnow()))
            selected = clean.mainboard_pool(metadata)["ts_code"].head(sample)
            if len(selected) < sample:
                raise ValidationError("smoke: insufficient mainboard sample")
            raw = raw.loc[raw["ts_code"].isin(selected)]
        self.store.save("stock_basic", raw)
        # Some retired provider identifiers have no market metadata. Keep them and
        # an explicit gap; never guess their board from numeric code prefixes.
        for row in raw.loc[raw["market"].isna() | raw["market"].eq("")].itertuples():
            self.store.gap("stock_basic", {"ts_code": row.ts_code, "list_status": row.list_status,
                                          "metadata_field": "market"},
                           "historical market metadata missing; preserved with UNKNOWN board")
        self.store.save_clean("security_master", clean.normalize("security_master", clean.security_master(raw, utcnow())))
        self.master = read_dataset(self.stage, "clean", "security_master")
        start = self.config["download"]["history_start"]
        if sample:
            start = (self.as_of - timedelta(days=self.config["download"]["smoke_lookback_days"])).strftime("%Y%m%d")
        self.calendar(start)
        calendar = read_dataset(self.stage, "clean", "trade_cal")
        self.sessions = sorted(set(calendar.loc[(calendar["exchange"] == "SSE") & (calendar["is_open"] == 1)
                                                & (calendar["calendar_date"] <= self.as_of), "calendar_date"]))
        if not self.sessions:
            raise ValidationError("no open sessions in trade_cal")
        current = calendar.loc[(calendar["exchange"] == "SSE") & (calendar["calendar_date"] == self.as_of)]
        if current.empty:
            raise ValidationError("today is absent from calendar")
        self.mode = "DAILY_UPDATE" if current.iloc[-1]["is_open"] == 1 else "MAINTENANCE"

    def calendar(self, start: str) -> None:
        # Refresh both current calendars before bounded history requests.
        for exchange in ("SSE", "SZSE"):
            self.get_save("trade_cal", {"exchange": exchange,
                                       "start_date": max(start, f"{self.as_of.year}0101"),
                                       "end_date": f"{self.as_of.year}1231"}, critical=True)
        existing = read_dataset(self.stage, "clean", "trade_cal")
        ranges = {}
        for exchange in ("SSE", "SZSE"):
            seen_history = False
            leading_empty = []
            for year in range(int(start[:4]), self.as_of.year):
                params = {"exchange": exchange, "start_date": max(start, f"{year}0101"), "end_date": f"{year}1231"}
                stored = existing.loc[(existing["exchange"] == exchange)
                                      & (existing["calendar_date"] >= datetime.strptime(params["start_date"], "%Y%m%d").date())
                                      & (existing["calendar_date"] <= date(year, 12, 31))]
                receipt = self.store.receipts.get(request_id("trade_cal", params), {})
                expected_count = (date(year, 12, 31) - datetime.strptime(params["start_date"], "%Y%m%d").date()).days + 1
                if len(stored) and (len(stored) == expected_count or (receipt.get("status") == "complete" and receipt.get("rows") == len(stored))):
                    seen_history = True
                    continue
                try:
                    raw = self.client.fetch("trade_cal", **params)
                    if raw.empty:
                        if seen_history:
                            raise ValidationError(f"trade_cal: interior calendar gap for {exchange} {year}")
                        leading_empty.append(year)
                        self.store.receipt("trade_cal", params, 0)
                        continue
                    seen_history = True
                    self.store.save("trade_cal", raw)
                    self.store.receipt("trade_cal", params, len(raw), raw)
                except Exception as exc:
                    self.store.gap("trade_cal", params, self.client.redact(exc))
                    raise
            available = read_dataset(self.stage, "clean", "trade_cal")
            available = available.loc[available["exchange"] == exchange]
            ranges[exchange] = {"first_observed_date": str(available["calendar_date"].min()),
                                "empty_leading_years": leading_empty, "detected_at": utcnow()}
        atomic_json(self.root / "metadata/capabilities/calendar_ranges.json", ranges)

    def daily(self, dataset: str, day: date, sample: bool = False) -> None:
        params = {"trade_date": day.strftime("%Y%m%d")}
        try:
            if sample:
                frames = [self.client.fetch(dataset, ts_code=code, **params) for code in self.master["ts_code"]]
                raw = pd.concat(frames, ignore_index=True)
            else:
                raw = self.client.fetch(dataset, **params)
            if raw.empty:
                raise ValidationError(f"{dataset}: Critical request returned empty for {day}")
        except Exception as exc:
            self.store.gap(dataset, params, self.client.redact(exc))
            raise
        if not sample and day not in self.suspension_probed and self.available("suspend_d"):
            self.get_save("suspend_d", params)
            self.suspension_probed.add(day)
        suspended = suspension_codes(self.stage, day)
        missing = sorted(clean.expected_pool(self.master, day, suspended) - set(raw["ts_code"]))
        # Missing securities are retried individually before the coverage gate.
        for code in missing:
            retry_params = {"ts_code": code, **params}
            try:
                retry = self.client.fetch(dataset, **retry_params)
                raw = pd.concat([raw, retry], ignore_index=True)
                if not retry.empty:
                    self.store.receipt(dataset, retry_params, len(retry), retry)
            except Exception as exc:
                self.store.gap(dataset, retry_params, self.client.redact(exc), [code])
        # Replace this session in staging. Earlier published rows cannot mask a bad response.
        self.store.save(dataset, raw, replace_dates={day})
        self.coverage_cache.pop((dataset, day.year, day.month), None)
        frame = clean.normalize(dataset, raw)
        try:
            result = validate_session(dataset, frame, self.master, day, self.config, suspended)
        except Exception as exc:
            self.store.gap(dataset, params, str(exc), missing)
            raise
        if result["missing_ts_codes"]:
            self.store.receipt(dataset, params, len(raw), raw)
            self.store.gap(dataset, params, "accepted coverage with residual gaps", result["missing_ts_codes"])
        else:
            self.store.receipt(dataset, params, len(raw), raw)
        self.checked_sessions.add(day)

    def critical_recent(self, sample: bool = False) -> None:
        errors = []
        names = dependency_order(["daily", "adj_factor", "daily_basic"])
        counts = {n: 2 if sample else self.config["download"]["daily_basic_lookback_trading_days" if n == "daily_basic" else "lookback_trading_days"] for n in names}
        # Complete the newest session for all Critical datasets before older sessions.
        for day in reversed(self.sessions[-max(counts.values()):]):
            for dataset in names:
                if day not in self.sessions[-counts[dataset]:]:
                    continue
                try:
                    self.daily(dataset, day, sample)
                except Exception as exc:
                    errors.append(self.client.redact(exc))
        if errors:
            raise ValidationError("; ".join(errors))

    def discover_metadata(self) -> None:
        for market in ("SSE", "SZSE", "CSI"):
            self.get_save("index_basic", {"market": market})
        indexes = read_dataset(self.stage, "clean", "index_basic")
        if not indexes.empty:
            names = self.config["index_discovery_names"]
            discovered = indexes.loc[indexes["name"].isin(names), "ts_code"].dropna().unique().tolist()
            self.config["index_universe"] = sorted(set(self.config["index_universe"]) | set(discovered))
            if self.config_path:
                # No token is ever present in this config.
                temporary = self.config_path.with_suffix(".yaml.tmp")
                temporary.write_text(yaml.safe_dump(self.config, allow_unicode=True, sort_keys=False), encoding="utf-8")
                os.replace(temporary, self.config_path)
        for src, capability in self.capabilities["industry_systems"].items():
            if capability["status"] == "AVAILABLE":
                self.get_save("index_classify", {"src": src})
        primary = self.config["industry"]["primary"]
        if primary and self.capabilities["industry_systems"].get(primary, {}).get("status") != "AVAILABLE":
            self.store.gap("index_classify", {"src": primary}, "configured primary industry is unavailable")
        atomic_json(self.root / "metadata/industry_selection.json", {
            "primary": primary, "auxiliary": [s for s, c in self.capabilities["industry_systems"].items() if c["status"] == "AVAILABLE" and s != primary],
            "status": "selected" if primary else "pending_history_and_stability_review", "updated_at": utcnow()})

    def recent_requests(self, tier: str):
        day = self.sessions[-1].strftime("%Y%m%d")
        start = (self.as_of - timedelta(days=self.config["download"]["financial_lookback_days"])).strftime("%Y%m%d")
        names = [n for n, s in DATASETS.items() if s.tier == tier and s.strategy not in ("derived", "index_metadata", "industry_metadata")
                 and (tier != "optional" or n in self.config["optional_whitelist"])]
        # Interleave security requests so large financial datasets cannot starve others.
        streams = []
        for name in dependency_order(names):
            spec = DATASETS[name]
            if spec.strategy == "trade":
                streams.append(iter([(name, {"trade_date": day})]))
            elif spec.strategy == "index_history":
                streams.append(iter([(name, {"index_code" if name == "index_weight" else "ts_code": code,
                                             "start_date": start, "end_date": day}) for code in self.config["index_universe"]]))
            else:
                streams.append(iter([(name, {"ts_code": code, **({"start_date": start, "end_date": day} if spec.strategy in ("financial", "security_history") else {})}) for code in self.master["ts_code"]]))
        yield from round_robin(streams)

    def extensions(self) -> None:
        if self.config["datasets"]["required"]:
            self.discover_metadata()
        for tier in ("required", "optional"):
            if not self.config["datasets"][tier]:
                continue
            candidates = list(self.recent_requests(tier))
            path = self.store.metadata / f"{tier}_cursor.json"
            cursor = read_json(path).get("cursor", 0)
            budget = self.config["download"][f"{tier}_recent_requests_per_run"]
            # All small trade-date datasets get freshness priority; low-frequency data rotate.
            immediate = [(n, p) for n, p in candidates if DATASETS[n].strategy == "trade"]
            for name, params in immediate:
                self.get_save(name, params)
            rotating = [(n, p) for n, p in candidates if DATASETS[n].strategy != "trade"]
            if rotating:
                for i in range(min(budget, len(rotating))):
                    name, params = rotating[(cursor + i) % len(rotating)]
                    self.get_save(name, params)
                atomic_json(path, {"cursor": (cursor + min(budget, len(rotating))) % len(rotating)})

    def complete(self, name: str, params: dict) -> bool:
        receipt = self.store.receipts.get(request_id(name, params))
        if name in ("daily", "adj_factor", "daily_basic"):
            day = datetime.strptime(params["trade_date"], "%Y%m%d").date()
            key = (name, day.year, day.month)
            if key not in self.coverage_cache:
                from .manifest import read_parquet
                files = sorted((self.stage / "clean" / name / f"year={day.year}/month={day.month:02d}").glob("*.parquet"))
                frame = pd.concat([read_parquet(p) for p in files], ignore_index=True) if files else pd.DataFrame()
                results = {}
                if not frame.empty:
                    for stored_day in frame["trade_date"].unique():
                        try:
                            validate_session(name, frame, self.master, stored_day, self.config, suspension_codes(self.stage, stored_day))
                            results[stored_day] = True
                        except Exception:
                            results[stored_day] = False
                self.coverage_cache[key] = results
            return self.coverage_cache[key].get(day, False)
        if not receipt or receipt["status"] != "complete":
            return False
        if receipt["rows"] == 0:
            return True  # Successful bounded empty queries are explicitly recorded.
        if "clean_partitions" in receipt:
            return all(list((self.stage / "clean" / name / p).glob("*.parquet")) for p in receipt["clean_partitions"])
        return bool(list((self.stage / "clean" / name).rglob("*.parquet")))

    def history_requests(self, name: str):
        spec = DATASETS[name]
        start = self.config["download"]["history_start"]
        if spec.strategy == "trade":
            floor = max(start, spec.available_from or start,
                        read_json(self.root / "metadata/capabilities/history_ranges.json").get(name, {}).get("first_observed_date", start))
            for day in self.sessions:
                if day.strftime("%Y%m%d") < floor:
                    continue
                if not clean.expected_pool(self.master, day):
                    continue
                yield name, {"trade_date": day.strftime("%Y%m%d")}
        elif spec.strategy in ("financial", "security_history", "industry_history"):
            for year in range(int(start[:4]), self.as_of.year + 1):
                for row in self.master.itertuples():
                    if pd.notna(row.list_date) and row.list_date.year > year:
                        continue
                    params = {"ts_code": row.ts_code}
                    if spec.strategy != "industry_history":
                        params.update(start_date=max(start, f"{year}0101"), end_date=min(self.as_of.strftime("%Y%m%d"), f"{year}1231"))
                    elif year != int(start[:4]):
                        continue  # Membership request already returns raw membership history.
                    yield name, params
        elif spec.strategy == "index_history":
            for month in pd.period_range(pd.Timestamp(start), self.as_of, freq="M"):
                for code in self.config["index_universe"]:
                    yield name, {"index_code" if name == "index_weight" else "ts_code": code,
                                 "start_date": max(start, month.start_time.strftime("%Y%m%d")),
                                 "end_date": min(self.as_of.strftime("%Y%m%d"), month.end_time.strftime("%Y%m%d"))}

    def discover_history_ranges(self) -> None:
        """Observe provider availability rather than hardcoding different API inception dates.

        Binary-search open sessions for the first nonempty response. The assumption of
        contiguous availability is recorded; monthly file audits still find later gaps.
        """
        path = self.root / "metadata/capabilities/history_ranges.json"
        ranges = read_json(path)
        eligible = [d for d in self.sessions if clean.expected_pool(self.master, d)]
        if not eligible:
            return
        for name in ("daily", "adj_factor", "daily_basic"):
            if (name in ranges and ranges[name].get("probed_month") == self.as_of.strftime("%Y-%m")
                    and ranges[name].get("requested_start") == self.config["download"]["history_start"]):
                continue
            lo, hi = 0, len(eligible) - 1
            while lo < hi:
                middle = (lo + hi) // 2
                frame = self.client.query(name, trade_date=eligible[middle].strftime("%Y%m%d"), limit=1, offset=0)
                if frame.empty:
                    lo = middle + 1
                else:
                    hi = middle
            ranges[name] = {"first_observed_date": eligible[lo].strftime("%Y%m%d"), "probed_at": utcnow(),
                            "probed_month": self.as_of.strftime("%Y-%m"), "requested_start": self.config["download"]["history_start"],
                            "assumption": "nonempty availability is contiguous over open sessions; later gaps are retried"}
            atomic_json(path, ranges)

    def repair(self) -> None:
        budget = self.config["download"]["repair_requests_per_run"]
        candidates = [g for g in self.store.gaps.values() if "partition" not in g["params"] and self.available(g["dataset"])]
        candidates.sort(key=lambda g: (DATASETS[g["dataset"]].tier != "critical", g["updated_at"]))
        repaired: set[tuple] = set()
        for gap in candidates[:budget]:
            name, params = gap["dataset"], gap["params"]
            if "partition" in params:
                continue  # File scan + history scheduler rebuild damaged partitions.
            if "metadata_field" in params:
                field = params["metadata_field"]
                query = {k: v for k, v in params.items() if k != "metadata_field"}
                frame = self.get_save(name, query, critical=DATASETS[name].tier == "critical")
                if field in frame and frame[field].notna().all() and not frame[field].eq("").any():
                    self.store.gaps.pop(request_id(name, params), None)
                    atomic_json(self.store.metadata / "gaps.json", self.store.gaps)
                else:
                    self.store.gap(name, params, gap["reason"])
            elif name in ("daily", "adj_factor", "daily_basic"):
                unit = (name, params["trade_date"])
                if unit in repaired:
                    continue
                self.daily(name, datetime.strptime(params["trade_date"], "%Y%m%d").date())
                repaired.add(unit)
            else:
                self.get_save(name, params, critical=DATASETS[name].tier == "critical")

    def history(self) -> None:
        if self.config["download"]["history_requests_per_run"] > 0:
            self.discover_history_ranges()
        names = [n for n, s in DATASETS.items() if self.available(n) and s.strategy not in ("snapshot", "calendar", "derived", "index_metadata", "industry_metadata")
                 and (s.tier == "critical" or (s.tier == "required" and self.config["datasets"]["required"])
                      or (s.tier == "optional" and self.config["datasets"]["optional"] and n in self.config["optional_whitelist"]))]
        names = dependency_order(names)
        state_path = self.store.metadata / "history.json"
        state = read_json(state_path)
        cursor = state.get("cursor", 0) % max(len(names), 1)
        names = names[cursor:] + names[:cursor]
        streams = [self.history_requests(n) for n in names]
        completed, fetched = 0, 0
        exhausted = True
        for name, params in round_robin(streams):
            if self.complete(name, params):
                completed += 1
                continue
            if fetched >= self.config["download"]["history_requests_per_run"]:
                exhausted = False
                break
            if name in ("daily", "adj_factor", "daily_basic"):
                self.daily(name, datetime.strptime(params["trade_date"], "%Y%m%d").date())
            else:
                self.get_save(name, params)
            fetched += 1
        atomic_json(state_path, {"cursor": cursor + fetched, "history_start": self.config["download"]["history_start"],
                                 "as_of": str(self.as_of), "fetched_this_run": fetched, "existing_complete_requests": completed,
                                 "all_enabled_requests_examined": exhausted, "updated_at": utcnow()})

    def monthly_audit(self) -> None:
        path = self.store.metadata / "audit.json"
        state = read_json(path)
        month = self.as_of.strftime("%Y-%m")
        if state.get("month") == month:
            return
        # Re-fetch a rolling slice even if its existing coverage passes, capturing revisions.
        ranges = read_json(self.root / "metadata/capabilities/history_ranges.json")
        eligible = [(name, day) for day in self.sessions[:-15] for name in ("daily", "adj_factor", "daily_basic")
                    if clean.expected_pool(self.master, day)
                    and day.strftime("%Y%m%d") >= ranges.get(name, {}).get("first_observed_date", self.config["download"]["history_start"])]
        cursor = state.get("cursor", 0)
        count = min(self.config["download"]["audit_requests_per_run"], len(eligible))
        for i in range(count):
            name, day = eligible[(cursor + i) % len(eligible)]
            self.daily(name, day)
        # All stored files are structurally rechecked by Store.scan every run.
        atomic_json(path, {"month": month, "cursor": (cursor + count) % max(1, len(eligible)), "updated_at": utcnow()})

    def run(self, sample: bool = False) -> dict:
        self.bootstrap(sample=2 if sample else None)
        self.critical_recent(sample)
        if not sample:
            self.extensions()
            self.repair()
            self.history()
            self.monthly_audit()
        self.store.retain_raw(datetime.combine(self.as_of, datetime.min.time()))
        entries = self.store.scan()
        critical_bad = [e for e in entries if e["layer"] == "clean" and e["dataset"] in CRITICAL and e["validation_status"] != "passed"]
        # Each fetched historical session must pass all three Critical datasets only
        # after all three have been downloaded. Freshness checks are dataset-specific.
        report = validate_task01(self.stage, self.config, self.as_of, dates=self.sessions[-(2 if sample else self.config["download"]["lookback_trading_days"]):])
        report["mode"] = self.mode
        if critical_bad:
            report["passed"] = False
            report["errors"].append("Critical stored partition failed structural validation")
        atomic_json(self.root / "metadata/acceptance.json", report)
        if not report["passed"]:
            raise ValidationError("; ".join(report["errors"]))
        self.store.publish(report)
        self.store.prune_versions()
        return report


def round_robin(streams):
    active = list(streams)
    while active:
        remaining = []
        for stream in active:
            try:
                yield next(stream)
                remaining.append(stream)
            except StopIteration:
                pass
        active = remaining


def schema_docs(root: Path) -> None:
    for dataset, schema in clean.SCHEMAS.items():
        atomic_json(root / f"metadata/schemas/{dataset}.json", {
            "dataset": dataset, "source": "Tushare", "schema_version": clean.SCHEMA_VERSION,
            "primary_key": clean.KEYS[dataset], "fields": [{"source_field": f.source, "clean_field": f.name,
                                                           "dtype": f.dtype, "description": f.description} for f in schema],
            "clean_rules": "explicit schema; keep last primary key; financial latest announcement/revision",
            "partition": "financial raw ann_year/ann_month; clean report_year/report_period; otherwise Hive year/month or snapshot",
            "update_frequency": "daily with per-type lookback; low-frequency rotating refresh"})


def main() -> int:
    parser = argparse.ArgumentParser(description="Task 01: smoke or automatic daily/maintenance")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--mode", choices=["smoke", "auto"], default="auto")
    parser.add_argument("--root", default=None)
    args = parser.parse_args()
    client = None
    root = Path(args.root or ".").resolve()
    try:
        config_path = Path(args.config).resolve()
        config = load_config(config_path)
        root = Path(args.root or config["storage"]["root"]).resolve()
        root.mkdir(parents=True, exist_ok=True)
        client = TushareClient(config)
        as_of = datetime.now(ZoneInfo(config["schedule"]["timezone"])).date()
        schema_docs(root)
        if args.mode == "smoke":
            atomic_json(root / "metadata/smoke.json", {"passed": False, "live_tushare": True, "checked_at": utcnow()})
            smoke_config = copy.deepcopy(config)
            smoke_config["validation"]["min_daily_rows"] = 1
            with tempfile.TemporaryDirectory(prefix="task01-smoke-", dir=root) as smoke:
                try:
                    report = Pipeline(Path(smoke), smoke_config, client, as_of).run(sample=True)
                finally:
                    import shutil
                    capabilities = Path(smoke) / "metadata/capabilities"
                    if capabilities.exists():
                        shutil.copytree(capabilities, root / "metadata/capabilities", dirs_exist_ok=True)
            atomic_json(root / "metadata/smoke.json", {"passed": True, "live_tushare": True, "code_fingerprint": code_fingerprint(), "checked_at": utcnow(), "report": report})
        else:
            gate = read_json(root / "metadata/smoke.json")
            if not gate.get("passed") or not gate.get("live_tushare") or gate.get("code_fingerprint") != code_fingerprint():
                raise ValidationError("Run a live --mode smoke successfully for this code before history or production")
            Pipeline(root, config, client, as_of, config_path).run()
        print("SUCCESS")
        return 0
    except Exception as exc:
        reason = client.redact(exc) if client else str(exc)
        atomic_json(root / "metadata/last_run.json", {"status": "FAILED", "reason": reason, "updated_at": utcnow()})
        print(f"FAILED: {reason}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
