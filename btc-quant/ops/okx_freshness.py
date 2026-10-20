"""Independent OKX archive freshness/Drive integrity watchdog.

Reads GitHub Actions + actual immutable Google Drive snapshot bytes.
Writes/recovers a deduplicated GitHub Issue; never places trade orders.
"""
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request


ISSUE_TITLE = "[BTC OKX] 数据采集停滞或云盘校验失败"
_ISSUES_CREATED_THIS_PROCESS = {}
DEST = "gdrive:BTC_Quant_System_OKX/market_data/OKX/sparse_snapshots"


def parse_time(value):
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def latest_success(runs, branch):
    good = [r for r in runs if r.get("conclusion") == "success"
            and r.get("status") == "completed" and r.get("head_branch") == branch]
    return max(good, key=lambda r: r["created_at"]) if good else None


def verify_snapshot(manifest, payload, *, now, max_age_hours=6):
    if manifest.get("kind") != "SPARSE_REST_NOT_CONTINUOUS_L2":
        raise ValueError("Invalid snapshot kind")
    if not isinstance(manifest.get("rows"), int) or manifest["rows"] < 1:
        raise ValueError("No sampled REST data")
    if manifest.get("errors"):
        raise ValueError("Capture reported API errors")
    if hashlib.sha256(payload).hexdigest() != manifest.get("sha256"):
        raise ValueError("Drive snapshot SHA256 differs from manifest")
    if payload.count(b"\n") != manifest["rows"]:
        raise ValueError("Snapshot row count differs from manifest")
    created = dt.datetime.strptime(manifest["created_utc"], "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)
    age = (now - created).total_seconds() / 3600
    if age < -0.1 or age > max_age_hours:
        raise ValueError(f"Snapshot age outside [0,{max_age_hours}]h: {age:.2f}h")
    return age


def request_json(path, method="GET", payload=None):
    token = os.environ["GH_TOKEN"]
    req = urllib.request.Request(
        "https://api.github.com/repos/" + os.environ["GH_REPO"] + path,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28",
                 "Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=25) as response:
        return json.load(response)


def rclone(*args):
    config = os.environ["RCLONE_CONFIG"]
    result = subprocess.run(
        ["rclone", "--config", config, *args],
        check=True, capture_output=True, timeout=60,
    )
    return result.stdout


def check(now=None, max_age_hours=6):
    now = now or dt.datetime.now(dt.timezone.utc)
    branch = os.environ.get("WATCH_BRANCH", "main")
    data = request_json("/actions/workflows/btc-okx-market-archive.yml/runs?per_page=40")
    run = latest_success(data.get("workflow_runs", []), branch)
    if run is None:
        raise ValueError(f"No successful OKX archive runs on branch {branch}")
    run_age = (now - parse_time(run["created_at"])).total_seconds() / 3600
    if run_age > max_age_hours or run_age < -0.1:
        raise ValueError(f"Latest verified archive run age {run_age:.2f}h exceeds threshold")
    folder = f"{DEST}/run_{run['id']}_{run['run_attempt']}"
    found = json.loads(rclone("lsjson", folder, "--files-only"))
    filenames = {x["Name"] for x in found}
    catalogs = [x for x in filenames if x == "historical_catalog.json"]
    manifests = [x for x in filenames if x.startswith("manifest_") and x.endswith(".json")]
    snapshots = [x for x in filenames if x.startswith("snapshot_") and x.endswith(".jsonl")]
    if len(catalogs) != 1 or len(manifests) != 1 or len(snapshots) != 1:
        raise ValueError(f"Drive snapshot missing expected files: {sorted(filenames)}")
    manifest = json.loads(rclone("cat", folder + "/" + manifests[0]))
    if manifest.get("file") != snapshots[0]:
        raise ValueError("Manifest points to a missing or wrong snapshot")
    payload = rclone("cat", folder + "/" + snapshots[0])
    age = verify_snapshot(manifest, payload, now=now, max_age_hours=max_age_hours)
    return f"run={run['id']} branch={branch} manifest={manifests[0]} rows={manifest['rows']} age={age:.2f}h sha256=OK"


def sync_issue(healthy, details):
    issues = request_json("/issues?state=open&per_page=100")
    existing = next((x for x in issues if x.get("title") == ISSUE_TITLE and "pull_request" not in x), None)
    # API list endpoints may lag immediately after creation. Preserve the
    # exact returned issue ID for subsequent checks in the same process.
    existing = existing or _ISSUES_CREATED_THIS_PROCESS.get(ISSUE_TITLE)
    if healthy:
        if existing is not None:
            closed = request_json(f"/issues/{existing['number']}", "PATCH", {"state": "closed"})
            _ISSUES_CREATED_THIS_PROCESS.pop(ISSUE_TITLE, None)
            return closed
        return None
    if existing is not None:
        return existing  # deduplicate; unchanged issue remains active until healthy
    issue = request_json("/issues", "POST", {
        "title": ISSUE_TITLE,
        "body": "## BTC OKX 数据采集独立告警\n\n" + details[:1500] +
                "\n\n检测包括成功工作流时间、Drive 文件存在性、真实 SHA256 和快照时间戳。"
                "\n此告警不涉及实盘交易。"
    })
    _ISSUES_CREATED_THIS_PROCESS[ISSUE_TITLE] = issue
    return issue


def main():
    try:
        details = check()
    except Exception as exc:
        details = f"{type(exc).__name__}: {str(exc)[:900]}"
        print("ALERT:", details, flush=True)
        sync_issue(False, details)
        raise SystemExit(1)
    print("OKX independent freshness verified:", details, flush=True)
    sync_issue(True, details)


if __name__ == "__main__":
    main()
