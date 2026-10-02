"""Tushare client, bounded requests, retries and capability snapshots."""
from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .manifest import atomic_json

CRITICAL = ("stock_basic", "trade_cal", "daily", "adj_factor", "daily_basic")
STOCK_BASIC_FIELDS = "ts_code,symbol,name,area,industry,market,exchange,list_status,list_date,delist_date"


@dataclass(frozen=True)
class Dataset:
    name: str
    tier: str
    strategy: str
    date_field: str | None = None
    dependencies: tuple[str, ...] = ("stock_basic", "trade_cal")
    available_from: str | None = None


DATASETS = {
    "stock_basic": Dataset("stock_basic", "critical", "snapshot", dependencies=()),
    "trade_cal": Dataset("trade_cal", "critical", "calendar", dependencies=()),
    "security_master": Dataset("security_master", "required", "derived", dependencies=("stock_basic",)),
    **{n: Dataset(n, "critical", "trade", "trade_date") for n in ("daily", "adj_factor", "daily_basic")},
    **{n: Dataset(n, "required", "financial", "ann_date") for n in ("income", "balancesheet", "cashflow", "fina_indicator", "forecast", "express", "dividend")},
    "namechange": Dataset("namechange", "required", "security_history", "start_date"),
    "stock_st": Dataset("stock_st", "required", "trade", "trade_date", available_from="20000101"),
    "stk_premarket": Dataset("stk_premarket", "required", "trade", "trade_date", available_from="20240501"),
    **{n: Dataset(n, "required", "trade", "trade_date") for n in ("suspend_d", "stk_limit", "sw_daily", "ci_daily")},
    "index_basic": Dataset("index_basic", "required", "index_metadata"),
    "index_daily": Dataset("index_daily", "required", "index_history", "trade_date", ("index_basic", "trade_cal")),
    "index_weight": Dataset("index_weight", "required", "index_history", "trade_date", ("index_basic", "trade_cal")),
    "index_classify": Dataset("index_classify", "required", "industry_metadata"),
    "index_member_all": Dataset("index_member_all", "required", "industry_history", "in_date", ("index_classify",)),
    "ci_index_member": Dataset("ci_index_member", "required", "industry_history", "in_date", ("index_classify",)),
    **{n: Dataset(n, "optional", "trade", "trade_date") for n in ("margin", "margin_detail", "block_trade", "top_list")},
    "stk_holdernumber": Dataset("stk_holdernumber", "optional", "financial", "ann_date"),
    **{n: Dataset(n, "optional", "security_history", "ann_date") for n in ("stk_holdertrade", "share_float")},
    "stk_surv": Dataset("stk_surv", "optional", "security_history", "surv_date"),
}


def dependency_order(names: list[str]) -> list[str]:
    ordered: list[str] = []
    visiting: set[str] = set()

    def visit(name: str) -> None:
        if name in visiting:
            raise ValueError("dataset dependency cycle")
        if name in ordered:
            return
        visiting.add(name)
        for dep in DATASETS[name].dependencies:
            if dep in names:
                visit(dep)
        visiting.remove(name)
        ordered.append(name)

    for name in names:
        visit(name)
    return ordered


class DownloadError(RuntimeError):
    pass


class TushareClient:
    def __init__(self, config: dict, client: Any = None, sleeper=time.sleep):
        self.config = config["download"]
        self.sleeper = sleeper
        self.last_call = 0.0
        self.token = os.environ.get("TUSHARE_TOKEN", "")
        if client is None:
            if not self.token:
                raise DownloadError("TUSHARE_TOKEN is required in the environment")
            import tushare as ts
            client = ts.pro_api(self.token)
        self.client = client

    def redact(self, exc: Exception) -> str:
        message = str(exc)
        return message.replace(self.token, "[REDACTED]") if self.token else message

    def query(self, endpoint: str, **params: Any) -> pd.DataFrame:
        for attempt in range(self.config["retries"]):
            spacing = 60 / self.config["requests_per_minute"]
            self.sleeper(max(0, spacing - (time.monotonic() - self.last_call)))
            self.last_call = time.monotonic()
            try:
                result = self.client.query(endpoint, **params)
                if not isinstance(result, pd.DataFrame):
                    raise DownloadError("API returned a non-DataFrame response")
                return result
            except Exception as exc:
                if attempt + 1 == self.config["retries"]:
                    raise DownloadError(f"{endpoint}: {self.redact(exc)}") from None
                self.sleeper(self.config["retry_seconds"] * 2 ** attempt)
        raise DownloadError(endpoint)

    def fetch(self, endpoint: str, **params: Any) -> pd.DataFrame:
        """Offset pagination, fail closed on ignored offsets or runaway results."""
        if endpoint == "stock_basic":
            params.setdefault("fields", STOCK_BASIC_FIELDS)
        pages, fingerprints, offset = [], set(), 0
        limit = min(self.config["page_size"], 1000) if endpoint == "stock_st" else self.config["page_size"]
        for _ in range(self.config["max_pages"]):
            page = self.query(endpoint, **params, limit=limit, offset=offset)
            fingerprint = hashlib.sha256(page.to_json().encode()).hexdigest()
            if len(page) and fingerprint in fingerprints:
                raise DownloadError(f"{endpoint}: repeated page; offset pagination unsupported")
            fingerprints.add(fingerprint)
            pages.append(page)
            if len(page) < limit:
                return pd.concat(pages, ignore_index=True)
            offset += len(page)
        raise DownloadError(f"{endpoint}: pagination exceeded safety bound")

    def audit(self, root: Path, day: str) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        results: dict = {"detected_at": now, "interfaces": {}}
        probes: dict[str, dict] = {
            "stock_basic": {"list_status": "L"},
            "trade_cal": {"exchange": "SSE", "start_date": day[:4] + "0101", "end_date": day},
        }
        # Use the latest closed session, not an assumed weekday.
        try:
            calendar = self.fetch("trade_cal", **probes["trade_cal"])
            opens = calendar.loc[pd.to_numeric(calendar["is_open"]) == 1, "cal_date"]
            probe_day = opens.max()
            if not isinstance(probe_day, str):
                raise DownloadError("trade_cal: no open sessions")
        except Exception as exc:
            probe_day = day
            results["calendar_probe_error"] = self.redact(exc)
        for name, spec in DATASETS.items():
            if spec.strategy == "derived":
                continue
            params = probes.get(name, {"trade_date": probe_day})
            if spec.strategy in ("financial", "security_history"):
                params = {"ts_code": "000001.SZ"}
            elif spec.strategy == "index_metadata":
                params = {"market": "SSE"}
            elif spec.strategy == "index_history":
                params = {("index_code" if name == "index_weight" else "ts_code"): "000300.SH",
                          "start_date": str(int(day[:4]) - 1) + "0101", "end_date": day}
            elif spec.strategy == "industry_metadata":
                continue
            elif spec.strategy == "industry_history":
                params = {"ts_code": "000001.SZ"}
            try:
                frame = self.query(name, **params, limit=10, offset=0)
                if name in CRITICAL and frame.empty:
                    raise DownloadError("critical probe returned no rows")
                results["interfaces"][name] = {"status": "AVAILABLE", "tier": spec.tier,
                                              "permission_status": "GRANTED", "probe_rows": len(frame), "empty_probe": frame.empty}
            except Exception as exc:
                reason = self.redact(exc)
                denied = any(word in reason.lower() for word in ("权限", "积分", "permission", "denied"))
                results["interfaces"][name] = {"status": "UNAVAILABLE", "tier": spec.tier,
                                              "permission_status": "DENIED" if denied else "UNKNOWN", "reason": reason}
        results["industry_systems"] = {}
        for src in ("SW2021", "SW2014", "CITICS"):
            try:
                frame = self.query("index_classify", src=src, limit=10)
                status = "AVAILABLE" if not frame.empty else "EMPTY"
                results["industry_systems"][src] = {"status": status, "probe_rows": len(frame)}
            except Exception as exc:
                results["industry_systems"][src] = {"status": "UNAVAILABLE", "reason": self.redact(exc)}
        results["interfaces"]["index_classify"] = {"status": "AVAILABLE" if any(v["status"] == "AVAILABLE" for v in results["industry_systems"].values()) else "UNAVAILABLE", "tier": "required"}
        results["candidate_datasets"] = ["pledge_stat", "pledge_detail", "repurchase"]
        directory = root / "metadata/capabilities"
        atomic_json(directory / f"{now.replace(':', '-')}.json", results)
        atomic_json(directory / "latest.json", results)
        missing = [n for n in CRITICAL if results["interfaces"][n]["status"] != "AVAILABLE"]
        if missing:
            raise DownloadError(f"Critical capability audit FAILED: {', '.join(missing)}")
        return results
