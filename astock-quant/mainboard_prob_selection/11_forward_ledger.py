"""Step 11: forward paper ledger from frozen signals (run Saturday, after Friday data).

Reads live/signals/signal_<date>.json (latest revision issued BEFORE that week's Tuesday open
is used; revisions issued later are ignored), rebuilds plans and settles them with
core.simulate on actual bars (live/cache/daily_sim.parquet, refreshed by 09).
Accounts from 100,000: PRIMARY budget 50%; CONTROL budget 50% + planned risk 1%;
CONTROL_R2_gate55 = PRIMARY but buys only if rank-1 p_abs >= 0.55 (step 14);
CONTROL_R3_dual = PRIMARY but also requires protection-pool mean p_abs >= 0.5 (step 14).
Writes live/ledger/{trades,equity}_<account>.csv and live/ledger/summary.json (overwritten
each run; they are derived views - the frozen signals are the source of truth).
"""
import datetime as dt
import glob
import json
import os
import sys

import numpy as np
import polars as pl

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import core  # noqa: E402

LIVE = os.environ.get("V7_LIVE_DIR", os.path.join(HERE, "live"))
TZ = dt.timezone(dt.timedelta(hours=8))


def main():
    sig_files = sorted(glob.glob(os.path.join(LIVE, "signals", "signal_*.json")))
    if not sig_files:
        sys.exit("no frozen signals yet")
    if "--test-ignore-deadline" in sys.argv:
        print("TEST MODE: issue-time deadline ignored")
    weeks = {}
    for f in sig_files:
        p = json.load(open(f, encoding="utf-8")); r = p["record"]
        issued = dt.datetime.fromisoformat(r["issued_at"])
        deadline = dt.datetime.fromisoformat(r["entry_date"] + "T09:25:00").replace(tzinfo=TZ)
        if issued >= deadline and "--test-ignore-deadline" not in sys.argv:
            continue                                    # late revision: not tradable
        prev = weeks.get(r["signal_date"])
        if prev is None or int(r["revision"]) > int(prev["revision"]):
            top = p["top50"][:3]
            ids = pl.read_parquet(os.path.join(core.GOLD, "instruments.parquet"),
                                  columns=["instrument_id", "vendor_symbol"])
            m = dict(zip(ids["vendor_symbol"], ids["instrument_id"]))
            weeks[r["signal_date"]] = dict(revision=r["revision"], invest=r["invest"] in (True, "True"),
                                           gate=float(r["gate_p_abs"]), mkt=float(r["market_p_abs"]),
                                           entry=r["entry_date"], exit=r["exit_date"],
                                           cands=[(m[t["vendor_symbol"]], t["atr_raw"]) for t in top])
    if not weeks:
        sys.exit("no signals issued before their Tuesday 09:25 deadline")
    plans = {np.datetime64(w["entry"], "D"): dict(exit_day=np.datetime64(w["exit"], "D"), cands=w["cands"],
                                                 invest=w["invest"]) for w in weeks.values()}
    bars, days = core.load_bars(os.path.join(LIVE, "cache", "daily_sim.parquet"))
    start = min(plans)
    days = days[days >= start]
    out_dir = os.path.join(LIVE, "ledger"); os.makedirs(out_dir, exist_ok=True)
    summ = {}
    plans55 = {np.datetime64(w["entry"], "D"): dict(exit_day=np.datetime64(w["exit"], "D"), cands=w["cands"],
                                                   invest=w["gate"] >= 0.55) for w in weeks.values()}
    plansR3 = {np.datetime64(w["entry"], "D"): dict(exit_day=np.datetime64(w["exit"], "D"), cands=w["cands"],
                                                   invest=w["invest"] and w["mkt"] >= 0.5) for w in weeks.values()}
    for name, rules, pl_ in (("PRIMARY_budget50", core.Rules(), plans), ("CONTROL_risk1pct", core.Rules(risk=0.01), plans),
                             ("CONTROL_R2_gate55", core.Rules(), plans55), ("CONTROL_R3_dual", core.Rules(), plansR3)):
        curve, trades = core.simulate(pl_, bars, days, rules)
        pl.DataFrame({"date": [str(d) for d, _ in curve], "equity": [e for _, e in curve]}).write_csv(
            os.path.join(out_dir, f"equity_{name}.csv"))
        if trades:
            pl.DataFrame(trades).write_csv(os.path.join(out_dir, f"trades_{name}.csv"))
        r = np.array([t["ret"] for t in trades]) if trades else np.array([])
        summ[name] = dict(equity=curve[-1][1] if curve else core.CAPITAL, trades=len(trades),
                          wins=int((r > 0).sum()), open_position_value_included=True,
                          last_day=str(days[-1]) if len(days) else None)
    summ["weeks_signalled"] = len(weeks); summ["weeks_invest"] = sum(w["invest"] for w in weeks.values())
    core.atomic_write(os.path.join(out_dir, "summary.json"), json.dumps(summ, ensure_ascii=False, indent=1))
    print(json.dumps(summ, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
