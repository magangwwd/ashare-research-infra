"""BTC hourly research coordinator and auditable experiment receipt.

Checks operational GitHub workflow states on every invocation. For bounded deep
iterations, reads existing frozen-code research outputs but NEVER edits strategy
source, submits orders, closes issues, or auto-promotes an experiment.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
from urllib.request import Request, urlopen

CHECKED_WORKFLOWS = {
    "btc-okx-market-archive.yml": 12,
    "btc-okx-freshness-watchdog.yml": 12,
    "btc-v3-forward.yml": 72,
    "btc-v3-watchdog.yml": 12,
    "btc-regime-router.yml": 48,
}
EXPECTED_REPO = "magangwwd/ashare-research-infra"


def check_latest(raw, now, max_age_hours):
    runs = raw.get("workflow_runs", [])
    if not runs:
        return {"status": "UNKNOWN_NO_RUNS", "reason": "no workflow run returned"}
    r = runs[0]
    created = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
    age = max(0.0, (now - created).total_seconds() / 3600)
    status = r.get("status")
    conclusion = r.get("conclusion")
    if status != "completed":
        result = "IN_PROGRESS" if age <= max_age_hours else "STALE_RUNNING"
    elif conclusion != "success":
        result = "FAILED"
    elif age > max_age_hours:
        result = "STALE"
    else:
        result = "OK"
    return {
        "status": result, "last_run_utc": r["created_at"],
        "age_hours": round(age, 3),
        "conclusion": conclusion, "github_run_url": r.get("html_url"),
        "run_id": r.get("id"),
    }


def query_workflows(repo, token, now, opener=urlopen):
    if repo != EXPECTED_REPO:
        raise ValueError("Unexpected repository; refusing to check another account")
    if not token:
        raise ValueError("Missing GitHub token for authenticated read-only status")
    result = {}
    for filename, maximum in CHECKED_WORKFLOWS.items():
        url = f"https://api.github.com/repos/{repo}/actions/workflows/{filename}/runs?per_page=1"
        request = Request(url, headers={
            "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
            "User-Agent": "BTC-bounded-research-loop",
        })
        with opener(request, timeout=20) as response:
            data = json.load(response)
        result[filename] = check_latest(data, now, maximum)
    return result


def finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError("Non-finite or non-numeric result from strategy")
    return float(value)


def deep_summary(folder):
    root = Path(folder)
    regime = json.loads((root / "regime" / "latest_regime.json").read_text())
    r1 = json.loads((root / "stage1" / "stage1_summary.json").read_text())
    h1 = json.loads((root / "hmg_1x" / "hmg_summary.json").read_text())
    h2 = json.loads((root / "hmg_2x" / "hmg_summary.json").read_text())
    v3 = json.loads((root / "v3" / "summary.json").read_text())
    provenance = json.loads((root / "data" / "data_provenance.json").read_text())
    source = json.loads((root / "source_availability.json").read_text())
    if len({r1["data_end_utc"], regime["data_end"], v3["data_end_utc"],
            provenance["end"], source["last_verified_available_utc_day"]}) != 1:
        raise ValueError("Research artifacts contain inconsistent UTC cutoff")
    historical = {}
    for name in ("r1_1x", "r1_2x", "v3_static_1x", "v3_static_2x", "cash_static_1x"):
        d = r1["results"][name]
        historical[name] = {
            "total_return": finite_number(d["total_return"]),
            "max_drawdown": finite_number(d["max_drawdown"]),
            "trades": int(d["trades"]),
            "folds": int(d["folds"]),
        }
    guarded = {}
    for name in ("v3_guarded_1x_cost", "v3_guarded_2x_cost"):
        d = v3["strategies"][name]
        guarded[name] = {
            "total_return": finite_number(d["total_return"]),
            "max_drawdown": finite_number(d["max_drawdown"]),
            "trades": int(d["trades"]),
        }
    hmg = {}
    for name, data in (("hmg_1x", h1), ("hmg_2x", h2)):
        hmg[name] = {k: finite_number(data[k]) for k in ("total_return", "max_drawdown")}
        hmg[name]["trades"] = int(data["trades"])
    return {
        "observed_utc_end": r1["data_end_utc"],
        "data_sha256": r1["input_sha256"],
        "source_lag_days": int(source["source_lag_days"]),
        "source_health": source["data_health"],
        "regime": regime["latest_state"]["confirmed_regime"],
        "regime_recommendation": regime["latest_recommendation"]["strategy"],
        "historical_rolling_730_90_not_forward": historical,
        "historical_aggregate_v3_not_forward": guarded,
        "historical_hmg_daily_proxy_not_intraday": hmg,
        "risk_target_25pct_pass_in_retrospective_test": all(
            historical[x]["max_drawdown"] <= 0.25 for x in ("r1_1x", "r1_2x")
        ),
        "promotion_decision": "RESEARCH_ONLY_NO_LIVE_OR_AUTOMATIC_PROMOTION",
        "warning": "Historical rolling samples are not new real forward days; market recommendation not authorized for orders.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("quick", "deep"), required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--deep-dir")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    checks = query_workflows(os.environ.get("GITHUB_REPOSITORY", ""),
                             os.environ.get("GH_TOKEN", ""), now)
    summary = {
        "schema": "BTC_RESEARCH_LOOP_V1",
        "created_utc": now.isoformat(),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "commit": os.environ.get("GITHUB_SHA"),
        "mode": args.mode,
        "heartbeat_only_when_quick": args.mode == "quick",
        "checks": checks,
        "issues_never_autoclosed": True,
        "live_trading": False,
        "auto_merge_or_modify_strategies": False,
        "resource_governor": "hourly lightweight; deep hours UTC 01/07/13/19; 45min timeout; no overlapping jobs",
    }
    if args.mode == "deep":
        if not args.deep_dir:
            raise ValueError("--deep-dir is required for deep run")
        summary["study"] = deep_summary(args.deep_dir)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "mode": summary["mode"],
        "checks": {k: v["status"] for k, v in checks.items()},
        "research_observed_through": summary.get("study", {}).get("observed_utc_end", None),
        "research_candidate_status": summary.get("study", {}).get("promotion_decision", None),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
