"""Fail-closed research-cycle gate using immutable last verified Drive receipt.

A deep research iteration runs ONLY if a newly published official closed UTC
day or relevant research code changed, or previous deep run did not complete.
Checkpoints are data/receipt-only. No strategy self-modification or live orders.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

CODE_FILES = (
    "btc-quant/regime/router.py",
    "btc-quant/regime/hmg.py",
    "btc-quant/regime/r1_experiment.py",
    "btc-quant/backtest/run.py",
    "btc-quant/data/extend_daily.py",
    "btc-quant/config/regime_router_20261016.json",
)
QUEUED_RESEARCH = (
    "PR85_REVIEW_AND_SOURCE_BOUNDARY",
    "WALK_FORWARD_LEAKAGE_AND_FOLD_TESTS",
    "HMG_DOWN_TREND_BUY_VETO",
    "OKX_L2_COVERAGE_AND_GAPS",
)


def code_digest(repo_root):
    root = Path(repo_root)
    sha = hashlib.sha256()
    for name in CODE_FILES:
        raw = (root / name).read_bytes()
        sha.update(name.encode("utf-8") + b"\0" + raw + b"\0")
    return sha.hexdigest()


def plan(requested_mode, availability, last_deep, digest, now=None):
    if requested_mode not in ("deep", "quick"):
        raise ValueError("invalid requested mode")
    end = availability["last_verified_available_utc_day"]
    if not isinstance(end, str) or len(end) != 10:
        raise ValueError("missing data as-of date")
    if availability["data_health"] not in ("READY", "STALE_SOURCE_ARCHIVE"):
        raise ValueError("unknown source publication health")
    stamp = end + ":" + digest
    same = bool(last_deep and
                last_deep.get("mode") == "deep" and
                isinstance(last_deep.get("study"), dict) and
                last_deep.get("research_fingerprint") == stamp)
    action = ("QUICK_CHECK" if requested_mode == "quick" else
              "SKIP_DUPLICATE_DEEP" if same else "RUN_NEW_DEEP_EXPERIMENT")
    # Fail-closed if latest officially published source is stale:
    # such historical work is permitted but cannot be a 'current signal'.
    now = now or datetime.now(timezone.utc)
    return {
        "schema": "BTC_RESEARCH_GATE_V1",
        "created_utc": now.isoformat(),
        "requested_mode": requested_mode,
        "action": action,
        "effective_mode": "deep" if action == "RUN_NEW_DEEP_EXPERIMENT" else "quick",
        "last_verified_available_utc_day": end,
        "source_health": availability["data_health"],
        "research_code_sha256": digest,
        "research_fingerprint": stamp,
        "previous_successful_deep_run_id": last_deep.get("run_id") if last_deep else None,
        "no_repeated_historical_backtest_without_change": same,
        "unresolved_research_backlog": list(QUEUED_RESEARCH),
        "research_backlog_is_not_automatically_implemented": True,
        "no_live_trade_or_strategy_autopromotion": True,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--requested-mode", choices=("quick", "deep"), required=True)
    p.add_argument("--availability", required=True)
    p.add_argument("--previous")
    p.add_argument("--root", default=".")
    p.add_argument("--out", required=True)
    p.add_argument("--github-env", required=True)
    args = p.parse_args()
    availability = json.loads(Path(args.availability).read_text(encoding="utf-8"))
    prior = (json.loads(Path(args.previous).read_text(encoding="utf-8"))
             if args.previous and Path(args.previous).is_file() else None)
    decision = plan(args.requested_mode, availability, prior, code_digest(args.root))
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(decision, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with Path(args.github_env).open("a", encoding="utf-8") as f:
        f.write("MODE=" + decision["effective_mode"] + "\n")
        f.write("RESEARCH_FINGERPRINT=" + decision["research_fingerprint"] + "\n")
        f.write("CYCLE_DECISION=" + decision["action"] + "\n")
    print(json.dumps({k: decision[k] for k in (
        "action", "last_verified_available_utc_day", "source_health",
        "no_repeated_historical_backtest_without_change",
        "previous_successful_deep_run_id")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
