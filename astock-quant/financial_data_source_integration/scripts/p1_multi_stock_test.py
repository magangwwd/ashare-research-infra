"""
P1: 20-stock multi-board stability test.

Tests mootdx / akshare / cninfo across 主板, 创业板, 科创板 to produce
a field dictionary, latency stats, and rate-limit log.

Usage:
    python p1_multi_stock_test.py              # full run (~6 min)
    python p1_multi_stock_test.py --quick      # bars + batch-quotes + news only
    python p1_multi_stock_test.py --no-pdf     # skip PDF byte-reads
    python p1_multi_stock_test.py --dry-run    # print plan, do nothing

Outputs (financial_data_source_integration/results/):
    TIMESTAMP_p1_summary.csv      per-probe records
    TIMESTAMP_p1_fields.json      field dictionary per interface
    TIMESTAMP_p1_report.md        human-readable summary
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT / "results"

SAMPLE_START = "20260401"
SAMPLE_END = "20260505"

# 20-stock sample: 8 主板SZ + 7 主板SH (random seed=42 from filtered pool)
# + 3 创业板 + 2 科创板 (manual, for board coverage)
P1_STOCKS: list[str] = [
    # 主板 SZ
    "002997.SZ", "002365.SZ", "002240.SZ", "002572.SZ",
    "002953.SZ", "002945.SZ", "000807.SZ", "002196.SZ",
    # 主板 SH
    "603086.SH", "600460.SH", "600409.SH", "603097.SH",
    "603282.SH", "601608.SH", "600642.SH",
    # 创业板 (manually added for board coverage)
    "300750.SZ", "300122.SZ", "300015.SZ",
    # 科创板 (manually added for board coverage)
    "688036.SH", "688111.SH",
]

# Limits for heavy interfaces
RESEARCH_LIMIT = 5    # research reports: ~13s each
PDF_LIMIT = 3         # PDF byte-reads: ~15s each
NEWS_LIMIT = 10       # news per stock
ANNOUNCE_LIMIT = 10   # cninfo announcements per stock
ANNOUNCE_PDF_LIMIT = 3

RATE_LIMIT_SLEEP = 1.2  # seconds between sequential calls to same source


# ---------------------------------------------------------------------------
# Code-format helpers
# ---------------------------------------------------------------------------

def bare(ts_code: str) -> str:
    """'000001.SZ' -> '000001'"""
    return ts_code.split(".")[0]


def market_suffix(ts_code: str) -> str:
    return ts_code.split(".")[1]  # 'SZ' | 'SH'


def to_akshare_hist(ts_code: str) -> str:
    """Akshare stock_zh_a_hist uses bare code without prefix."""
    return bare(ts_code)


def to_mootdx_symbol(ts_code: str) -> str:
    """mootdx bars/finance/xdxr use bare code; market auto-detected."""
    return bare(ts_code)


def to_cninfo_params(ts_code: str) -> tuple[str, str]:
    """Return (column, stock_param) for cninfo hisAnnouncement/query."""
    code = bare(ts_code)
    if market_suffix(ts_code) == "SZ":
        return "szse", f"{code},gssz0{code}"
    else:
        return "sse", f"{code},gssh0{code}"


# ---------------------------------------------------------------------------
# Result record
# ---------------------------------------------------------------------------

@dataclass
class ProbeRecord:
    source: str
    function: str
    ts_code: str
    called_at: str
    elapsed_ms: int
    rows: int
    columns: list[str]
    status: str          # pass | empty | error | config_required | skipped
    error: str = ""
    notes: str = ""


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------

def _import(name: str) -> Any:
    import importlib
    try:
        return importlib.import_module(name)
    except Exception as exc:
        raise RuntimeError(f"import failed: {name}: {exc}") from exc


def _frame_cols_rows(obj: Any) -> tuple[int, list[str]]:
    try:
        import pandas as pd
    except Exception:
        pd = None  # type: ignore[assignment]
    if pd is not None and isinstance(obj, pd.DataFrame):
        return len(obj), [str(c) for c in obj.columns]
    if isinstance(obj, list):
        cols: set[str] = set()
        for row in obj[:5]:
            if isinstance(row, dict):
                cols.update(str(k) for k in row)
        return len(obj), sorted(cols)
    if isinstance(obj, dict):
        for key in ("announcements", "data", "rows", "result"):
            val = obj.get(key)
            if isinstance(val, list):
                cols = set()
                for row in val[:5]:
                    if isinstance(row, dict):
                        cols.update(str(k) for k in row)
                return len(val), sorted(cols)
        return 1, sorted(str(k) for k in obj)
    return (1 if obj is not None else 0), []


def _run(source: str, function: str, ts_code: str, fn: Any) -> ProbeRecord:
    t0 = time.perf_counter()
    called_at = datetime.now().isoformat(timespec="seconds")
    try:
        obj = fn()
        rows, cols = _frame_cols_rows(obj)
        status = "pass" if rows >= 1 else "empty"
        return ProbeRecord(source, function, ts_code, called_at,
                           int((time.perf_counter() - t0) * 1000),
                           rows, cols, status)
    except Exception as exc:
        return ProbeRecord(source, function, ts_code, called_at,
                           int((time.perf_counter() - t0) * 1000),
                           0, [], "error", str(exc)[:300])


def probe_mootdx_bars(ts_code: str) -> ProbeRecord:
    def _fn() -> Any:
        m = _import("mootdx.quotes")
        client = m.Quotes.factory(market="std", multithread=False)
        return client.bars(symbol=to_mootdx_symbol(ts_code), frequency=9, offset=20)
    return _run("mootdx", "bars", ts_code, _fn)


def probe_mootdx_quotes_batch(ts_codes: list[str]) -> ProbeRecord:
    """Single batch call for all stocks; recorded under ts_code='batch'."""
    symbols = [bare(c) for c in ts_codes]
    def _fn() -> Any:
        m = _import("mootdx.quotes")
        client = m.Quotes.factory(market="std", multithread=False)
        return client.quotes(symbol=symbols)
    return _run("mootdx", "quotes_batch", f"batch_{len(ts_codes)}", _fn)


def probe_akshare_hist(ts_code: str) -> ProbeRecord:
    def _fn() -> Any:
        ak = _import("akshare")
        return ak.stock_zh_a_hist(
            symbol=to_akshare_hist(ts_code),
            period="daily",
            start_date=SAMPLE_START,
            end_date=SAMPLE_END,
            adjust="",
        )
    return _run("akshare", "stock_zh_a_hist", ts_code, _fn)


def probe_akshare_research(ts_code: str) -> ProbeRecord:
    def _fn() -> Any:
        ak = _import("akshare")
        return ak.stock_research_report_em(symbol=bare(ts_code))
    return _run("akshare", "stock_research_report_em", ts_code, _fn)


def probe_research_pdf(ts_code: str) -> ProbeRecord:
    def _fn() -> Any:
        ak = _import("akshare")
        requests = _import("requests")
        df = ak.stock_research_report_em(symbol=bare(ts_code))
        if "报告PDF链接" not in df.columns:
            raise RuntimeError("missing 报告PDF链接 column")
        links = df["报告PDF链接"].dropna()
        if links.empty:
            return []
        url = str(links.iloc[0])
        resp = requests.get(url, headers={"Range": "bytes=0-4095", "User-Agent": "Mozilla/5.0"},
                            timeout=25, stream=True)
        resp.raise_for_status()
        first = next(resp.iter_content(chunk_size=4096), b"")
        return [{"url": url, "status_code": resp.status_code,
                 "content_type": resp.headers.get("Content-Type", ""),
                 "bytes_read": len(first)}]
    return _run("eastmoney-pdf", "research_pdf_bytes", ts_code, _fn)


def probe_akshare_news(ts_code: str) -> ProbeRecord:
    def _fn() -> Any:
        ak = _import("akshare")
        return ak.stock_news_em(symbol=bare(ts_code))
    return _run("akshare", "stock_news_em", ts_code, _fn)


def probe_cninfo_announcements(ts_code: str) -> ProbeRecord:
    def _fn() -> Any:
        requests = _import("requests")
        column, stock_param = to_cninfo_params(ts_code)
        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Origin": "https://www.cninfo.com.cn",
            "Referer": "https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
            "User-Agent": "Mozilla/5.0",
            "X-Requested-With": "XMLHttpRequest",
        }
        payload = {
            "pageNum": "1", "pageSize": "30",
            "column": column, "tabName": "fulltext",
            "plate": "", "stock": stock_param,
            "searchkey": "", "secid": "", "category": "",
            "trade": "", "seDate": "", "sortName": "",
            "sortType": "", "isHLtitle": "true",
        }
        resp = requests.post(
            "https://www.cninfo.com.cn/new/hisAnnouncement/query",
            data=payload, headers=headers, timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and data.get("announcements"):
            return data["announcements"]
        return data
    return _run("cninfo", "hisAnnouncement/query", ts_code, _fn)


def probe_cninfo_pdf(ts_code: str) -> ProbeRecord:
    def _fn() -> Any:
        requests = _import("requests")
        column, stock_param = to_cninfo_params(ts_code)
        headers_post = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Origin": "https://www.cninfo.com.cn",
            "Referer": "https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
            "User-Agent": "Mozilla/5.0",
            "X-Requested-With": "XMLHttpRequest",
        }
        payload = {
            "pageNum": "1", "pageSize": "30",
            "column": column, "tabName": "fulltext",
            "plate": "", "stock": stock_param,
            "searchkey": "", "secid": "", "category": "",
            "trade": "", "seDate": "", "sortName": "",
            "sortType": "", "isHLtitle": "true",
        }
        resp = requests.post(
            "https://www.cninfo.com.cn/new/hisAnnouncement/query",
            data=payload, headers=headers_post, timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("announcements", []) if isinstance(data, dict) else []
        first = next((x for x in items if isinstance(x, dict) and x.get("adjunctUrl")), None)
        if not first:
            return []
        url = "https://static.cninfo.com.cn/" + str(first["adjunctUrl"]).lstrip("/")
        pdf_resp = requests.get(
            url,
            headers={"Range": "bytes=0-4095", "User-Agent": "Mozilla/5.0"},
            timeout=25, stream=True,
        )
        pdf_resp.raise_for_status()
        first_bytes = next(pdf_resp.iter_content(chunk_size=4096), b"")
        return [{"url": url, "status_code": pdf_resp.status_code,
                 "content_type": pdf_resp.headers.get("Content-Type", ""),
                 "bytes_read": len(first_bytes)}]
    return _run("cninfo-pdf", "announcement_pdf_bytes", ts_code, _fn)


# ---------------------------------------------------------------------------
# Field dictionary builder
# ---------------------------------------------------------------------------

def build_field_dict(records: list[ProbeRecord]) -> dict[str, Any]:
    """
    For each (source, function) pair, collect all column sets and report:
    - common_columns: present in every passing call
    - variable_columns: present in some but not all
    - total_stocks_tested
    """
    from collections import Counter

    groups: dict[tuple[str, str], list[list[str]]] = defaultdict(list)
    for r in records:
        if r.status == "pass" and r.columns:
            groups[(r.source, r.function)].append(r.columns)

    result: dict[str, Any] = {}
    for (source, function), col_sets in groups.items():
        if not col_sets:
            continue
        counter: Counter[str] = Counter()
        n = len(col_sets)
        for cols in col_sets:
            for c in cols:
                counter[c] += 1
        common = sorted(c for c, cnt in counter.items() if cnt == n)
        variable = sorted(c for c, cnt in counter.items() if cnt < n)
        result[f"{source}::{function}"] = {
            "stocks_tested": n,
            "common_columns": common,
            "variable_columns": {c: counter[c] for c in variable},
        }
    return result


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------

def write_outputs(records: list[ProbeRecord], field_dict: dict[str, Any],
                  metadata: dict[str, Any]) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")

    csv_path = RESULTS_DIR / f"{stamp}_p1_summary.csv"
    fields_path = RESULTS_DIR / f"{stamp}_p1_fields.json"
    md_path = RESULTS_DIR / f"{stamp}_p1_report.md"

    fieldnames = ["source", "function", "ts_code", "called_at", "elapsed_ms",
                  "rows", "status", "error", "notes"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for r in records:
            writer.writerow({
                "source": r.source, "function": r.function,
                "ts_code": r.ts_code, "called_at": r.called_at,
                "elapsed_ms": r.elapsed_ms, "rows": r.rows,
                "status": r.status, "error": r.error, "notes": r.notes,
            })

    with fields_path.open("w", encoding="utf-8") as fh:
        json.dump({"metadata": metadata, "field_dict": field_dict},
                  fh, ensure_ascii=False, indent=2)

    # latency summary per (source, function)
    lat: dict[tuple[str, str], list[int]] = defaultdict(list)
    status_cnt: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in records:
        key = (r.source, r.function)
        if r.status not in ("skipped",):
            lat[key].append(r.elapsed_ms)
        status_cnt[key][r.status] += 1

    lines = [
        "# P1 多标的稳定性测试报告",
        "",
        f"- 测试时间：{metadata['started_at']}",
        f"- Python：{metadata['python']}",
        f"- 平台：{metadata['platform']}",
        f"- 股票数：{len(metadata['stocks'])}（主板SZ×8 主板SH×7 创业板×3 科创板×2）",
        "",
        "## 包版本",
        "",
    ]
    for name, ver in metadata["package_versions"].items():
        lines.append(f"- `{name}`: `{ver}`")

    lines += [
        "",
        "## 接口耗时与通过率",
        "",
        "| source | function | n | pass | empty | error | p50_ms | p95_ms | max_ms |",
        "|--------|----------|---|------|-------|-------|--------|--------|--------|",
    ]
    for key, timings in sorted(lat.items()):
        source, function = key
        sc = status_cnt[key]
        n = sum(sc.values())
        timings_sorted = sorted(timings)
        p50 = timings_sorted[len(timings_sorted) // 2] if timings_sorted else 0
        p95_idx = int(len(timings_sorted) * 0.95)
        p95 = timings_sorted[min(p95_idx, len(timings_sorted) - 1)] if timings_sorted else 0
        mx = max(timings_sorted) if timings_sorted else 0
        lines.append(
            f"| {source} | {function} | {n} | {sc['pass']} | {sc['empty']} | {sc['error']} "
            f"| {p50} | {p95} | {mx} |"
        )

    lines += [
        "",
        "## 字段一致性",
        "",
        "各接口跨标的字段变化情况（`variable_columns` 非空说明字段覆盖不稳定）：",
        "",
    ]
    for iface, info in sorted(field_dict.items()):
        n_var = len(info["variable_columns"])
        lines.append(
            f"- **{iface}**: {info['stocks_tested']} 只, "
            f"共同列 {len(info['common_columns'])} 个, "
            f"可变列 {n_var} 个"
            + (" ⚠️" if n_var > 0 else "")
        )

    lines += [
        "",
        "## 错误汇总",
        "",
        "| source | function | ts_code | error |",
        "|--------|----------|---------|-------|",
    ]
    errors = [r for r in records if r.status == "error"]
    if errors:
        for r in errors:
            lines.append(f"| {r.source} | {r.function} | {r.ts_code} | {r.error[:120].replace('|','/')} |")
    else:
        lines.append("| — | — | — | 无错误 |")

    lines += ["", f"详细记录：`{csv_path.name}`", f"字段字典：`{fields_path.name}`", ""]
    md_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"report : {md_path}")
    print(f"csv    : {csv_path}")
    print(f"fields : {fields_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _pkg_ver(name: str) -> str:
    import importlib
    try:
        m = importlib.import_module(name)
        return str(getattr(m, "__version__", "unknown"))
    except Exception:
        return "missing"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--quick", action="store_true",
                   help="bars + batch-quotes + news only (faster)")
    p.add_argument("--no-pdf", action="store_true",
                   help="skip PDF byte-read probes")
    p.add_argument("--dry-run", action="store_true",
                   help="print test plan without running")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    stocks = P1_STOCKS

    plan: list[tuple[str, str, list[str]]] = []
    plan.append(("mootdx", "bars", stocks))
    plan.append(("mootdx", "quotes_batch", stocks))
    plan.append(("akshare", "stock_zh_a_hist", stocks))
    if not args.quick:
        plan.append(("akshare", "stock_research_report_em", stocks[:RESEARCH_LIMIT]))
        if not args.no_pdf:
            plan.append(("eastmoney-pdf", "research_pdf_bytes", stocks[:PDF_LIMIT]))
        plan.append(("akshare", "stock_news_em", stocks[:NEWS_LIMIT]))
        plan.append(("cninfo", "hisAnnouncement/query", stocks[:ANNOUNCE_LIMIT]))
        if not args.no_pdf:
            plan.append(("cninfo-pdf", "announcement_pdf_bytes", stocks[:ANNOUNCE_PDF_LIMIT]))
    else:
        plan.append(("akshare", "stock_news_em", stocks[:NEWS_LIMIT]))

    if args.dry_run:
        print("P1 test plan:")
        total = 0
        for source, function, sample in plan:
            n = 1 if function == "quotes_batch" else len(sample)
            print(f"  {source}.{function}: {n} call(s) over {sample if function != 'quotes_batch' else ['batch']}")
            total += n
        print(f"Total calls: {total}")
        return 0

    metadata = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "stocks": stocks,
        "quick": args.quick,
        "no_pdf": args.no_pdf,
        "package_versions": {n: _pkg_ver(n) for n in
                              ["pandas", "requests", "mootdx", "akshare", "tushare"]},
    }

    all_records: list[ProbeRecord] = []

    for source, function, sample in plan:
        if function == "quotes_batch":
            print(f"  mootdx.quotes_batch (n={len(stocks)})")
            rec = probe_mootdx_quotes_batch(stocks)
            all_records.append(rec)
            print(f"    -> {rec.status} ({rec.elapsed_ms}ms, rows={rec.rows})")
            time.sleep(RATE_LIMIT_SLEEP)
            continue

        probe_fn = {
            "bars": probe_mootdx_bars,
            "stock_zh_a_hist": probe_akshare_hist,
            "stock_research_report_em": probe_akshare_research,
            "research_pdf_bytes": probe_research_pdf,
            "stock_news_em": probe_akshare_news,
            "hisAnnouncement/query": probe_cninfo_announcements,
            "announcement_pdf_bytes": probe_cninfo_pdf,
        }[function]

        print(f"\n{source}.{function} ({len(sample)} stocks)")
        for i, ts_code in enumerate(sample):
            if i > 0:
                time.sleep(RATE_LIMIT_SLEEP)
            rec = probe_fn(ts_code)
            all_records.append(rec)
            flag = "OK" if rec.status == "pass" else f"!! {rec.status}"
            print(f"  {ts_code:12s} {flag:30s} {rec.elapsed_ms:6d}ms  rows={rec.rows}")

    field_dict = build_field_dict(all_records)
    write_outputs(all_records, field_dict, metadata)

    errors = [r for r in all_records if r.status == "error"]
    if errors:
        print(f"\n{len(errors)} error(s) — see report for details")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
