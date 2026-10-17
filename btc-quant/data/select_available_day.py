#!/usr/bin/env python3
"""Select only actually published original Binance Vision complete UTC 1D archives.

If the most recently closed UTC day is unpublished, mark STALE and run
retrospective research on a VERIFIED older UTC day, never forge fresh signals.
A non-404 source/network failure is fatal (no silent failover).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASE = "https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1d/"


def select_latest_published(closed_day, max_lag=3, opener=urlopen):
    checked = []
    for lag in range(max_lag + 1):
        day = closed_day - timedelta(days=lag)
        filename = "BTCUSDT-1d-" + day.isoformat() + ".zip"
        url = BASE + filename
        try:
            req = Request(url,headers={"User-Agent": "BTC-router-research/1.0"})
            with opener(req, timeout=25) as response:
                signature = response.read(4)
                if signature != b"PK\\x03\\x04":
                    raise ValueError("source is not a ZIP: "+day.isoformat())
            checked.append({"day": day.isoformat(), "http": 200, "published": True})
            return day, checked
        except HTTPError as error:
            checked.append({"day": day.isoformat(), "http": error.code, "published": False})
            if error.code != 404:
                raise
    raise RuntimeError("NO_PUBLISHED_DAILY_ARCHIVE_IN_ALLOWED_LAG_WINDOW: " + json.dumps(checked))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True)
    ap.add_argument("--github-env", required=True)
    ap.add_argument("--max-lag", type=int, default=3)
    args = ap.parse_args()
    if args.max_lag < 0 or args.max_lag > 7:
        raise ValueError("invalid maximum lag")
    target = datetime.now(timezone.utc).date()-timedelta(days=1)
    actual, checked = select_latest_published(target, args.max_lag)
    lag = (target-actual).days
    payload = {
        "source": BASE, "last_complete_utc_day": target.isoformat(),
        "last_verified_available_utc_day": actual.isoformat(),
        "source_lag_days": lag, "data_health": "READY" if lag == 0 else "STALE_SOURCE_ARCHIVE",
        "eligible_for_current_market_signal": False,
        "reason": "Historical daily next-open fill is always retrospective; missing latest archive adds staleness",
        "checked": checked, "retrieved_at_utc": datetime.now(timezone.utc).isoformat()
    }
    Path(args.output).write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    with Path(args.github_env).open("a",encoding="utf8") as stream:
        stream.write("LATEST_CLOSED_UTC="+actual.isoformat()+"\n")
        stream.write("SOURCE_LAG_DAYS="+str(lag)+"\n")
        stream.write("SOURCE_DATA_HEALTH="+payload["data_health"]+"\n")
    print(json.dumps(payload,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
