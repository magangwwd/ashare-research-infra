#!/usr/bin/env python3
"""Forward-paper failure/recovery GitHub Issue notifications, no exchange access."""
import json
import os
import sys
import urllib.request
import urllib.error

TITLE="[BTC V3 Forward] 日线模拟停止 / 数据缺口告警"
repo=os.environ.get("GITHUB_REPOSITORY","")
token=os.environ.get("GITHUB_TOKEN","")
run_url="https://github.com/"+repo+"/actions/runs/"+os.environ.get("GITHUB_RUN_ID","unknown")
mode=sys.argv[1] if len(sys.argv)>1 else "failed"

def request(method,path,data=None):
    url="https://api.github.com/repos/"+repo+path
    payload=json.dumps(data,ensure_ascii=False).encode("utf-8") if data is not None else None
    req=urllib.request.Request(url,data=payload,method=method,headers={
      "Authorization":"Bearer "+token,
      "Accept":"application/vnd.github+json",
      "X-GitHub-Api-Version":"2022-11-28",
      "User-Agent":"BTC-V3-frozen-paper-watchdog",
      "Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=30) as resp:
        txt=resp.read()
        return json.loads(txt) if txt else {}

if not repo or not token:
    raise RuntimeError("Cannot send forward failure alert: GitHub identity/token absent")

issues=request("GET","/issues?state=open&per_page=100")
match=next((r for r in issues if r.get("title")==TITLE and "pull_request" not in r),None)
if mode=="failed":
    body=("**BTC V3 前向模拟未能安全完成，本轮没有产生新的可信净值快照。**\n\n"
          "- 失败工作流："+run_url+"\n"
          "- 策略冻结提交：d60c667b3654dbfb00efea9bc72241cca3bb8d5c\n"
          "- 可能原因：K线未发布、历史前缀哈希变化、漏跑或Drive读写异常；查看失败步骤。\n"
          "- **不得自动补造历史、忽略缺口或进行实盘交易。**\n"
          "- 若本次失败由网络短暂故障造成，待故障排除可手动重跑；自动健康监控另行告警。\n")
    if match:
        request("POST","/issues/"+str(match["number"])+"/comments",{"body":body})
        print("Forward alert added to existing issue #"+str(match["number"]))
    else:
        created=request("POST","/issues",{"title":TITLE,"body":body,
                                     "assignees":[repo.split("/")[0]]})
        print("Forward issue #"+str(created["number"])+" opened")
elif mode=="recovered":
    if match:
        request("POST","/issues/"+str(match["number"])+"/comments",
                {"body":"Forward workflow recovered / completed safely: "+run_url})
        request("PATCH","/issues/"+str(match["number"]),{"state":"closed"})
        print("Forward issue closed after recovery")
    else:
        print("No forward issue to close")
else:
    raise ValueError("mode must be failed or recovered")
