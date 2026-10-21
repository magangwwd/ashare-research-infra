"""Independent BTC research scheduler watchdog, called by existing hourly V3 monitor.

If the NEW research workflow misses ~2 hours and no fallback was dispatched,
record a deduplicated GitHub Issue and request a read-only QUICK run. Never
modify trading/strategy data or V3 watchdog schedules.
"""
import datetime as dt
import json
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen

WORKFLOW = "btc-hourly-research-cycle.yml"
REPO = "magangwwd/ashare-research-infra"
TITLE = "[BTC RESEARCH] hourly scheduler missing or delayed"


def utc_time(raw):
    return dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))


def decide(runs, now, age_limit=120):
    """"Within age_limit minutes" counts only main-branch cron or fallback runs."""
    eligible = sorted(
        (x for x in runs if x.get("head_branch") == "main"
         and x.get("event") in ("schedule", "workflow_dispatch")),
        key=lambda x: x["created_at"], reverse=True,
    )
    latest = eligible[0] if eligible else None
    since = ((now - utc_time(latest["created_at"])).total_seconds() / 60
             if latest else None)
    has_recent = since is not None and since < age_limit
    # Avoid duplicate remediation while a recent PR/main push is already running.
    all_recent = any(
        x.get("head_branch") == "main" and
        (now - utc_time(x["created_at"])).total_seconds() / 60 < age_limit
        for x in runs
    )
    return {
        "status": ("HEALTHY_RECENT_CRON_OR_FALLBACK" if has_recent else
                   "BOOTSTRAP_GRACE_RECENT_MAIN_PUSH" if all_recent else
                   "MISSING_RECENT_CRON_AND_FALLBACK"),
        "should_dispatch": not has_recent and not all_recent,
        "last_cron_or_fallback_id": latest.get("id") if latest else None,
        "age_minutes": round(since, 2) if since is not None else None,
        "scheduled_run_seen": any(x.get("event") == "schedule" for x in eligible),
    }


def api(token, method, path, data=None):
    payload = None if data is None else json.dumps(data).encode("utf-8")
    req = Request(
        "https://api.github.com/repos/" + REPO + path, data=payload, method=method,
        headers={"Accept": "application/vnd.github+json",
                 "Authorization": "Bearer " + token,
                 "User-Agent": "BTC-research-async-watchdog",
                 "Content-Type": "application/json"},
    )
    with urlopen(req, timeout=20) as response:
        body = response.read()
    return json.loads(body) if body else {}


def issue(token, message):
    records = api(token, "GET", "/issues?state=open&per_page=100")
    existing = next((x for x in records if x.get("title") == TITLE
                     and "pull_request" not in x), None)
    if existing is not None:
        return existing["number"]
    created = api(token, "POST", "/issues", {"title":TITLE, "body":message})
    return created["number"]


def main():
    token = os.environ.get("GH_TOKEN")
    if not token or os.environ.get("GITHUB_REPOSITORY") != REPO:
        raise SystemExit("Missing scoped token / unexpected repository")
    now = dt.datetime.now(dt.timezone.utc)
    current = api(token,"GET",f"/actions/workflows/{WORKFLOW}")
    if current.get("state") != "active":
        raise SystemExit("Research workflow not active; no self-dispatch")
    fetched = api(token,"GET",f"/actions/workflows/{WORKFLOW}/runs?per_page=50")
    result = decide(fetched["workflow_runs"],now)
    print(json.dumps({"checked_utc":now.isoformat(),**result},ensure_ascii=False))
    if result["status"] != "MISSING_RECENT_CRON_AND_FALLBACK":
        return
    evidence = (f"At {now.isoformat()} official BTC research cron missing/delayed for >=2h. "
                f"Last schedule/dispatch run: {result['last_cron_or_fallback_id']}, "
                f"age minutes: {result['age_minutes']}. "
                f"Do not infer failure of OKX/V3 independent source workflows. "
                f"Audit: https://github.com/{REPO}/actions/workflows/{WORKFLOW}")
    number = issue(token, evidence)
    print(f"::warning::Research cron stale: issue #{number}; fallback eligible={result['should_dispatch']}")
    if result["should_dispatch"]:
        api(token,"POST",f"/actions/workflows/{WORKFLOW}/dispatches",
            {"ref":"main","inputs":{"mode":"quick"}})
        print("Fallback workflow_dispatch QUICK requested; check subsequent actual run before claiming recovery.")


if __name__ == "__main__":
    main()
