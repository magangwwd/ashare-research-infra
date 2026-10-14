#!/usr/bin/env python3
"""Hourly BTC V3 GitHub Actions watchdog; research-only, no trade operations.

Uses only the built-in GITHUB_TOKEN.  Conservatively retries download/timeout
failures, never data-quality failures.  Maintains one open GitHub issue as an
incident, assigned to the repository owner.  GitHub notifications depend on
the owner's notification preferences.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

WORKFLOW = "btc-v3-backtest.yml"
ISSUE_TITLE = "[BTC Watchdog] 第三轮回测停止/失败告警"
MAX_ATTEMPTS = 3  # total attempts, including the original run
STALE_MINUTES = 120
UTC = dt.timezone.utc


def parse_utc(value):
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def retry_decision(conclusion, failed_steps, attempt):
    """Return (should_retry, description); conservative by design."""
    names = " | ".join(failed_steps)
    if conclusion == "cancelled":
        return False, "运行被取消：尊重人工停止，不自动重启"
    if "Strict UTC data validation" in names:
        return False, "数据完整性校验失败：禁止重试掩盖缺失K线"
    if "Run synthetic unit tests" in names or "Rebuild V1 V2 V3" in names:
        return False, "程序测试或策略计算失败：需修复代码"
    if "Configure rclone" in names or "Write append-only" in names:
        return False, "Drive 配置或写入失败：需核实权限与目录，避免重复上传"
    retryable = (
        conclusion == "timed_out"
        or "Download official complete-month" in names
    )
    if retryable and attempt < MAX_ATTEMPTS:
        return True, "下载或超时故障：有限次数自动重试"
    if retryable:
        return False, "下载/超时故障已达到最多三轮，转人工排查"
    return False, "未知或非临时故障：不盲目重启"


class GitHub:
    def __init__(self, repo, token):
        if "/" not in repo or not token:
            raise ValueError("GITHUB_REPOSITORY and GITHUB_TOKEN are required")
        self.repo = repo
        self.owner = repo.split("/", 1)[0]
        self.root = f"https://api.github.com/repos/{repo}"
        self.token = token

    def api(self, method, path, payload=None):
        url = self.root + path
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "BTC-research-watchdog",
            **({"Content-Type": "application/json"} if data is not None else {}),
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                body = res.read()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"GitHub API HTTP {exc.code} on {method} {path}") from exc

    def open_incident(self):
        issues = self.api("GET", "/issues?state=open&per_page=100")
        return next((i for i in issues
                     if "pull_request" not in i and i.get("title") == ISSUE_TITLE), None)

    def report(self, detail):
        incident = self.open_incident()
        body = (f"## BTC V3 工作流告警\n\n{detail}\n\n"
                "本告警仅针对历史回测研究，不连接交易所、不自动实盘。\n"
                "请检查数据缺口、日志和运行环境；不要跳过严格数据验证。\n\n"
                "由每小时监控任务生成；GitHub 邮件/推送取决于用户通知设置。")
        if incident:
            if incident.get("body") != body:
                self.api("PATCH", f"/issues/{incident['number']}", {"body": body})
                print(f"Updated incident issue #{incident['number']}")
            else:
                print(f"Existing incident #{incident['number']} unchanged")
        else:
            result = self.api("POST", "/issues", {
                "title": ISSUE_TITLE, "body": body, "assignees": [self.owner],
            })
            print(f"Created incident issue #{result['number']}")

    def resolve(self, run_url):
        incident = self.open_incident()
        if incident:
            self.api("POST", f"/issues/{incident['number']}/comments", {
                "body": f"BTC V3 工作流恢复成功：{run_url}"
            })
            self.api("PATCH", f"/issues/{incident['number']}", {"state": "closed"})
            print(f"Closed recovered incident #{incident['number']}")


def inspect(api, now=None):
    now = now or dt.datetime.now(UTC)
    result = api.api("GET", f"/actions/workflows/{WORKFLOW}/runs?per_page=10")
    runs = result.get("workflow_runs", [])
    if not runs:
        api.report("未找到 BTC V3 历史回测工作流运行记录；请确认主工作流已经手动触发。")
        return "missing_run"
    run = runs[0]  # GitHub returns most recent run first
    run_id = int(run["id"])
    run_url = run.get("html_url", f"https://github.com/{api.repo}/actions/runs/{run_id}")
    attempt = int(run.get("run_attempt", 1))
    status = run.get("status")
    conclusion = run.get("conclusion")
    print(f"Run {run_id}, attempt {attempt}: {status}/{conclusion}")
    if status != "completed":
        since = parse_utc(run.get("run_started_at") or run["created_at"])
        age = (now - since).total_seconds() / 60
        if age >= STALE_MINUTES:
            api.report(f"运行超过 {STALE_MINUTES} 分钟仍未完成（约 {age:.0f} 分钟）。"
                       f"\n\n- [查看运行]({run_url})\n- 状态：{status}\n"
                       "目前只告警，不强制取消正在运行的任务。")
            return "stalled"
        print(f"Still {status}; age {age:.0f} min < {STALE_MINUTES} min")
        return "running"
    if conclusion == "success":
        api.resolve(run_url)
        return "healthy"

    jobs = api.api("GET", f"/actions/runs/{run_id}/jobs?per_page=100").get("jobs", [])
    failed_steps = [
        str(step.get("name", "")) for job in jobs for step in job.get("steps", [])
        if step.get("conclusion") in ("failure", "timed_out")
    ]
    should_retry, reason = retry_decision(conclusion, failed_steps, attempt)
    if should_retry:
        api.api("POST", f"/actions/runs/{run_id}/rerun-failed-jobs")
        print(f"Triggered attempt {attempt + 1}: {reason}")
        return "restarted"

    steps_msg = "、".join(failed_steps) if failed_steps else "无可识别失败步骤"
    api.report(f"**工作流停止，未自动重启。**\n\n- [查看失败运行]({run_url})"
               f"\n- 运行编号：{run_id}\n- 尝试次数：{attempt}/{MAX_ATTEMPTS}"
               f"\n- 结论：{conclusion}\n- 失败步骤：{steps_msg}"
               f"\n- 判定：{reason}")
    return "alerted"


if __name__ == "__main__":
    try:
        gh = GitHub(os.environ.get("GITHUB_REPOSITORY", ""),
                    os.environ.get("GITHUB_TOKEN", ""))
        result = inspect(gh)
        print(f"Watchdog decision: {result}")
    except Exception as err:
        print(f"::error::BTC watchdog failed: {err}", file=sys.stderr)
        sys.exit(1)
