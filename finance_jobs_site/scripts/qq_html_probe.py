#!/usr/bin/env python3
"""Read only the public Tencent Docs HTML and identify visible link candidates."""
import datetime, html, json, re
from urllib.parse import urljoin, urlparse, parse_qsl
from bs4 import BeautifulSoup
import requests

SOURCE = "https://docs.qq.com/sheet/DS210WHFxVXdHcWJS?tab=BB08J2"
KEYWORDS = re.compile(r"招聘|校招|财务|会计|审计|税务|岗位|投递|报名|career|recruit|campus|zhaopin|jobs?", re.I)
SENSITIVE = re.compile(r"token|secret|auth|cookie|session|ticket|credential|signature|sign|key", re.I)
MAX_BYTES = 1_800_000

def safe_link(raw, base):
    raw = html.unescape(str(raw or "")).replace("\\/", "/").strip()
    if not raw or raw.startswith(("data:", "javascript:", "mailto:", "#")):
        return None
    u = urljoin(base, raw)
    p = urlparse(u)
    if p.scheme != "https" or not p.hostname or p.username or p.password or len(u) > 450:
        return None
    if any(SENSITIVE.search(k) for k, _ in parse_qsl(p.query, keep_blank_values=True)):
        return None
    return u

def inspect(body, final_url, http_status=200):
    soup = BeautifulSoup(body, "html.parser")
    found = {}
    anchors = soup.select("a[href]")
    for a in anchors:
        u = safe_link(a.get("href"), final_url)
        if u and KEYWORDS.search(u + " " + a.get_text(" ", strip=True)):
            found.setdefault(u, {"url": u, "origin": "html_anchor",
                                 "label": a.get_text(" ", strip=True)[:110]})
    scripts = soup.find_all("script")
    inline_urls = 0
    for s in scripts:
        if s.get("src"):
            continue
        js = s.string or s.get_text() or ""
        for possible in re.findall(r'https?:\\?/\\?/[^\s"\'<>\\]{3,450}', js):
            inline_urls += 1
            u = safe_link(possible, final_url)
            if u and KEYWORDS.search(u):
                found.setdefault(u, {"url": u, "origin": "inline_script",
                                     "label": "Unverified embedded URL"})
    candidates = []
    for item in found.values():
        u = urlparse(item["url"])
        path = u.path.lower()
        if u.hostname in ("docs.qq.com", "www.qq.com", "qzonestyle.gtimg.cn"):
            continue
        if path.endswith((".js", ".css", ".png", ".svg", ".jpg", ".jpeg", ".woff", ".ico", ".map", ".gif")):
            continue
        candidates.append(item)
    text = soup.get_text(" ", strip=True)
    return {
        "checked_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "status": "html_inspected", "source": SOURCE, "http_status": http_status,
        "html_title": soup.title.get_text(" ", strip=True)[:120] if soup.title else None,
        "content_bytes": len(body), "html_anchor_count": len(anchors),
        "script_tag_count": len(scripts), "script_literal_url_count": inline_urls,
        "html_table_row_tags": len(soup.select("table tr")),
        "html_table_cell_tags": len(soup.select("table td")),
        "dom_visible_text_chars": len(text),
        "visible_finance_keywords": len(re.findall("财务|会计|审计|税务", text)),
        "candidate_external_recruitment_links_count": len(candidates),
        "candidate_external_recruitment_links": candidates[:50],
        "verified_sheet_rows": 0,
        "notice": "HTML-only evidence. No login, internal APIs or proof of open vacancies."
    }

def main():
    report = {"status": "fetch_error", "source": SOURCE, "verified_sheet_rows": 0}
    try:
        with requests.get(SOURCE, timeout=(8, 25), stream=True, allow_redirects=True,
                          headers={"User-Agent": "FinanceCampusJobSourceMonitor/1.0 (public announcements only)"}) as r:
            report.update(http_status=r.status_code, content_type=r.headers.get("content-type", ""),
                          final_url=r.url)
            if r.status_code != 200 or urlparse(r.url).hostname != "docs.qq.com":
                report["status"] = "http_or_redirect_failed"
            elif "html" not in report["content_type"].lower():
                report["status"] = "not_html"
            else:
                buf = bytearray()
                for part in r.iter_content(65536):
                    buf.extend(part)
                    if len(buf) > MAX_BYTES:
                        raise ValueError("HTML byte limit exceeded")
                report = inspect(bytes(buf), r.url, r.status_code)
    except Exception as e:
        report["status"] = "fetch_error"
        report["error_type"] = type(e).__name__
    with open("qq_html_probe.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("QQ_HTML_PROBE", json.dumps({k: v for k, v in report.items()
                                       if k != "candidate_external_recruitment_links"}, ensure_ascii=False))
    for item in report.get("candidate_external_recruitment_links", []):
        print("UNVERIFIED_HTML_LINK", item["origin"], item["url"])
    with open("qq_html_probe_summary.md", "w", encoding="utf-8") as f:
        f.write("### Tencent Docs public HTML probe\n\n")
        for key in ("status", "http_status", "content_bytes", "html_title", "html_anchor_count",
                    "script_tag_count", "script_literal_url_count", "html_table_row_tags",
                    "html_table_cell_tags", "dom_visible_text_chars",
                    "candidate_external_recruitment_links_count", "verified_sheet_rows"):
            f.write("- " + key + ": " + str(report.get(key, "unknown")) + "\n")
        f.write("\nAll candidate links are unverified. Source HTML may omit JavaScript-rendered cells.\n")

if __name__ == "__main__":
    main()
