#!/usr/bin/env python3
"""Uncredentialed browser-render test of Tencent Docs visible page only."""
import asyncio, datetime, json, re
from urllib.parse import urlparse, parse_qsl
from playwright.async_api import async_playwright

URL = "https://docs.qq.com/sheet/DS210WHFxVXdHcWJS?tab=BB08J2"
MATCH = re.compile(r"招聘|校招|会计|审计|财务|税务|岗位|career|recruit|campus|zhaopin|job", re.I)
SECRET = re.compile(r"token|secret|auth|cookie|session|ticket|credential|signature|sign|key", re.I)

def approved_url(raw):
    try:
        u = urlparse(str(raw))
        if u.scheme != "https" or not u.hostname or u.username or u.password:
            return None
        if len(raw) > 450 or any(SECRET.search(k) for k, _ in parse_qsl(u.query)):
            return None
        if u.hostname in {"docs.qq.com", "www.qq.com"}:
            return None
        if u.path.lower().endswith((".js",".css",".png",".jpg",".woff",".svg",".map",".gif",".ico")):
            return None
        return raw
    except Exception:
        return None

async def main():
    result = {
        "source": URL,
        "checked_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "status": "not_started", "credential_mode": "anonymous_no_cookies",
        "verified_sheet_rows": 0, "candidate_external_recruitment_links": []
    }
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
            context = await browser.new_context(viewport={"width":1440,"height":900}, locale="zh-CN")
            page = await context.new_page()
            response = await page.goto(URL, wait_until="domcontentloaded", timeout=45000)
            result["response_status"] = response.status if response else None
            await page.wait_for_timeout(13000)
            result["page_title"] = await page.title()
            result["frame_count"] = len(page.frames)
            evidence = []
            links = []
            for frame in page.frames[:15]:
                try:
                    part = await frame.evaluate("""() => ({
                      body_chars: document.body?.innerText?.length || 0,
                      anchors: Array.from(document.querySelectorAll('a[href]')).slice(0,2000).map(a => ({
                        url: a.href, label: (a.innerText || a.title || a.getAttribute('aria-label') || '').trim().slice(0,100)
                      })),
                      grid_cells: document.querySelectorAll('[role=gridcell]').length,
                      table_cells: document.querySelectorAll('table td').length,
                      tables: document.querySelectorAll('table').length,
                      link_data_elements: document.querySelectorAll('[data-href],[data-url]').length,
                      text_has_visible_finance: /财务|会计|审计|税务/.test(document.body?.innerText || '')
                    })""")
                except Exception as e:
                    evidence.append({"frame":frame.url[:140], "access_error":type(e).__name__})
                    continue
                for a in part.pop("anchors", []):
                    u = approved_url(a["url"])
                    if u and MATCH.search(u + " " + a["label"]):
                        links.append({"url":u, "label":a["label"], "frame":frame.url[:120], "verified_job":False})
                part["frame"] = frame.url[:140]
                evidence.append(part)
            dedup = {x["url"]: x for x in links}
            result.update(status="browser_rendered",
                          frames=evidence,
                          candidate_external_recruitment_links=list(dedup.values())[:50],
                          candidate_external_recruitment_links_count=len(dedup))
            await browser.close()
    except Exception as e:
        result["status"] = "browser_error"
        result["error_type"] = type(e).__name__
        result["error_excerpt"] = str(e)[:230]
    with open("qq_browser_probe.json","w",encoding="utf-8") as f:
        json.dump(result,f,ensure_ascii=False,indent=2)
    print("QQ_BROWSER_PROBE", json.dumps({k:v for k,v in result.items() if k != "candidate_external_recruitment_links"},ensure_ascii=False))
    for link in result.get("candidate_external_recruitment_links", []):
        print("BROWSER_UNVERIFIED_LINK",link["url"])
    with open("qq_browser_probe_summary.md","w",encoding="utf-8") as f:
        f.write("### Tencent Docs browser-render test (anonymous)\n\n")
        f.write("- status: " + str(result.get("status")) + "\n")
        f.write("- response_status: " + str(result.get("response_status")) + "\n")
        f.write("- frame_count: " + str(result.get("frame_count")) + "\n")
        f.write("- candidates: " + str(result.get("candidate_external_recruitment_links_count",0)) + "\n")
        f.write("- real sheet rows verified: 0\n")
        for frame in result.get("frames", []):
            f.write("- rendered frame: " + str({k:v for k,v in frame.items() if k != "frame"}) + "\n")
        f.write("\nNo Tencent login, internal API calls, or permission-bypass behavior.\n")

if __name__ == "__main__":
    asyncio.run(main())
