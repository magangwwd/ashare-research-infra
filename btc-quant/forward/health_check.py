#!/usr/bin/env python3
"""Independent GitHub Actions V3 forward-paper heartbeat; protects against silent schedule loss.

No auto-retry after missing bars, state drift, ledger conflicts, or trading-rule
failures. Alerts by creating/retaining the assigned forward Issue.
"""
from datetime import datetime,timezone
import json
import os
import sys
import urllib.request

REPO=os.environ.get("GITHUB_REPOSITORY","")
TOKEN=os.environ.get("GITHUB_TOKEN","")
TITLE="[BTC V3 Forward] 日线模拟停止 / 数据缺口告警"

def api(method,path,data=None):
    payload=json.dumps(data,ensure_ascii=False).encode() if data is not None else None
    req=urllib.request.Request("https://api.github.com/repos/"+REPO+path,
        data=payload,method=method,headers={
          "Authorization":"Bearer "+TOKEN,"Accept":"application/vnd.github+json",
          "X-GitHub-Api-Version":"2022-11-28","Content-Type":"application/json",
          "User-Agent":"BTC-V3-forward-independent-heartbeat"})
    with urllib.request.urlopen(req,timeout=25) as response:
        raw=response.read()
        return json.loads(raw) if raw else {}

def check(now=None):
    now=now or datetime.now(timezone.utc)
    result=api("GET","/actions/workflows/btc-v3-forward.yml/runs?per_page=5")
    runs=result.get("workflow_runs",[])
    reason=None
    url=""
    if not runs:
        reason="找不到任何已执行的前向日线模拟工作流"
    else:
        last=runs[0]
        url=last.get("html_url","")
        stamped=last.get("run_started_at") or last.get("created_at")
        dt=datetime.fromisoformat(stamped.replace("Z","+00:00"))
        age=(now-dt).total_seconds()/3600
        if last.get("status")=="completed":
            if last.get("conclusion")!="success":
                reason="最近一次前向工作流未成功："+str(last.get("conclusion"))
            elif age>36:
                reason="超过36小时没有新的前向工作流运行（可能是定时调度停止）"
        elif age>2:
            reason="前向工作流执行超过2小时，疑似卡住"
    if not reason:
        print("Forward heartbeat: recent safe run, no alert")
        return "healthy"
    existing=api("GET","/issues?state=open&per_page=100")
    match=next((x for x in existing if x.get("title")==TITLE and "pull_request" not in x),None)
    body=("**BTC V3 前向模拟需要检查。**\n\n"
          "- 原因："+reason+"\n"
          "- 最近运行："+url+"\n"
          "- 冻结版本：d60c667b3654dbfb00efea9bc72241cca3bb8d5c\n"
          "- 请核查该运行的实际错误和 Drive 最新不可覆盖账本。"
          "未查明原因之前不模拟补造缺失交易，不接真实资金。\n")
    if match:
        print("Forward heartbeat: incident already open #"+str(match["number"]))
    else:
        issue=api("POST","/issues",{"title":TITLE,"body":body,"assignees":[REPO.split("/")[0]]})
        print("Forward heartbeat opened #"+str(issue["number"]))
    return "alerted"

if __name__=="__main__":
    if not REPO or not TOKEN:
        raise RuntimeError("GitHub repo and GITHUB_TOKEN required")
    try:
        check()
    except Exception as e:
        print("::error::Forward independent heartbeat failed: "+str(e),file=sys.stderr)
        raise
