"""cninfo adapter: public announcement list (single-trigger, not batch-poll).

P1 finding: cninfo has dual-layer anti-scraping.
- Hard disconnect (ConnectionAbortedError 10053) at < 5s intervals.
- Soft empty-return (HTTP 200, announcements=[]) at random after first success.

Callers must check is_blocked and not treat soft-empty as "no announcements".
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import requests

_HOME = "https://www.cninfo.com.cn"
_QUERY = f"{_HOME}/new/hisAnnouncement/query"
_STATIC = "https://static.cninfo.com.cn"

_session: requests.Session | None = None


def _bare(ts_code: str) -> str:
    return ts_code.split(".")[0]


def _cninfo_params(ts_code: str) -> tuple[str, str]:
    """Return (column, stock_param) for the query POST body."""
    code = _bare(ts_code)
    if ts_code.endswith(".SZ"):
        return "szse", f"{code},gssz0{code}"
    return "sse", f"{code},gssh0{code}"


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        s = requests.Session()
        s.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9",
        })
        try:
            s.get(_HOME, timeout=15)  # warm up session cookie
        except Exception:
            pass
        _session = s
    return _session


def reset_session() -> None:
    """Force rebuild of the HTTP session on next call."""
    global _session
    if _session is not None:
        _session.close()
        _session = None


def get_announcements(
    ts_code: str,
    page_size: int = 30,
) -> tuple[list[dict[str, Any]], bool]:
    """Fetch announcement list for one stock.

    Returns:
        items      : list of announcement dicts (may be empty)
        is_blocked : True when cninfo returned HTTP 200 but empty list.
                     Caller must not treat this as "stock has no announcements".
    """
    s = _get_session()
    column, stock_param = _cninfo_params(ts_code)
    post_headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Origin": _HOME,
        "Referer": f"{_HOME}/new/commonUrl/pageOfSearch?url=disclosure/list/search",
        "X-Requested-With": "XMLHttpRequest",
    }
    payload = {
        "pageNum": "1",
        "pageSize": str(page_size),
        "column": column,
        "tabName": "fulltext",
        "plate": "",
        "stock": stock_param,
        "searchkey": "",
        "secid": "",
        "category": "",
        "trade": "",
        "seDate": "",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    resp = s.post(_QUERY, data=payload, headers=post_headers, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    raw = data.get("announcements") if isinstance(data, dict) else None
    if raw is None:
        # Unexpected response structure — treat as blocked
        return [], True

    fetched_at = datetime.now().isoformat(timespec="seconds")
    items: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            item = dict(item)
            item["ts_code"] = ts_code
            item["source"] = "cninfo"
            item["fetched_at"] = fetched_at
            items.append(item)

    return items, len(items) == 0


def pdf_url(adjunct_url: str) -> str:
    """Build the full PDF URL from the adjunctUrl field in an announcement dict."""
    return _STATIC + "/" + adjunct_url.lstrip("/")
