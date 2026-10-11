from __future__ import annotations

import csv
import importlib
import json
import os
import platform
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT / "results"

SAMPLE_START = "20260401"
SAMPLE_END = "20260505"
CNINFO_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"


class ConfigRequired(RuntimeError):
    pass


@dataclass
class Probe:
    source: str
    function: str
    sample: str
    runner: Callable[[], Any]
    attempts: int = 1
    min_rows: int = 1


def import_required(module_name: str) -> Any:
    try:
        return importlib.import_module(module_name)
    except Exception as exc:  # pragma: no cover - smoke helper
        raise ConfigRequired(f"import failed: {module_name}: {exc}") from exc


def frame_summary(obj: Any) -> tuple[int, list[str], Any]:
    try:
        import pandas as pd
    except Exception:
        pd = None

    if pd is not None and isinstance(obj, pd.DataFrame):
        rows = int(len(obj))
        columns = [str(col) for col in obj.columns]
        preview = obj.head(3).astype(str).to_dict(orient="records")
        return rows, columns, preview

    if isinstance(obj, list):
        preview = obj[:3]
        columns = sorted({str(key) for row in preview if isinstance(row, dict) for key in row.keys()})
        return len(obj), columns, preview

    if isinstance(obj, dict):
        rows = 1
        columns = sorted(str(key) for key in obj.keys())
        preview = {key: obj[key] for key in list(obj.keys())[:5]}
        for key in ("announcements", "data", "rows", "result"):
            value = obj.get(key)
            if isinstance(value, list):
                rows = len(value)
                preview = value[:3]
                columns = sorted({str(item_key) for row in preview if isinstance(row, dict) for item_key in row.keys()})
                break
        return rows, columns, preview

    return 1 if obj is not None else 0, [], str(obj)[:500]


def run_probe(probe: Probe) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    records: list[dict[str, Any]] = []
    preview_record: dict[str, Any] | None = None
    for attempt in range(1, probe.attempts + 1):
        started = time.perf_counter()
        called_at = datetime.now().isoformat(timespec="seconds")
        status = "error"
        error = ""
        rows = 0
        columns: list[str] = []
        preview: Any = None
        try:
            obj = probe.runner()
            rows, columns, preview = frame_summary(obj)
            status = "pass" if rows >= probe.min_rows else "empty"
        except ConfigRequired as exc:
            status = "config_required"
            error = str(exc)
        except Exception as exc:  # pragma: no cover - smoke helper
            error = f"{type(exc).__name__}: {exc}"

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        record = {
            "source": probe.source,
            "function": probe.function,
            "sample": probe.sample,
            "attempt": attempt,
            "attempts": probe.attempts,
            "called_at": called_at,
            "elapsed_ms": elapsed_ms,
            "rows": rows,
            "columns": "|".join(columns[:25]),
            "status": status,
            "error": error[:500],
        }
        records.append(record)
        if preview_record is None and status == "pass":
            preview_record = {
                "source": probe.source,
                "function": probe.function,
                "sample": probe.sample,
                "rows": rows,
                "columns": columns,
                "preview": preview,
            }
        if attempt < probe.attempts:
            time.sleep(1.0)
    return records, preview_record


def probe_mootdx_bars() -> Any:
    quotes_mod = import_required("mootdx.quotes")
    client = quotes_mod.Quotes.factory(market="std", multithread=False)
    return client.bars(symbol="600000", frequency=9, offset=20)


def probe_mootdx_quotes() -> Any:
    quotes_mod = import_required("mootdx.quotes")
    client = quotes_mod.Quotes.factory(market="std", multithread=False)
    return client.quotes(symbol=["000001", "600000"])


def probe_mootdx_finance() -> Any:
    quotes_mod = import_required("mootdx.quotes")
    client = quotes_mod.Quotes.factory(market="std", multithread=False)
    return client.finance(symbol="600000")


def probe_mootdx_xdxr() -> Any:
    quotes_mod = import_required("mootdx.quotes")
    client = quotes_mod.Quotes.factory(market="std", multithread=False)
    return client.xdxr(symbol="600000")


def probe_ak_hist() -> Any:
    ak = import_required("akshare")
    return ak.stock_zh_a_hist(
        symbol="000001",
        period="daily",
        start_date=SAMPLE_START,
        end_date=SAMPLE_END,
        adjust="",
    )


def probe_ak_hist_tx() -> Any:
    ak = import_required("akshare")
    return ak.stock_zh_a_hist_tx(
        symbol="sz000001",
        start_date=SAMPLE_START,
        end_date=SAMPLE_END,
        adjust="",
    )


def probe_ak_research() -> Any:
    ak = import_required("akshare")
    return ak.stock_research_report_em(symbol="000001")


def probe_ak_stock_news() -> Any:
    ak = import_required("akshare")
    return ak.stock_news_em(symbol="600000")


def probe_ak_global_news_em() -> Any:
    ak = import_required("akshare")
    return ak.stock_info_global_em()


def probe_ak_global_news_cls() -> Any:
    ak = import_required("akshare")
    return ak.stock_info_global_cls(symbol="全部")


def probe_tushare_stock_basic() -> Any:
    ts = import_required("tushare")
    pro = ts.pro_api()
    return pro.stock_basic(
        exchange="",
        list_status="L",
        fields="ts_code,symbol,name,area,industry,list_date",
    )


def probe_pywencai() -> Any:
    cookie = os.environ.get("IWENCAI_COOKIE")
    if not cookie:
        raise ConfigRequired("IWENCAI_COOKIE is not set")
    pywencai = import_required("pywencai")
    return pywencai.get(query="近一月机构评级上调的银行股", cookie=cookie)


def probe_cninfo_announcements() -> Any:
    requests = import_required("requests")
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Origin": "https://www.cninfo.com.cn",
        "Referer": "https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
        "User-Agent": "Mozilla/5.0",
        "X-Requested-With": "XMLHttpRequest",
    }
    payload = {
        "pageNum": "1",
        "pageSize": "30",
        "column": "szse",
        "tabName": "fulltext",
        "plate": "",
        "stock": "000001,gssz0000001",
        "searchkey": "",
        "secid": "",
        "category": "",
        "trade": "",
        "seDate": "",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    response = requests.post(CNINFO_URL, data=payload, headers=headers, timeout=20)
    response.raise_for_status()
    data = response.json()
    if isinstance(data, dict) and data.get("announcements"):
        return data["announcements"]
    return data


def read_first_bytes(url: str) -> list[dict[str, Any]]:
    requests = import_required("requests")
    headers = {
        "Range": "bytes=0-4095",
        "User-Agent": "Mozilla/5.0",
    }
    with requests.get(url, headers=headers, timeout=25, stream=True) as response:
        response.raise_for_status()
        first = next(response.iter_content(chunk_size=4096), b"")
        return [
            {
                "url": url,
                "status_code": response.status_code,
                "content_type": response.headers.get("Content-Type", ""),
                "content_length": response.headers.get("Content-Length", ""),
                "bytes_read": len(first),
            }
        ]


def probe_eastmoney_research_pdf() -> Any:
    df = probe_ak_research()
    if "报告PDF链接" not in df.columns:
        raise RuntimeError("missing PDF link column")
    links = df["报告PDF链接"].dropna()
    if links.empty:
        return []
    return read_first_bytes(str(links.iloc[0]))


def probe_cninfo_pdf() -> Any:
    announcements = probe_cninfo_announcements()
    if not isinstance(announcements, list):
        return []
    first = next((item for item in announcements if isinstance(item, dict) and item.get("adjunctUrl")), None)
    if not first:
        return []
    url = "https://static.cninfo.com.cn/" + str(first["adjunctUrl"]).lstrip("/")
    return read_first_bytes(url)


def build_probes() -> list[Probe]:
    return [
        Probe("mootdx", "bars", "600000 daily offset=20", probe_mootdx_bars, attempts=3),
        Probe("mootdx", "quotes", "000001,600000 realtime", probe_mootdx_quotes, attempts=2),
        Probe("mootdx", "finance", "600000", probe_mootdx_finance),
        Probe("mootdx", "xdxr", "600000", probe_mootdx_xdxr),
        Probe("tencent-via-akshare", "stock_zh_a_hist_tx", "sz000001 daily", probe_ak_hist_tx, attempts=3),
        Probe("akshare", "stock_zh_a_hist", "000001 daily", probe_ak_hist, attempts=3),
        Probe("akshare", "stock_research_report_em", "000001", probe_ak_research),
        Probe("eastmoney-pdf", "research_pdf_first_bytes", "000001 first report PDF", probe_eastmoney_research_pdf, attempts=2),
        Probe("akshare", "stock_news_em", "600000", probe_ak_stock_news, attempts=2),
        Probe("akshare", "stock_info_global_em", "global", probe_ak_global_news_em),
        Probe("akshare", "stock_info_global_cls", "all", probe_ak_global_news_cls),
        Probe("tushare", "stock_basic", "all listed", probe_tushare_stock_basic),
        Probe("pywencai", "get", "research semantic query", probe_pywencai),
        Probe("cninfo", "hisAnnouncement/query", "000001", probe_cninfo_announcements, attempts=3),
        Probe("cninfo-pdf", "announcement_pdf_first_bytes", "000001 first announcement PDF", probe_cninfo_pdf, attempts=2),
    ]


def package_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for name in ["pandas", "requests", "mootdx", "akshare", "pywencai", "tushare"]:
        try:
            module = importlib.import_module(name)
            versions[name] = str(getattr(module, "__version__", "unknown"))
        except Exception as exc:
            versions[name] = f"missing: {exc}"
    return versions


def write_outputs(records: list[dict[str, Any]], previews: list[dict[str, Any]], metadata: dict[str, Any]) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    csv_path = RESULTS_DIR / f"{stamp}_smoke_summary.csv"
    json_path = RESULTS_DIR / f"{stamp}_smoke_previews.json"
    md_path = RESULTS_DIR / f"{stamp}_smoke.md"

    fieldnames = [
        "source",
        "function",
        "sample",
        "attempt",
        "attempts",
        "called_at",
        "elapsed_ms",
        "rows",
        "columns",
        "status",
        "error",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)

    with json_path.open("w", encoding="utf-8") as fh:
        json.dump({"metadata": metadata, "previews": previews}, fh, ensure_ascii=False, indent=2, default=str)

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault((record["source"], record["function"]), []).append(record)

    lines = [
        "# 数据源接口烟测结果",
        "",
        f"- 测试时间：{metadata['started_at']}",
        f"- Python：{metadata['python']}",
        f"- 平台：{metadata['platform']}",
        f"- 输出 CSV：`{csv_path.name}`",
        f"- 输出预览：`{json_path.name}`",
        "",
        "## 包版本",
        "",
    ]
    for name, version in metadata["package_versions"].items():
        lines.append(f"- `{name}`: `{version}`")

    lines.extend(
        [
            "",
            "## 汇总",
            "",
            "| source | function | attempts | pass | empty | config_required | error | median_ms | rows_last | notes |",
            "|--------|----------|----------|------|-------|-----------------|-------|-----------|-----------|-------|",
        ]
    )
    for (source, function), items in grouped.items():
        statuses = [item["status"] for item in items]
        elapsed = sorted(int(item["elapsed_ms"]) for item in items)
        median = elapsed[len(elapsed) // 2] if elapsed else 0
        rows_last = items[-1]["rows"]
        first_error = next((item["error"] for item in items if item["error"]), "")
        notes = first_error.replace("|", "/")[:100]
        lines.append(
            f"| {source} | {function} | {len(items)} | {statuses.count('pass')} | "
            f"{statuses.count('empty')} | {statuses.count('config_required')} | "
            f"{statuses.count('error')} | {median} | {rows_last} | {notes} |"
        )

    lines.extend(
        [
            "",
            "## 判定说明",
            "",
            "- `pass`：接口返回非空结构化数据。",
            "- `empty`：连接成功但返回空结果。",
            "- `config_required`：缺少 token、cookie 或本地依赖配置。",
            "- `error`：连接、解析或接口调用失败。",
            "",
        ]
    )
    md_path.write_text("\n".join(lines), encoding="utf-8")

    print(str(md_path))
    print(str(csv_path))
    print(str(json_path))


def main() -> int:
    started_at = datetime.now().isoformat(timespec="seconds")
    metadata = {
        "started_at": started_at,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "package_versions": package_versions(),
    }
    all_records: list[dict[str, Any]] = []
    previews: list[dict[str, Any]] = []

    for probe in build_probes():
        print(f"running {probe.source}.{probe.function} ({probe.attempts} attempt(s))")
        records, preview = run_probe(probe)
        all_records.extend(records)
        if preview is not None:
            previews.append(preview)

    write_outputs(all_records, previews, metadata)
    failed = [record for record in all_records if record["status"] == "error"]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
