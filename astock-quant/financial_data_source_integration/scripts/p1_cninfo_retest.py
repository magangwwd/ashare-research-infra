"""
P1 补测：cninfo 公告查询间隔调整后重测。

修复策略：
- 首次请求前 GET 主页拿 session cookie
- 请求间隔 5–8s + 随机抖动
- 单 session 最多 5 只后重建

Usage:
    python p1_cninfo_retest.py
"""
from __future__ import annotations

import csv
import json
import random
import time
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT / "results"

CNINFO_HOME = "https://www.cninfo.com.cn"
CNINFO_QUERY = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_STATIC = "https://static.cninfo.com.cn"

# Same 10 stocks as P1 announcement sample
ANNOUNCE_STOCKS = [
    "002997.SZ", "002365.SZ", "002240.SZ", "002572.SZ", "002953.SZ",
    "002945.SZ", "000807.SZ", "002196.SZ", "603086.SH", "600460.SH",
]

SESSION_RESET_EVERY = 5    # rebuild session after this many requests
MIN_SLEEP = 5.0
MAX_SLEEP = 8.0


def bare(ts_code: str) -> str:
    return ts_code.split(".")[0]


def to_cninfo_params(ts_code: str) -> tuple[str, str]:
    code = bare(ts_code)
    if ts_code.endswith(".SZ"):
        return "szse", f"{code},gssz0{code}"
    else:
        return "sse", f"{code},gssh0{code}"


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    try:
        resp = s.get(CNINFO_HOME, timeout=15)
        resp.raise_for_status()
        print(f"  session init: {resp.status_code}, cookies={dict(s.cookies)}")
    except Exception as exc:
        print(f"  session init failed (continuing): {exc}")
    return s


def query_announcements(session: requests.Session, ts_code: str) -> tuple[str, int, str, list[dict]]:
    """Returns (status, rows, error, items)."""
    column, stock_param = to_cninfo_params(ts_code)
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Origin": CNINFO_HOME,
        "Referer": f"{CNINFO_HOME}/new/commonUrl/pageOfSearch?url=disclosure/list/search",
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
    try:
        resp = session.post(CNINFO_QUERY, data=payload, headers=headers, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("announcements", []) if isinstance(data, dict) else []
        rows = len(items) if isinstance(items, list) else 0
        return ("pass" if rows >= 1 else "empty"), rows, "", items
    except Exception as exc:
        return "error", 0, str(exc)[:300], []


def fetch_pdf_bytes(session: requests.Session, adjunct_url: str) -> tuple[str, str]:
    url = CNINFO_STATIC + "/" + adjunct_url.lstrip("/")
    try:
        resp = session.get(
            url,
            headers={"Range": "bytes=0-4095"},
            timeout=25,
            stream=True,
        )
        resp.raise_for_status()
        first = next(resp.iter_content(chunk_size=4096), b"")
        return "pass", f"{resp.status_code} {resp.headers.get('Content-Type','')} {len(first)}b"
    except Exception as exc:
        return "error", str(exc)[:200]


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_csv = RESULTS_DIR / f"{stamp}_cninfo_retest.csv"
    out_md = RESULTS_DIR / f"{stamp}_cninfo_retest.md"

    records: list[dict] = []
    session = make_session()
    req_count = 0

    for i, ts_code in enumerate(ANNOUNCE_STOCKS):
        if req_count > 0 and req_count % SESSION_RESET_EVERY == 0:
            print(f"\n  rebuilding session after {req_count} requests")
            session.close()
            time.sleep(3.0)
            session = make_session()

        sleep_s = random.uniform(MIN_SLEEP, MAX_SLEEP)
        if i > 0:
            print(f"  sleeping {sleep_s:.1f}s ...")
            time.sleep(sleep_s)

        t0 = time.perf_counter()
        called_at = datetime.now().isoformat(timespec="seconds")
        status, rows, error, items = query_announcements(session, ts_code)
        elapsed = int((time.perf_counter() - t0) * 1000)
        req_count += 1

        flag = "OK" if status == "pass" else f"!! {status}"
        print(f"  {ts_code:12s} {flag:20s} {elapsed:7d}ms  rows={rows}")

        pdf_status, pdf_note = "", ""
        if status == "pass" and items:
            first = next((x for x in items if isinstance(x, dict) and x.get("adjunctUrl")), None)
            if first:
                time.sleep(random.uniform(2.0, 3.0))
                pdf_status, pdf_note = fetch_pdf_bytes(session, first["adjunctUrl"])
                print(f"    pdf: {pdf_status} {pdf_note}")

        records.append({
            "ts_code": ts_code,
            "called_at": called_at,
            "elapsed_ms": elapsed,
            "rows": rows,
            "status": status,
            "error": error,
            "pdf_status": pdf_status,
            "pdf_note": pdf_note,
        })

    session.close()

    # write CSV
    with out_csv.open("w", encoding="utf-8-sig", newline="") as fh:
        fieldnames = ["ts_code","called_at","elapsed_ms","rows","status","error","pdf_status","pdf_note"]
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)

    # write MD
    passed = sum(1 for r in records if r["status"] == "pass")
    errors = [r for r in records if r["status"] == "error"]
    lines = [
        "# cninfo 公告补测结果",
        "",
        f"测试时间：{datetime.now().isoformat(timespec='seconds')}",
        f"间隔策略：{MIN_SLEEP}–{MAX_SLEEP}s + 随机抖动，每 {SESSION_RESET_EVERY} 只重建 session",
        "",
        "## 汇总",
        "",
        f"- 通过：{passed}/{len(records)}",
        f"- 错误：{len(errors)}",
        "",
        "## 逐只结果",
        "",
        "| ts_code | status | rows | elapsed_ms | pdf_status | error |",
        "|---------|--------|------|------------|------------|-------|",
    ]
    for r in records:
        lines.append(
            f"| {r['ts_code']} | {r['status']} | {r['rows']} | {r['elapsed_ms']} "
            f"| {r['pdf_status'] or '-'} | {r['error'][:80].replace('|','/')} |"
        )
    if errors:
        lines += ["", "## 错误详情", ""]
        for r in errors:
            lines.append(f"- **{r['ts_code']}**: {r['error']}")

    out_md.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nreport: {out_md}")
    print(f"csv   : {out_csv}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
