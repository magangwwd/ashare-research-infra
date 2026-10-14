"""Single orchestrator: freshness, extensions, repair, bounded history, acceptance."""
from __future__ import annotations

import argparse
import copy
import hashlib
import os
import tempfile
from itertools import islice
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

from . import clean
from .download import CRITICAL, DATASETS, STOCK_BASIC_FIELDS, DownloadError, TushareClient, dependency_order
from .manifest import Store, atomic_json, read_dataset, read_session, read_json, request_id, utcnow
from .intraday import aggregate_daily, covered_codes, history_window, minute_window, validate_bars
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
    for name in ("history_requests_per_run", "maintenance_history_requests_per_run"):
        value = config["download"].get(name, config["download"]["history_requests_per_run"])
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"download.{name} must be a nonnegative integer")
    fraction = config["download"].get("core_history_budget_fraction", 0.0)
    if not isinstance(fraction, (int, float)) or isinstance(fraction, bool) or not 0 <= fraction <= 1:
        raise ValueError("download.core_history_budget_fraction must be between 0 and 1")
    target = config["download"].get("core_history_target_sessions", 0)
    if not isinstance(target, int) or isinstance(target, bool) or target < 0:
        raise ValueError("download.core_history_target_sessions must be a nonnegative integer")
    intraday = config.get("intraday", {})
    if intraday.get("enabled"):
        if intraday.get("dataset") != "stk_mins" or intraday.get("freq") != "15min":
            raise ValueError("Task 01 intraday source is fixed to Tushare stk_mins 15min")
        if str(intraday.get("history_start", "")) < "20200101":
            raise ValueError("intraday.history_start cannot be earlier than 20200101")
        for name in ("recent_requests_per_run", "history_requests_per_run", "backfill_history_requests_per_run", "daily_validation_sample", "max_errors_before_abort", "raw_retention_days"):
            value = intraday.get(name, 0)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"intraday.{name} must be a nonnegative integer")
        coverage = intraday.get("min_security_coverage", 0)
        if not isinstance(coverage, (int, float)) or isinstance(coverage, bool) or not 0.95 <= coverage <= 1:
            raise ValueError("intraday.min_security_coverage must be between 0.95 and 1")
    return config


def resolved_as_of(now: datetime, completed_session_hour: int = 18) -> date:
    """Never select an unfinished A-share session for live smoke or updates.

    The provider publishes daily and daily_basic after the 15:00 market close;
    use an 18:00 local-time buffer for the full Critical bundle. Holidays and
    weekends are resolved through the SSE/SZSE trade calendar in the pipeline.
    A delayed 23:50 schedule starting next morning targets yesterday.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("resolved_as_of requires a timezone-aware datetime")
    if not 16 <= completed_session_hour <= 23:
        raise ValueError("completed_session_hour must be 16..23 local time")
    return (now - timedelta(days=1)).date() if now.hour < completed_session_hour else now.date()


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
        self.calendar_cache: dict | None = None
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
                self.store.gap(entry["dataset"], {"partition": entry["partition"], "layer": entry["layer"]}, entry.get("reason", "corrupt partition"))
                # Failed staged files cannot be inputs to merge; publication is untouched.
                import shutil
                shutil.rmtree(self.stage / entry["layer"] / entry["dataset"] / entry["partition"])
                self.store.receipts = {k: v for k, v in self.store.receipts.items() if v["dataset"] != entry["dataset"]}
                atomic_json(self.store.metadata / "requests.json", self.store.receipts)
        basics = []
        for status in ("L", "D", "P"):
            params = {"list_status": status,
                      "fields": STOCK_BASIC_FIELDS}
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
        self.sessions = sorted(set(calendar.loc[(calendar["exchange"].isin(["SSE", "SZSE"])) & (calendar["is_open"] == 1)
                                                & (calendar["calendar_date"] <= self.as_of), "calendar_date"]))
        if not self.sessions:
            raise ValidationError("no open sessions in trade_cal")
        current = calendar.loc[calendar["exchange"].isin(["SSE", "SZSE"]) & (calendar["calendar_date"] == self.as_of)]
        if current.empty:
            raise ValidationError("today is absent from calendar")
        self.mode = "DAILY_UPDATE" if current["is_open"].eq(1).any() else "MAINTENANCE"

    def open_exchanges(self, day: date) -> set[str] | None:
        if self.calendar_cache is None:
            calendar = read_dataset(self.stage, "clean", "trade_cal")
            if calendar.empty:
                return None
            self.calendar_cache = {d: set(g.loc[g["is_open"] == 1, "exchange"])
                                   for d, g in calendar.groupby("calendar_date")}
        return self.calendar_cache.get(day, set())

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
        missing = sorted(clean.expected_pool(self.master, day, suspended, self.open_exchanges(day)) - set(raw["ts_code"]))
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
            result = validate_session(dataset, frame, self.master, day, self.config, suspended, self.open_exchanges(day))
        except Exception as exc:
            self.store.gap(dataset, params, str(exc), missing)
            raise
        if result["missing_ts_codes"]:
            self.store.receipt(dataset, params, len(raw), raw)
            self.store.gap(dataset, params, "accepted coverage with residual gaps", result["missing_ts_codes"])
        else:
            self.store.receipt(dataset, params, len(raw), raw)
        self.checked_sessions.add(day)

    def intraday_state(self) -> dict:
        return read_json(self.store.metadata / "intraday.json")

    def intraday_active(self) -> bool:
        return bool(self.intraday_state().get("activated"))

    def minute_params(self, code: str, day: date) -> dict:
        start, end = minute_window(day)
        return {"ts_code": code, "freq": self.config["intraday"]["freq"],
                "start_date": start, "end_date": end}

    def fetch_minutes(self, params: dict, expected_day: date | None = None) -> int:
        try:
            raw = self.client.fetch("stk_mins", **params)
            if raw.empty:
                if expected_day is not None:
                    # Do not checkpoint an empty latest session as complete:
                    # retry it when minute entitlement/data recovers.
                    self.store.gap("stk_mins", params, "empty minute response for expected active session")
                else:
                    self.store.receipt("stk_mins", params, 0)
                return 0
            validate_bars(raw, expected_day=expected_day)
            self.store.save("stk_mins", raw)
            self.store.receipt("stk_mins", params, len(raw), raw)
            return len(raw)
        except Exception as exc:
            self.store.gap("stk_mins", params, self.client.redact(exc))
            raise

    def validate_derived_daily_sample(self, day: date, derived: pd.DataFrame) -> dict:
        sample_size = self.config["intraday"].get("daily_validation_sample", 0)
        report = {"trade_date": str(day), "sample_size": 0, "matched": 0,
                  "mismatched": 0, "mismatched_ts_codes": [], "checked_at": utcnow()}
        if sample_size <= 0 or derived.empty:
            atomic_json(self.store.metadata / "intraday_daily_validation.json", report)
            return report
        codes = sorted(derived["ts_code"].dropna().unique().tolist())[:sample_size]
        try:
            reference = self.client.fetch("daily", ts_code=",".join(codes), trade_date=day.strftime("%Y%m%d"))
            if reference.empty:
                report["reason"] = "daily validation sample returned no rows"
            else:
                columns = ["ts_code", "open", "high", "low", "close"]
                merged = derived[columns].merge(reference[columns], on="ts_code", suffixes=("_mins", "_daily"))
                mismatched = []
                for row in merged.itertuples():
                    ok = all(abs(float(getattr(row, f"{name}_mins")) - float(getattr(row, f"{name}_daily"))) <= 1e-6
                             for name in ("open", "high", "low", "close"))
                    if not ok:
                        mismatched.append(row.ts_code)
                report.update(sample_size=len(merged), matched=len(merged) - len(mismatched),
                              mismatched=len(mismatched), mismatched_ts_codes=mismatched)
        except Exception as exc:
            # Tushare daily is a QA reference after activation, not the primary
            # price source; inability to sample it must not silently change source.
            report["reason"] = self.client.redact(exc)
        atomic_json(self.store.metadata / "intraday_daily_validation.json", report)
        return report

    def derive_daily_from_minutes(self, day: date) -> dict:
        minute_clean = read_session(self.stage, "stk_mins", day)
        if day not in self.suspension_probed and self.available("suspend_d"):
            self.get_save("suspend_d", {"trade_date": day.strftime("%Y%m%d")})
            self.suspension_probed.add(day)
        suspended = suspension_codes(self.stage, day)
        expected = clean.expected_pool(self.master, day, suspended, self.open_exchanges(day))
        if not expected:
            raise ValidationError(f"stk_mins: theoretical security pool is empty for {day}")
        covered = covered_codes(minute_clean, day) & expected
        coverage = len(covered) / len(expected)
        threshold = self.config["intraday"]["min_security_coverage"]
        if coverage < threshold:
            raise ValidationError(
                f"stk_mins {day}: security coverage={coverage:.2%}, expected={len(expected)}, covered={len(covered)}")
        raw_daily = aggregate_daily(minute_clean, day)
        raw_daily = raw_daily.loc[raw_daily["ts_code"].isin(expected)].reset_index(drop=True)
        self.store.save("daily", raw_daily, replace_dates={day})
        self.coverage_cache.pop(("daily", day.year, day.month), None)
        normalized = clean.normalize("daily", raw_daily)
        details = validate_session(
            "daily", normalized, self.master, day, self.config,
            suspended, self.open_exchanges(day))
        self.store.receipt("daily", {"trade_date": day.strftime("%Y%m%d")}, len(raw_daily), raw_daily)
        qa = self.validate_derived_daily_sample(day, raw_daily)
        details.update(source="stk_mins_15min", qa_sample=qa)
        return details

    def intraday_recent(self) -> None:
        settings = self.config.get("intraday", {})
        if not settings.get("enabled"):
            return
        state_path = self.store.metadata / "intraday.json"
        previous = read_json(state_path)
        if not self.available("stk_mins"):
            reason = self.capabilities["interfaces"].get("stk_mins", {}).get(
                "reason", "stk_mins capability unavailable")
            status = {**previous, "enabled": True, "available": False,
                      "activated": bool(previous.get("activated", False)),
                      "minute_source_healthy": False,
                      "daily_source": ("tushare_daily_fallback" if previous.get("activated")
                                       else "tushare_daily_until_minute_gate_passes"),
                      "updated_at": utcnow(), "reason": reason,
                      "source_status": "DEGRADED" if previous.get("activated") else "NOT_ACTIVATED"}
            atomic_json(state_path, status)
            self.store.gap("stk_mins", {"trade_date": str(self.sessions[-1])}, reason)
            print(f"WARNING minute feed unavailable; validated daily fallback required: {reason}")
            return

        day = self.sessions[-1]
        if day not in self.suspension_probed and self.available("suspend_d"):
            self.get_save("suspend_d", {"trade_date": day.strftime("%Y%m%d")})
            self.suspension_probed.add(day)
        expected = sorted(clean.expected_pool(
            self.master, day, suspension_codes(self.stage, day), self.open_exchanges(day)))
        budget = settings["recent_requests_per_run"]
        fetched = bars = empty_streak = 0
        errors = []
        for code in expected:
            params = self.minute_params(code, day)
            if self.complete("stk_mins", params):
                continue
            if fetched >= budget:
                break
            try:
                rows = self.fetch_minutes(params, expected_day=day)
                bars += rows
                empty_streak = empty_streak + 1 if rows == 0 else 0
            except Exception as exc:
                errors.append(self.client.redact(exc))
                if len(errors) >= settings.get("max_errors_before_abort", 5):
                    break
            fetched += 1
            # Prevent thousands of zero-row calls when an API silently stops
            # returning market minutes. This is never treated as good coverage.
            if empty_streak >= 30 and bars == 0:
                print("WARNING stk_mins 30 successive empty security responses; stopping minute probe")
                break

        minute_clean = read_session(self.stage, "stk_mins", day)
        covered = covered_codes(minute_clean, day) & set(expected)
        coverage = len(covered) / len(expected) if expected else 0.0
        activated = bool(previous.get("activated"))
        derived = None
        if coverage >= settings["min_security_coverage"]:
            derived = self.derive_daily_from_minutes(day)
            if settings.get("activate_daily_from_minutes"):
                activated = True
        elif activated:
            reason = (f"stk_mins {day}: activated source incomplete, coverage={coverage:.2%}, "
                      f"expected={len(expected)}, covered={len(covered)}")
            self.store.gap("stk_mins", {"trade_date": str(day)}, reason)
            print(f"WARNING {reason}; use fully validated Tushare daily fallback")

        state = {
            **previous,
            "enabled": True,
            "available": True,
            "freq": settings["freq"],
            "history_start": settings["history_start"],
            "latest_session": str(day),
            "expected_securities": len(expected),
            "covered_securities": len(covered),
            "coverage": coverage,
            "recent_requests_fetched": fetched,
            "recent_bars_fetched": bars,
            "request_errors": len(errors),
            "activated": activated,
            "minute_source_healthy": coverage >= settings["min_security_coverage"],
            "source_status": ("HEALTHY" if coverage >= settings["min_security_coverage"]
                              else ("DEGRADED" if activated else "NOT_ACTIVATED")),
            "activation_date": previous.get("activation_date") or (str(day) if activated else None),
            "daily_source": ("stk_mins_15min" if activated and coverage >= settings["min_security_coverage"]
                             else ("tushare_daily_fallback" if activated
                                   else "tushare_daily_until_minute_gate_passes")),
            "derived_daily": derived,
            "updated_at": utcnow(),
        }
        atomic_json(state_path, state)

    def intraday_history_requests(self):
        settings = self.config["intraday"]
        start = datetime.strptime(settings["history_start"], "%Y%m%d").date()
        end = self.as_of
        rows = self.master.sort_values(["list_date", "ts_code"], kind="stable")
        for year in range(end.year, start.year - 1, -1):
            year_start, year_end = date(year, 1, 1), date(year, 12, 31)
            for row in rows.itertuples():
                if pd.notna(row.list_date) and row.list_date > year_end:
                    continue
                if pd.notna(row.delist_date) and row.delist_date < year_start:
                    continue
                left, right = history_window(year, start, end)
                if left > right:
                    continue
                yield {
                    "ts_code": row.ts_code,
                    "freq": settings["freq"],
                    "start_date": left,
                    "end_date": right,
                }

    def intraday_history(self) -> None:
        settings = self.config.get("intraday", {})
        if not settings.get("enabled") or not self.available("stk_mins"):
            return
        budget = (settings.get("backfill_history_requests_per_run", settings.get("history_requests_per_run", 0))
                  if self.mode == "BACKFILL" else settings.get("history_requests_per_run", 0))
        if budget <= 0:
            return
        requests = list(self.intraday_history_requests())
        if not requests:
            return
        state_path = self.store.metadata / "intraday.json"
        state = read_json(state_path)
        cursor = state.get("history_cursor", 0) % len(requests)
        fetched = bars = examined = 0
        while examined < len(requests) and fetched < budget:
            params = requests[(cursor + examined) % len(requests)]
            examined += 1
            if self.complete("stk_mins", params):
                continue
            try:
                bars += self.fetch_minutes(params)
            except Exception:
                pass
            fetched += 1
        completed = sum(1 for params in requests if self.complete("stk_mins", params))
        pending = len(requests) - completed
        complete_ratio = completed / len(requests) if requests else 1.0
        state.update({
            "history_cursor": (cursor + examined) % len(requests),
            "history_requests_fetched": fetched,
            "history_bars_fetched": bars,
            "history_requests_examined": examined,
            "history_request_universe": len(requests),
            "history_complete_requests": completed,
            "history_pending_requests": pending,
            "history_complete_ratio": complete_ratio,
            "history_complete": pending == 0,
            "history_finished_at": (state.get("history_finished_at") or utcnow()) if pending == 0 else None,
            "history_start": settings["history_start"],
            "updated_at": utcnow(),
        })
        atomic_json(state_path, state)

    def critical_recent(self, sample: bool = False) -> None:
        errors = []
        names = dependency_order(["daily", "adj_factor", "daily_basic"])
        counts = {n: 2 if sample else self.config["download"]["daily_basic_lookback_trading_days" if n == "daily_basic" else "lookback_trading_days"] for n in names}
        intraday_state = self.intraday_state()
        minute_daily = bool(intraday_state.get("activated")) and not sample
        minute_healthy = bool(intraday_state.get("minute_source_healthy", True))
        activation_date = (date.fromisoformat(intraday_state["activation_date"])
                           if minute_daily and intraday_state.get("activation_date") else None)
        fallbacks = []
        # Complete the newest session for all Critical datasets before older sessions.
        for day in reversed(self.sessions[-max(counts.values()):]):
            for dataset in names:
                if day not in self.sessions[-counts[dataset]:]:
                    continue
                try:
                    if dataset == "daily" and minute_daily and activation_date and day >= activation_date:
                        params = {"trade_date": day.strftime("%Y%m%d")}
                        # Minutes are authoritative only when this day's coverage
                        # meets the frozen 95% gate. Otherwise the entire daily
                        # session is fetched afresh and validated using the same
                        # strict Critical acceptance rules, never partial bars.
                        frame = read_session(self.stage, "stk_mins", day) if minute_healthy else pd.DataFrame()
                        if day not in self.suspension_probed and self.available("suspend_d"):
                            self.get_save("suspend_d", params)
                            self.suspension_probed.add(day)
                        suspended = suspension_codes(self.stage, day)
                        expected = clean.expected_pool(self.master, day, suspended, self.open_exchanges(day))
                        covered = covered_codes(frame, day) & expected
                        coverage = len(covered) / len(expected) if expected else 0.0
                        if minute_healthy and coverage >= self.config["intraday"]["min_security_coverage"]:
                            if not self.complete("daily", params):
                                self.derive_daily_from_minutes(day)
                        else:
                            reason = (f"minute coverage {coverage:.2%} below gate for {day}"
                                      if minute_healthy else "minute source unavailable or degraded")
                            print(f"WARNING {day}: {reason}; fetching and validating Tushare daily")
                            fallbacks.append({"date": str(day), "reason": reason, "source": "tushare_daily"})
                            self.daily(dataset, day, sample)
                    else:
                        self.daily(dataset, day, sample)
                except Exception as exc:
                    errors.append(self.client.redact(exc))
        if fallbacks:
            atomic_json(self.store.metadata / "daily_source_fallback.json", {
                "status": "FALLBACK_PENDING_TASK01_ACCEPTANCE",
                "sessions": fallbacks, "updated_at": utcnow()})
        else:
            # The report describes this run, not a historical unresolved outage.
            (self.store.metadata / "daily_source_fallback.json").unlink(missing_ok=True)
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
        names = [n for n, s in DATASETS.items() if s.tier == tier and s.strategy not in ("derived", "index_metadata", "industry_metadata", "intraday")
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
            elif spec.strategy == "industry_history":
                streams.append(iter([(name, {"ts_code": code, "is_new": state})
                                     for code in self.master["ts_code"] for state in ("Y", "N")]))
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
                            validate_session(name, frame, self.master, stored_day, self.config, suspension_codes(self.stage, stored_day), self.open_exchanges(stored_day))
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
            for day in reversed(self.sessions):
                if day.strftime("%Y%m%d") < floor:
                    continue
                if not clean.expected_pool(self.master, day, open_exchanges=self.open_exchanges(day)):
                    continue
                yield name, {"trade_date": day.strftime("%Y%m%d")}
        elif spec.strategy == "industry_history":
            # Membership spans listing cohorts and requires both provider views;
            # a default query only returns current members (is_new=Y).
            for code in self.master.sort_values(["list_date", "ts_code"], kind="stable")["ts_code"]:
                for state in ("Y", "N"):
                    yield name, {"ts_code": code, "is_new": state}
        elif spec.strategy == "security_snapshot":
            for code in self.master["ts_code"]:
                yield name, {"ts_code": code}
        elif spec.strategy in ("financial", "security_history"):
            for year in range(self.as_of.year, int(start[:4]) - 1, -1):
                for row in self.master.itertuples():
                    if pd.notna(row.list_date) and row.list_date.year > year:
                        continue
                    params = {"ts_code": row.ts_code}
                    params.update(start_date=max(start, f"{year}0101"), end_date=min(self.as_of.strftime("%Y%m%d"), f"{year}1231"))
                    yield name, params
        elif spec.strategy == "index_history":
            for month in reversed(pd.period_range(pd.Timestamp(start), self.as_of, freq="M")):
                for code in self.config["index_universe"]:
                    yield name, {"index_code" if name == "index_weight" else "ts_code": code,
                                 "start_date": max(start, month.start_time.strftime("%Y%m%d")),
                                 "end_date": min(self.as_of.strftime("%Y%m%d"), month.end_time.strftime("%Y%m%d"))}

    def discover_history_ranges(self) -> None:
        """Observe validated coverage rather than hardcoding API inception dates.

        Binary-search open sessions for the first response meeting the coverage gate. The assumption of
        contiguous availability is recorded; monthly file audits still find later gaps.
        """
        path = self.root / "metadata/capabilities/history_ranges.json"
        ranges = read_json(path)
        eligible = [d for d in self.sessions if clean.expected_pool(self.master, d, open_exchanges=self.open_exchanges(d))]
        if not eligible:
            return
        for name in ("daily", "adj_factor", "daily_basic"):
            if (name in ranges and ranges[name].get("probed_month") == self.as_of.strftime("%Y-%m")
                    and ranges[name].get("requested_start") == self.config["download"]["history_start"]
                    and ranges[name].get("coverage_checked") is True):
                continue
            lo, hi = 0, len(eligible) - 1
            while lo < hi:
                middle = (lo + hi) // 2
                probe_day = eligible[middle]
                params = {"trade_date": probe_day.strftime("%Y%m%d")}
                if probe_day not in self.suspension_probed and self.available("suspend_d"):
                    self.get_save("suspend_d", params)
                    self.suspension_probed.add(probe_day)
                frame = self.client.fetch(name, **params)
                suspended = suspension_codes(self.stage, probe_day)
                pool = clean.expected_pool(self.master, probe_day, suspended, self.open_exchanges(probe_day))
                missing = sorted(pool - (set(frame["ts_code"]) if "ts_code" in frame else set()))
                # Small boundary holes are retried rather than misclassified as
                # inaccessible history. Bulk periods with widespread holes remain probes.
                if 0 < len(missing) <= 20:
                    for code in missing:
                        retry = self.client.fetch(name, ts_code=code, **params)
                        if not retry.empty:
                            frame = pd.concat([frame, retry], ignore_index=True)
                try:
                    normalized = clean.normalize(name, frame) if not frame.empty else frame
                    validate_session(name, normalized, self.master, probe_day, self.config,
                                     suspended, self.open_exchanges(probe_day))
                    valid = True
                except ValidationError:
                    valid = False
                if not valid:
                    lo = middle + 1
                else:
                    hi = middle
            ranges[name] = {"first_observed_date": eligible[lo].strftime("%Y%m%d"), "probed_at": utcnow(),
                            "coverage_checked": True, "min_coverage": self.config["validation"]["min_coverage"],
                            "probed_month": self.as_of.strftime("%Y-%m"), "requested_start": self.config["download"]["history_start"],
                            "assumption": "validated coverage is contiguous after the observed boundary; later gaps still fail and are retried"}
            if lo > 0:
                self.store.gap(name, {"start_date": eligible[0].strftime("%Y%m%d"),
                                      "end_date": eligible[lo-1].strftime("%Y%m%d"), "coverage_boundary": True},
                               "historical source prefix does not meet the 95% coverage gate; not published to Clean")
            atomic_json(path, ranges)

    def repair(self) -> None:
        budget = self.config["download"]["repair_requests_per_run"]
        candidates = [g for g in self.store.gaps.values() if "partition" not in g["params"]
                      and not g["params"].get("coverage_boundary")
                      and DATASETS[g["dataset"]].strategy != "intraday"
                      and self.available(g["dataset"])]
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
                floor = read_json(self.root / "metadata/capabilities/history_ranges.json").get(name, {}).get("first_observed_date")
                if floor and params["trade_date"] < floor:
                    continue  # Explicit historical prefix gap, revisited by monthly capability audit.
                unit = (name, params["trade_date"])
                if unit in repaired:
                    continue
                self.daily(name, datetime.strptime(params["trade_date"], "%Y%m%d").date())
                repaired.add(unit)
            else:
                self.get_save(name, params, critical=DATASETS[name].tier == "critical")

    def _run_history_streams(self, streams, budget: int) -> tuple[int, int, bool]:
        """Run bounded history requests; completion is proved by existing files/coverage."""
        completed, fetched = 0, 0
        exhausted = True
        for name, params in round_robin(streams):
            if self.complete(name, params):
                completed += 1
                continue
            if fetched >= budget:
                exhausted = False
                break
            if name in ("daily", "adj_factor", "daily_basic"):
                self.daily(name, datetime.strptime(params["trade_date"], "%Y%m%d").date())
            else:
                self.get_save(name, params)
            fetched += 1
        return completed, fetched, exhausted

    def history(self) -> None:
        if self.mode == "BACKFILL":
            budget_key = "backfill_history_requests_per_run"
        else:
            budget_key = "maintenance_history_requests_per_run" if self.mode == "MAINTENANCE" else "history_requests_per_run"
        budget = self.config["download"].get(budget_key, self.config["download"]["history_requests_per_run"])
        if budget > 0:
            self.discover_history_ranges()
        names = [n for n, s in DATASETS.items() if self.available(n) and s.strategy not in ("snapshot", "calendar", "derived", "index_metadata", "industry_metadata", "intraday")
                 and (s.tier == "critical" or (s.tier == "required" and self.config["datasets"]["required"])
                      or (s.tier == "optional" and self.config["datasets"]["optional"] and n in self.config["optional_whitelist"]))]
        names = dependency_order(names)
        state_path = self.store.metadata / "history.json"
        state = read_json(state_path)

        core_names = [n for n in ("daily", "adj_factor", "daily_basic") if n in names]
        other_names = [n for n in names if n not in core_names]
        target = self.config["download"].get("core_history_target_sessions", 0)
        fraction = float(self.config["download"].get("core_history_budget_fraction", 0.0))
        core_budget = min(budget, int(budget * fraction)) if target and core_names else 0
        if core_names and not other_names:
            core_budget = budget

        core_completed = core_fetched = 0
        core_exhausted = True
        if core_budget:
            # Only the newest target sessions are accelerated. Existing complete
            # sessions are skipped by complete(); every new session still passes
            # the same per-dataset coverage gate before it is stored.
            core_streams = [islice(self.history_requests(name), target) for name in core_names]
            core_completed, core_fetched, core_exhausted = self._run_history_streams(core_streams, core_budget)

        # While the target window is incomplete, reserve the remaining budget for
        # non-core history so financial/industry/index data can never be starved.
        # Once the target window is complete, return to the original all-dataset
        # round-robin and use any unused fast-lane budget there too.
        remaining = budget - core_fetched
        phase2_names = names if core_exhausted else other_names
        cursor_base = state.get("other_cursor", state.get("cursor", 0))
        cursor = cursor_base % max(len(phase2_names), 1)
        rotated = phase2_names[cursor:] + phase2_names[:cursor]
        other_completed = other_fetched = 0
        other_exhausted = True
        if remaining and rotated:
            other_completed, other_fetched, other_exhausted = self._run_history_streams(
                [self.history_requests(n) for n in rotated], remaining)

        fetched = core_fetched + other_fetched
        completed = core_completed + other_completed
        exhausted = core_exhausted and other_exhausted
        next_cursor = cursor + other_fetched
        atomic_json(state_path, {
            "cursor": next_cursor,
            "other_cursor": next_cursor,
            "history_start": self.config["download"]["history_start"],
            "as_of": str(self.as_of),
            "fetched_this_run": fetched,
            "existing_complete_requests": completed,
            "mode": self.mode,
            "request_budget": budget,
            "core_history_target_sessions": target,
            "core_history_budget_fraction": fraction,
            "core_fast_lane_active": bool(core_budget and not core_exhausted),
            "core_request_budget": core_budget,
            "core_fetched_this_run": core_fetched,
            "other_fetched_this_run": other_fetched,
            "all_enabled_requests_examined": exhausted,
            "updated_at": utcnow(),
        })

    def monthly_audit(self) -> None:
        path = self.store.metadata / "audit.json"
        state = read_json(path)
        month = self.as_of.strftime("%Y-%m")
        if state.get("month") == month:
            return
        # Re-fetch a rolling slice even if its existing coverage passes, capturing revisions.
        ranges = read_json(self.root / "metadata/capabilities/history_ranges.json")
        eligible = [(name, day) for day in self.sessions[:-15] for name in ("daily", "adj_factor", "daily_basic")
                    if clean.expected_pool(self.master, day, open_exchanges=self.open_exchanges(day))
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
        if not sample:
            # Minute onboarding happens before Critical freshness. Once the
            # coverage gate activates, daily freshness is derived from 15-minute
            # bars and the full-market Tushare daily endpoint is no longer used.
            self.intraday_recent()
        self.critical_recent(sample)
        if not sample:
            self.extensions()
            self.discover_history_ranges()
            self.repair()
            self.history()
            self.intraday_history()
            self.monthly_audit()
        self.store.retain_raw(datetime.combine(self.as_of, datetime.min.time()))
        atomic_json(self.root / "metadata/effective_config.json", self.config)
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
    for obsolete in ("stk_st", "stk_share"):
        (root / f"metadata/schemas/{obsolete}.json").unlink(missing_ok=True)
    for dataset, schema in clean.SCHEMAS.items():
        atomic_json(root / f"metadata/schemas/{dataset}.json", {
            "dataset": dataset, "source": "Tushare", "schema_version": clean.schema_version(dataset),
            "primary_key": clean.KEYS[dataset], "fields": [{"source_field": f.source, "clean_field": f.name,
                                                           "dtype": f.dtype, "description": f.description} for f in schema],
            "clean_rules": "explicit schema; keep last primary key; financial latest announcement/revision",
            "partition": "financial raw ann_year/ann_month; clean report_year/report_period; otherwise Hive year/month or snapshot",
            "update_frequency": "daily with per-type lookback; low-frequency rotating refresh"})


def main() -> int:
    parser = argparse.ArgumentParser(description="Task 01: smoke or automatic daily/maintenance")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--mode", choices=["smoke", "auto", "backfill"], default="auto")
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
        now_local = datetime.now(ZoneInfo(config["schedule"]["timezone"]))
        as_of = resolved_as_of(now_local)
        # Backfill stays before today, but should not skip an extra day when
        # the scheduled job is already running after midnight.
        if args.mode == "backfill":
            as_of = min(as_of, now_local.date() - timedelta(days=1))
        print(f"TASK01_DATE_SELECTION mode={args.mode} local_now={now_local.isoformat()} as_of={as_of}")
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
            pipeline = Pipeline(root, config, client, as_of, config_path)
            if args.mode == "backfill":
                pipeline.mode = "BACKFILL"
                pipeline.bootstrap()
                pipeline.mode = "BACKFILL"
                # Safe historical-only acceleration: latest completed session,
                # repair queues, all historical streams and minute history.
                pipeline.intraday_recent()
                pipeline.critical_recent(False)
                pipeline.extensions()
                pipeline.discover_history_ranges()
                pipeline.repair()
                pipeline.history()
                pipeline.intraday_history()
                pipeline.store.retain_raw(datetime.combine(pipeline.as_of, datetime.min.time()))
                atomic_json(root / "metadata/effective_config.json", config)
                entries = pipeline.store.scan()
                report = validate_task01(
                    pipeline.stage, config, pipeline.as_of,
                    dates=pipeline.sessions[-config["download"]["lookback_trading_days"]:])
                report["mode"] = "BACKFILL"
                critical_bad = [e for e in entries if e["layer"] == "clean" and e["dataset"] in CRITICAL
                                and e["validation_status"] != "passed"]
                if critical_bad:
                    report["passed"] = False
                    report["errors"].append("Critical stored partition failed structural validation")
                atomic_json(root / "metadata/acceptance.json", report)
                if not report["passed"]:
                    raise ValidationError("; ".join(report["errors"]))
                pipeline.store.publish(report)
                pipeline.store.prune_versions()
            else:
                pipeline.run()
        atomic_json(root / "metadata/last_run.json", {"status": "SUCCESS", "mode": args.mode, "updated_at": utcnow()})
        print("SUCCESS")
        return 0
    except Exception as exc:
        reason = client.redact(exc) if client else str(exc)
        atomic_json(root / "metadata/last_run.json", {"status": "FAILED", "reason": reason, "updated_at": utcnow()})
        print(f"FAILED: {reason}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
