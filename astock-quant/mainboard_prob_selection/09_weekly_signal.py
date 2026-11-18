"""Step 9 (rewritten on core.py): weekly forward signal for frozen version V7.

  python -I -W ignore 09_weekly_signal.py                 # normal run (Tue 00:00 Asia/Shanghai)
  python -I -W ignore 09_weekly_signal.py --replay 2026-11-02   # historical replay, no log
  python -I -W ignore 09_weekly_signal.py --revise "reason"     # re-issue a frozen week (new version)

Guarantees (audit F01/F06/F07/F09):
  * quarterly frozen models in live/models/<YYYYQn>/ (trained once, hash-checked on load);
  * the expected signal week is derived from the trading calendar; stale data or a run
    after Tuesday 09:25 for that week is rejected;
  * a frozen signal is never overwritten; --revise writes signal_<date>_revN.json and logs it;
  * the log is append-only (one line per issued signal), files are written atomically.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import sys

import numpy as np
import polars as pl

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import core  # noqa: E402

LIVE = os.environ.get("V7_LIVE_DIR", os.path.join(HERE, "live"))
VERSION = "V7_ensemble_quarterly_20261107"
TZ = dt.timezone(dt.timedelta(hours=8))
LOG_FIELDS = ["issued_at", "version", "revision", "revise_reason", "signal_date", "entry_date", "exit_date",
              "invest", "gate_p_abs", "market_p_abs", "rank1", "rank2", "rank3", "pool_size",
              "data_max_date", "gold_as_of", "model_quarter", "model_cutoff", "signal_file",
              "gate_p_up_cal", "gate_p_netup_cal", "dual_gate_R3"]


def expected_week(now: dt.datetime):
    days = core.trading_days()
    today = now.date()
    for i, d in enumerate(days):
        if d >= today and d.weekday() == 1:          # next trading Tuesday (incl. today)
            if d == today and now.time() >= dt.time(9, 25):
                continue
            return days[i - 1], d
    raise RuntimeError("no upcoming trading Tuesday in calendar")


def mod(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, file))
    m = importlib.util.module_from_spec(spec); sys.modules[name] = m; spec.loader.exec_module(m)
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", help="signal date YYYY-MM-DD (historical, not logged)")
    ap.add_argument("--revise", help="reason; re-issue an already frozen week")
    ap.add_argument("--skip-build", action="store_true")
    ap.add_argument("--now", help="override clock, ISO datetime (testing)")
    a = ap.parse_args()
    cache = os.path.join(LIVE, "cache"); os.makedirs(cache, exist_ok=True)
    b = mod("build", "01_build_panel.py"); b.CACHE = cache
    if not a.skip_build:
        b.main()
    panel = pl.read_parquet(os.path.join(cache, "weekly_panel.parquet"))
    data_max = pl.read_parquet(os.path.join(cache, "daily_sim.parquet"), columns=["trade_date"])["trade_date"].max()
    gold_as_of = json.load(open(os.path.join(core.GOLD, "manifest.json"), encoding="utf-8")).get("as_of_date")

    if a.replay:
        sig = dt.date.fromisoformat(a.replay); out_dir = os.path.join(LIVE, "replay")
    else:
        now = dt.datetime.fromisoformat(a.now).replace(tzinfo=TZ) if a.now else dt.datetime.now(TZ)
        sig, entry = expected_week(now)
        out_dir = os.path.join(LIVE, "signals")
        if data_max < sig:
            sys.exit(f"REJECT: data only to {data_max}, need signal date {sig}. No plan issued (stay in cash).")
        if panel["signal_date"].max() != sig:
            sys.exit(f"REJECT: newest panel week {panel['signal_date'].max()} != expected {sig}.")
    os.makedirs(out_dir, exist_ok=True)
    rows = panel.filter(pl.col("signal_date") == sig)
    if rows.height == 0:
        sys.exit(f"REJECT: no rows for signal date {sig}")

    # frozen-signal check (before any write)
    base = os.path.join(out_dir, f"signal_{sig}")
    revision = 0
    if os.path.exists(base + ".json") and not a.replay:
        if not a.revise:
            print(open(base + ".json", encoding="utf-8").read())
            sys.exit(f"week {sig} already frozen; not overwritten (use --revise REASON to issue a revision)")
        while os.path.exists(f"{base}_rev{revision + 1}.json"):
            revision += 1
        revision += 1

    # quarterly frozen model
    cutoff = core.quarter_cutoff(panel, sig)
    qkey = f"{sig.year}Q{(sig.month - 1) // 3 + 1}"
    mdir = os.path.join(LIVE, "models", qkey)
    if os.path.exists(os.path.join(mdir, "meta.json")):
        models, meta = core.load_quarter(mdir)
        if meta["cutoff"] != str(cutoff):
            sys.exit(f"REJECT: stored model cutoff {meta['cutoff']} != expected {cutoff}")
    else:
        models, meta = core.train_quarter(panel, cutoff, save_dir=mdir)
        meta = json.load(open(os.path.join(mdir, "meta.json"), encoding="utf-8"))

    sc = core.score(models, meta, rows)
    cal = core.calibration(dt.date.fromisoformat(meta["cutoff"]), save_dir=mdir)   # display only
    sc = sc.with_columns(pl.Series("p_up_cal", core.apply_calibration(cal, sc["p_abs"].to_numpy(), "up")),
                         pl.Series("p_netup_cal", core.apply_calibration(cal, sc["p_abs"].to_numpy(), "net_up")))
    s = core.select(sc)
    names = pl.read_parquet(os.path.join(core.GOLD, "instruments.parquet"),
                            columns=["instrument_id", "vendor_symbol", "name_zh"])
    ranked = s["ranked"].head(50).join(names, on="instrument_id", how="left").with_columns(
        pl.int_range(1, pl.len() + 1).alias("rank"),
        pl.max_horizontal(2 * pl.col("atr_raw"), pl.lit(0.02)).alias("plan_stop_dist"))
    sym = ranked["vendor_symbol"].to_list()
    rec = dict(issued_at=dt.datetime.now(TZ).isoformat(timespec="seconds"), version=VERSION,
               revision=revision, revise_reason=a.revise or "", signal_date=str(sig),
               entry_date=str(rows["entry_date"][0]), exit_date=str(rows["exit_date"][0]),
               invest=s["invest"], gate_p_abs=round(s["gate_value"], 6),
               market_p_abs=round(float(sc.filter(2 * pl.col("atr_raw") <= core.POOL_ATR_MAX)["p_abs"].mean()), 6),
               rank1=sym[0] if sym else "", rank2=sym[1] if len(sym) > 1 else "", rank3=sym[2] if len(sym) > 2 else "",
               pool_size=s["pool_size"], data_max_date=str(data_max), gold_as_of=gold_as_of,
               model_quarter=qkey, model_cutoff=meta["cutoff"],
               gate_p_up_cal=round(float(ranked["p_up_cal"][0]), 4) if sym else "",
               gate_p_netup_cal=round(float(ranked["p_netup_cal"][0]), 4) if sym else "")
    rec["dual_gate_R3"] = bool(s["invest"] and rec["market_p_abs"] >= 0.5)
    payload = dict(record=rec, model_meta=dict(cutoff=meta["cutoff"], sha256=meta["sha256"],
                                               train_rows=meta["train_rows"], lightgbm=meta["lightgbm"]),
                   rules=dict(pool="2*ATR14<=8%", rank="p_rel_ens (3 seeds)", gate="rank1 p_abs>=0.5 (p_up_cal/p_netup_cal are display only)",
                              buy="Tuesday open; skip limit-up/suspended; fall back rank2, rank3",
                              sell="last trading day of week at close; if at down limit carry to next session",
                              size="primary: cost<=50% equity; control: also planned risk<=1% (dist=max(2ATR,2%))"),
                   top50=ranked.select("rank", "vendor_symbol", "name_zh", "p_rel_ens", "p_rel", "p_rel_s1",
                                       "p_rel_s2", "p_abs", "p_up_cal", "p_netup_cal", "signal_close", "atr_raw",
                                       "plan_stop_dist").to_dicts())
    fname = base + (f"_rev{revision}" if revision else "") + ".json"
    rec["signal_file"] = os.path.relpath(fname, LIVE)
    core.atomic_write(fname, json.dumps(payload, ensure_ascii=False, indent=1, default=str))
    if not a.replay:
        log = os.path.join(LIVE, "forward_log.csv")
        new = not os.path.exists(log)
        with open(log, "a", encoding="utf-8", newline="") as f:
            if new:
                f.write(",".join(LOG_FIELDS) + "\n")
            f.write(",".join(str(rec.get(k, "")).replace(",", ";") for k in LOG_FIELDS) + "\n")
    print(json.dumps(rec, ensure_ascii=False, indent=1))
    print(ranked.select("rank", "vendor_symbol", "name_zh", "p_rel_ens", "p_abs", "p_up_cal", "p_netup_cal").head(5))


if __name__ == "__main__":
    main()
