"""Plan lightweight hourly ETF GitHub Actions ticks; bound expensive research.

Scheduled ticks always run. Full historical research runs at most once per
Beijing trading-weekday after 17:00 and research commit, subject to a verified
success receipt in the GitHub Actions cache. GitHub cron can be delayed/skipped;
this module never promises exactly-on-time execution.

Manual "full" and main-branch workflow definition updates can validate the job
outside market hours. This is fixed-program automation, NOT autonomous coding.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo

MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
RESEARCH_GATE_VERSION = "etf-hourly-research-v1"


def decide(event: str, requested_mode: str, now_utc: datetime) -> dict:
    if now_utc.tzinfo is None:
        raise ValueError("UTC instant must have timezone information")
    local = now_utc.astimezone(MARKET_TIMEZONE)
    if event == "workflow_dispatch":
        eligible = requested_mode == "full"
        reason = "MANUAL_FULL" if eligible else "MANUAL_QUICK"
    elif event == "push":
        eligible = True
        reason = "WORKFLOW_DEFINITION_SMOKE_TEST"
    elif event == "schedule":
        eligible = local.weekday() < 5 and local.hour >= 17
        reason = (
            "CLOSED_A_SHARE_WEEKDAY" if eligible else
            "WEEKEND" if local.weekday() >= 5 else
            "WAIT_FOR_MARKET_CLOSE"
        )
    else:
        eligible = False
        reason = "UNSUPPORTED_EVENT"
    return {
        "eligible_full_research": eligible,
        "reason": reason,
        "event": event,
        "requested_mode": requested_mode,
        "beijing_time": local.isoformat(timespec="seconds"),
        "beijing_date": local.date().isoformat(),
        "hour": local.hour,
        "guard_version": RESEARCH_GATE_VERSION,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", default=os.getenv("GITHUB_EVENT_NAME", "schedule"))
    parser.add_argument("--mode", choices=("full", "quick"), default="full")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--github-output", type=Path, default=None)
    parser.add_argument("--utc-now", default=None, help="Only for reproducible unit tests.")
    args = parser.parse_args()

    now = (
        datetime.fromisoformat(args.utc_now.replace("Z", "+00:00"))
        if args.utc_now else datetime.now(timezone.utc)
    )
    decision = decide(args.event, args.mode, now)
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip()
    if len(head) != 40:
        raise ValueError("Cannot identify checked-out research revision")
    decision["source_commit"] = head
    key_payload = (
        f"{RESEARCH_GATE_VERSION}:{decision['beijing_date']}:{head}"
    ).encode("utf-8")
    decision["research_key"] = hashlib.sha256(key_payload).hexdigest()[:32]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(decision, indent=2), encoding="utf-8")
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as f:
            f.write(
                f"eligible={'true' if decision['eligible_full_research'] else 'false'}\n"
                f"research_key={decision['research_key']}\n"
                f"beijing_date={decision['beijing_date']}\n"
                f"reason={decision['reason']}\n"
            )
    print("ETF_HOURLY_GATE " + json.dumps(decision, ensure_ascii=False))


if __name__ == "__main__":
    main()
