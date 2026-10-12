"""Read-only coverage-gated bridge for version-pinned, accepted finance Clean snapshots.

Never assumes a validated *partial* Tushare Clean dataset covers every security.
Never fetches remote data, executes broker actions or calls model APIs.
"""
from __future__ import annotations
import json
import math
import re
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

DATASETS = ("income", "balancesheet", "cashflow", "fina_indicator")
DATA_PATTERN = re.compile(r"^(income|balancesheet|cashflow|fina_indicator)_(\d{8})\.parquet$")
AUTHORIZATION = "PAPER_ONLY_NO_ENTRY_AUTHORIZATION"


def iso_day(value):
    if value is None or str(value).strip().lower() in ("none", "nan", "nat", "<na>"):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s or s.lower() in ("none", "nan", "nat", "<na>"):
        return None
    if re.fullmatch(r"\d{8}", s):
        return datetime.strptime(s, "%Y%m%d").date()
    return date.fromisoformat(s[:10])


def published_local_day(value):
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Snapshot published_at requires timezone")
    return stamp.astimezone(ZoneInfo("Asia/Shanghai")).date()


def validate_snapshot(root: Path, as_of: date) -> dict:
    """Require a point-in-time verifiable, immutable accepted snapshot.

    An EOD snapshot without an intraday timestamp is usable only after its
    Shanghai-local publication day, never retrospectively before publication.
    """
    ptr = root / "clean_current_20261005.json"
    if not ptr.is_file():
        raise FileNotFoundError("No version-pinned Clean pointer in bridge")
    marker = json.loads(ptr.read_text(encoding="utf-8-sig"))
    version = str(marker.get("version", ""))
    if (not marker.get("validated") or not re.fullmatch(r"[\w.:-]{10,120}", version)):
        raise ValueError("Invalid/unvalidated Clean pointer")
    if iso_day(marker.get("snapshot_as_of")) is None:
        raise ValueError("Missing Clean snapshot_as_of")
    if iso_day(marker["snapshot_as_of"]) > as_of:
        raise ValueError("Clean snapshot's data date is after research date")
    published = published_local_day(marker.get("published_at"))
    if published >= as_of:
        raise ValueError("Clean snapshot not safely published before research day")
    audit = root / "acceptance_v20261005.json"
    if not audit.is_file():
        raise FileNotFoundError("Missing Clean acceptance audit")
    accepted = json.loads(audit.read_text(encoding="utf-8-sig"))
    if accepted.get("passed") is not True:
        raise ValueError("Unaccepted Clean data")
    return {
        "status": "ACCEPTED_PARTIAL" if marker.get("critical_history_complete") is False
                                      else "ACCEPTED_COVERAGE_UNVERIFIED",
        "version": version,
        "snapshot_as_of": marker["snapshot_as_of"],
        "published_local_date": published.isoformat(),
        "critical_history_complete": marker.get("critical_history_complete"),
        "full_requested_core_range_complete": marker.get("full_requested_core_range_complete"),
        "original_version_folder_id": marker.get("version_folder_id"),
        "age_days": (as_of - iso_day(marker["snapshot_as_of"])).days,
    }


def number(value):
    try:
        out = float(value)
    except (ValueError, TypeError):
        return None
    return out if math.isfinite(out) else None


def nd(value, digits=3):
    return round(value, digits) if value is not None and math.isfinite(value) else None


def accepted_frame_rows(root: Path, symbols: set[str], as_of: date):
    import pandas as pd
    acc = {s: {ds: [] for ds in DATASETS} for s in symbols}
    inventory = []
    for p in sorted(root.glob("*.parquet")):
        match = DATA_PATTERN.fullmatch(p.name)
        if not match:
            raise ValueError("Unexpected parquet in finance staging: " + p.name)
        ds, period_key = match.groups()
        report_period = iso_day(period_key)
        if report_period > as_of:
            continue
        frame = pd.read_parquet(p)
        required = {"ts_code", "report_period", "ann_date", "actual_ann_date",
                    "available_date", "schema_version"}
        if not required.issubset(frame.columns):
            raise ValueError(ds + ": missing required Clean temporal columns")
        if frame["schema_version"].dropna().astype(str).nunique() > 1:
            raise ValueError(ds + ": inconsistent schema_version")
        inventory.append({"dataset": ds, "period": period_key, "rows": len(frame),
                          "target_symbols": int(frame["ts_code"].isin(symbols).sum())})
        selected = frame.loc[frame["ts_code"].isin(symbols)]
        for _, row in selected.iterrows():
            sym = str(row["ts_code"])
            if iso_day(row["report_period"]) != report_period:
                raise ValueError(ds + ": report_period mismatch")
            stated = iso_day(row["ann_date"])
            actual = iso_day(row["actual_ann_date"])
            available = iso_day(row["available_date"])
            if stated is None and actual is None:
                # Do not infer publication from period end or fetched time.
                continue
            effective = max(d for d in (stated, actual, available) if d is not None)
            # Date-only announcement cannot prove availability by same-day close.
            if effective >= as_of:
                continue
            acc[sym][ds].append({"row": row.to_dict(), "period": report_period,
                                 "effective": effective})
    return acc, inventory


def unique_latest(items, expected_period: date):
    matches = [a for a in items if a["period"] == expected_period]
    if not matches:
        return None
    matches.sort(key=lambda r: r["effective"])
    newest_date = matches[-1]["effective"]
    ties = [m for m in matches if m["effective"] == newest_date]
    if len(ties) > 1:
        # Genuine duplicates may represent distinct report_type/comp_type.
        # Without selecting a verified consolidated statement, fail closed.
        raise ValueError("Ambiguous multiple Clean reports for same symbol/period/date")
    return ties[0]


def clean_agent(acc: dict, symbol: str, as_of: date, meta: dict) -> dict:
    docs = acc.get(symbol, {s: [] for s in DATASETS})
    periods = sorted({a["period"] for ds in DATASETS for a in docs[ds]})
    if not periods:
        return {"status": "NO_DATED_FINANCIAL", "facts": {},
                "flags": ["Clean数据对该证券截至研究日无可用公告日期记录"],
                "coverage": {ds: False for ds in DATASETS}, "source": "Drive staged accepted Clean " + meta["version"]}
    period = periods[-1]
    observations = {ds: unique_latest(docs[ds], period) for ds in DATASETS}
    present = {ds: bool(observations[ds]) for ds in DATASETS}
    def field(dataset, name):
        item = observations.get(dataset)
        if item is None:
            return None
        return number(item["row"].get(name))
    dates = [entry["effective"] for entry in observations.values() if entry]
    ann_date = max(dates)
    revenue = field("income", "total_revenue")
    profit = field("income", "n_income_attr_p")
    op_cash = field("cashflow", "n_cashflow_act")
    assets = field("balancesheet", "total_assets")
    liabilities = field("balancesheet", "total_liab")
    cur_assets = field("balancesheet", "total_cur_assets")
    cur_liab = field("balancesheet", "total_cur_liab")
    roe = field("fina_indicator", "roe")
    debt = field("fina_indicator", "debt_to_assets")
    curr_ratio = field("fina_indicator", "current_ratio")
    margin = profit/revenue*100 if revenue and profit is not None else None
    flags = []
    missing = [ds for ds, has in present.items() if not has]
    if missing:
        flags.append("Clean该期缺少数据集：" + "、".join(missing))
    if meta["critical_history_complete"] is not True:
        flags.append("源快照完整历史覆盖未验收；只对已存在记录作观察")
    if meta["age_days"] > 14:
        flags.append("财务Clean源快照距研究日超过14天，可能尚未同步新披露")
    age = (as_of - ann_date).days
    if age > 150:
        flags.append("所用财务披露距研究日超过150天")
    if profit is not None and profit < 0:
        flags.append("归母净利润为负")
    if op_cash is not None and op_cash < 0:
        flags.append("经营现金流为负")
    if debt is None and assets and liabilities is not None:
        debt = 100*liabilities/assets
    if curr_ratio is None and cur_liab and cur_assets is not None:
        curr_ratio = cur_assets/cur_liab
    facts = {
        "financial_end_date": period.isoformat(),
        "announcement_date": ann_date.isoformat(),
        "announcement_age_days": age,
        "roe_pct": nd(roe),
        "debt_to_assets_pct": nd(debt),
        "current_ratio": nd(curr_ratio),
        "netprofit_margin_pct": nd(margin),
        "total_revenue_cny": nd(revenue, 2),
        "attributable_net_profit_cny": nd(profit, 2),
        "operating_cashflow_cny": nd(op_cash, 2),
        "deducted_net_profit_cny": nd(field("fina_indicator", "profit_dedt"), 2),
        "gross_margin_pct": nd(field("fina_indicator", "gross_margin")),
        "cash_to_attributable_profit": nd(op_cash/profit, 3) if op_cash is not None and profit else None,
        "clean_snapshot_version": meta["version"],
        "statement_announcement_dates": {
            ds: observations[ds]["effective"].isoformat() if observations[ds] else None
            for ds in DATASETS
        },
    }
    return {"status": ("CLEAN_PIT_COMPLETE" if all(present.values()) else "CLEAN_PIT_PARTIAL"),
            "facts": facts, "coverage": present, "flags": flags,
            "source": "Drive staged accepted Clean " + meta["version"]}


def evaluate(root: Path, as_of: date, symbols: set[str]):
    meta = validate_snapshot(root, as_of)
    indexed, inventory = accepted_frame_rows(root, symbols, as_of)
    agents = {sym: clean_agent(indexed, sym, as_of, meta) for sym in sorted(symbols)}
    available = {sym: agents[sym]["status"] != "NO_DATED_FINANCIAL"
                 for sym in sorted(symbols)}
    coverage = {
        "financial_source": "ACCEPTED_VERSION_PINNED_CLEAN_STAGING",
        "authorization": AUTHORIZATION,
        "as_of": as_of.isoformat(),
        "snapshot": meta,
        "files": inventory,
        "symbols": {s: {"has_any_clean": available[s],
                        "dataset_coverage": agents[s]["coverage"],
                        "selected_report_period": agents[s]["facts"].get("financial_end_date")}
                    for s in sorted(symbols)},
        "all_target_symbols_covered": all(available.values()),
    }
    return agents, coverage
