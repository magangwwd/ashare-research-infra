"""Step 10: replay acceptance for core.py (audit F01/F04/F09).

(a) core.train_quarter() reproduces the cached quarterly predictions (2 quarters checked)
(b) full 2020-2026 replay with core.select()+core.simulate() vs the original V7 backtest
(c) corrected V7 baseline: base / slip_comm_x2 / all_x2 costs, 1% risk version,
    100-seed random baseline (same gate weeks, random stock from the protection pool),
    weekly t on Monday-start weeks.
Writes results/replay_check.json and results/v7_core_trades.csv
"""
import datetime as dt
import json
import os
import sys
import time

import numpy as np
import polars as pl

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import core  # noqa: E402

CACHE, RES = os.path.join(HERE, "cache"), os.path.join(HERE, "results")


def load_pred():
    p = pl.read_parquet(os.path.join(CACHE, "predictions.parquet"), columns=["signal_date", "instrument_id", "p_abs"])
    e = pl.read_parquet(os.path.join(CACHE, "predictions_v67.parquet"),
                        columns=["signal_date", "instrument_id", "p_rel_ens", "p_rel_s1", "p_rel_s2"])
    return p.join(e, on=["signal_date", "instrument_id"])


def main():
    t0 = time.time()
    panel = pl.read_parquet(os.path.join(CACHE, "weekly_panel.parquet"))
    pred = load_pred()
    out = {}
    # (a) model reproducibility
    rep = {}
    for q in (dt.date(2024, 1, 1), dt.date(2026, 10, 1)):
        cutoff = core.quarter_cutoff(panel, panel.filter(pl.col("signal_date") >= q)["signal_date"].min())
        qe = dt.date(q.year + (q.month + 2) // 12, (q.month + 2) % 12 + 1, 1)
        rows = panel.filter((pl.col("signal_date") >= cutoff) & (pl.col("signal_date") < qe))
        models, meta = core.train_quarter(panel, cutoff)
        sc = core.score(models, meta, rows).select("signal_date", "instrument_id", "p_rel_ens", "p_abs")
        cmp = sc.join(pred, on=["signal_date", "instrument_id"], suffix="_cache")
        d_rel = float((cmp["p_rel_ens"] - cmp["p_rel_ens_cache"]).abs().max())
        d_abs = float((cmp["p_abs"] - cmp["p_abs_cache"]).abs().max())
        same = 0; tot = 0
        for (sd,), g in cmp.join(panel.select("signal_date", "instrument_id", "atr_raw"),
                                 on=["signal_date", "instrument_id"]).group_by(["signal_date"]):
            a = core.select(g, "p_rel_ens")["cands"]; b = core.select(g, "p_rel_ens_cache", "p_abs_cache")["cands"]
            same += a[:1] == b[:1]; tot += 1
        rep[str(cutoff)] = dict(rows=cmp.height, max_diff_p_rel_ens=d_rel, max_diff_p_abs=d_abs,
                                same_top1_weeks=f"{same}/{tot}", train_rows=meta["train_rows"])
        print("(a)", cutoff, rep[str(cutoff)], f"[{time.time()-t0:.0f}s]", flush=True)
    out["model_reproducibility"] = rep

    # (b) full replay vs original V7
    bars, days = core.load_bars(os.path.join(CACHE, "daily_sim.parquet"))
    days = days[days >= np.datetime64("2020-01-01")]
    plans, picks = core.build_plans(pred, panel)
    curve, trades = core.simulate(plans, bars, days, core.Rules())
    s = core.summary(curve, trades)
    orig = pl.read_csv(os.path.join(RES, "improve_trades_V7_ensemble.csv"))
    new = pl.DataFrame(trades)
    m = orig.select("id", "entry").join(new.select("id", "entry"), on=["id", "entry"], how="full", coalesce=True)
    both = orig.join(new, on=["id", "entry"], how="inner").height
    out["replay_vs_original"] = dict(original_total=0.58611965, replay_total=s["total_ret"],
                                     original_trades=orig.height, replay_trades=new.height,
                                     same_trades=both)
    print("(b)", out["replay_vs_original"], flush=True)
    new.write_csv(os.path.join(RES, "v7_core_trades.csv"))
    picks.write_csv(os.path.join(RES, "v7_core_weekly_picks.csv"))

    # (c) corrected baseline
    week_pools = {}
    for (sdate,), g in pred.join(panel.select("signal_date", "instrument_id", "entry_date", "atr_raw"),
                                 on=["signal_date", "instrument_id"]).group_by(["signal_date"]):
        ed = np.datetime64(g["entry_date"][0], "D")
        pool = g.filter(2 * pl.col("atr_raw") <= core.POOL_ATR_MAX)
        if ed in plans:
            week_pools[ed] = (pool["instrument_id"].to_list(), pool["atr_raw"].to_list())
    base = {}
    for name, rules in (("budget50_base", core.Rules()),
                        ("budget50_slip_comm_x2", core.Rules(costs=core.COSTS["slip_comm_x2"])),
                        ("budget50_all_x2", core.Rules(costs=core.COSTS["all_x2"])),
                        ("risk1pct_base", core.Rules(risk=0.01))):
        cv, tr = core.simulate(plans, bars, days, rules)
        sm = core.summary(cv, tr)
        rnd_t, rnd_w = [], []
        for sd in range(100):
            rng = np.random.default_rng(sd)
            rp = {}
            for ed, (ids, atrs) in week_pools.items():
                idx = rng.permutation(len(ids))[:3]
                rp[ed] = dict(plans[ed], cands=[(ids[i], atrs[i]) for i in idx])
            c2, _ = core.simulate(rp, bars, days, rules)
            rnd_t.append(c2[-1][1] / core.CAPITAL - 1); rnd_w.append(core.weekly_returns(c2))
        rw = np.array(rnd_w); diff = core.weekly_returns(cv) - rw.mean(axis=0)
        sm.update(random_mean=float(np.mean(rnd_t)), random_p95=float(np.percentile(rnd_t, 95)),
                  beats_random=float(np.mean(np.array(rnd_t) < sm["total_ret"])),
                  t_vs_random_weekly=float(diff.mean() / diff.std(ddof=1) * np.sqrt(len(diff))))
        base[name] = sm
        print("(c)", name, {k: (round(v, 4) if isinstance(v, float) else v) for k, v in sm.items() if k != "yearly"},
              f"[{time.time()-t0:.0f}s]", flush=True)
    out["corrected_baseline"] = base
    json.dump(out, open(os.path.join(RES, "replay_check.json"), "w", encoding="utf-8"), indent=1, default=str)
    print(f"total {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
