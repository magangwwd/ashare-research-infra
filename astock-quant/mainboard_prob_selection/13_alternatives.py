"""Step 13: pre-registered alternatives to the one-stock V7 (corrected core engine).

Same gate (rank-1 by p_rel_ens has p_abs >= 0.5), same total budget 50%, same costs.
  A0 V7            top-1
  A1 top3          3 slots, equal split, candidates = top-5 in rank order
  A2 top5          5 slots, equal split, candidates = top-7
  A3 consensus     top-1 only if it is in the top-10 of every single-seed ranking, else cash
  A4 lowvol        among the top 10% by p_rel_ens, the lowest-ATR stock (one stock)
Pre-registered criterion: highest weekly Sharpe in 2020-2023; "better" only if it also beats
A0 in 2024-2026 on return AND drawdown is not worse by more than 2 points.
"""
import json
import os
import sys

import numpy as np
import polars as pl

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import core  # noqa: E402

CACHE, RES = os.path.join(HERE, "cache"), os.path.join(HERE, "results")
SPLIT = np.datetime64("2024-01-01")


def load():
    panel = pl.read_parquet(os.path.join(CACHE, "weekly_panel.parquet"),
                            columns=["signal_date", "entry_date", "exit_date", "instrument_id", "atr_raw"])
    p = pl.read_parquet(os.path.join(CACHE, "predictions.parquet"), columns=["signal_date", "instrument_id", "p_rel", "p_abs"])
    e = pl.read_parquet(os.path.join(CACHE, "predictions_v67.parquet"),
                        columns=["signal_date", "instrument_id", "p_rel_ens", "p_rel_s1", "p_rel_s2"])
    df = p.join(e, on=["signal_date", "instrument_id"]).join(panel, on=["signal_date", "instrument_id"])
    return df.filter(2 * pl.col("atr_raw") <= core.POOL_ATR_MAX)


def plans_for(df, kind):
    plans, pools = {}, {}
    for (sd,), g in df.group_by(["signal_date"]):
        g = g.sort("p_rel_ens", descending=True)
        ed = np.datetime64(g["entry_date"][0], "D")
        invest = bool(g["p_abs"][0] >= 0.5)
        ids, atr = g["instrument_id"].to_list(), g["atr_raw"].to_list()
        if kind == "A0":
            c = list(zip(ids[:3], atr[:3]))
        elif kind == "A1":
            c = list(zip(ids[:5], atr[:5]))
        elif kind == "A2":
            c = list(zip(ids[:7], atr[:7]))
        elif kind == "A3":
            tops = [set(g.sort(col, descending=True)["instrument_id"].head(10).to_list())
                    for col in ("p_rel", "p_rel_s1", "p_rel_s2")]
            ok = all(ids[0] in t for t in tops)
            invest = invest and ok
            c = list(zip(ids[:3], atr[:3]))
        elif kind == "A4":
            n = max(1, g.height // 10)
            t = g.head(n).sort("atr_raw")
            c = list(zip(t["instrument_id"].to_list()[:3], t["atr_raw"].to_list()[:3]))
        plans[ed] = dict(exit_day=np.datetime64(g["exit_date"][0], "D"), cands=c, invest=invest)
        pools[ed] = (ids, atr)
    return plans, pools


def seg_stats(curve, a=None, b=None):
    d = np.array([x[0] for x in curve]); e = np.array([x[1] for x in curve])
    m = np.ones(len(d), bool)
    if a is not None: m &= d >= a
    if b is not None: m &= d < b
    i0 = int(np.argmax(m)); start = e[i0 - 1] if i0 > 0 else core.CAPITAL
    ee = np.r_[start, e[m]]
    wr = core.weekly_returns([(x, y) for x, y in zip(d[m], e[m])])
    wr = np.r_[ee[1:][0] / start - 1, wr[1:]] if len(wr) else wr
    return dict(ret=float(ee[-1] / start - 1), mdd=float((1 - ee / np.maximum.accumulate(ee)).max()),
                sharpe=float(wr.mean() / wr.std(ddof=1) * np.sqrt(52)) if wr.std() > 0 else 0.0)


def main():
    df = load()
    bars, days = core.load_bars(os.path.join(CACHE, "daily_sim.parquet"))
    days = days[days >= np.datetime64("2020-01-01")]
    spec = {"A0_V7_top1": ("A0", 1), "A1_top3": ("A1", 3), "A2_top5": ("A2", 5),
            "A3_consensus": ("A3", 1), "A4_lowvol_top10pct": ("A4", 1)}
    out = {}
    for name, (kind, slots) in spec.items():
        plans, pools = plans_for(df, kind)
        res = {}
        for cname in ("base", "all_x2"):
            rules = core.Rules(slots=slots, costs=core.COSTS[cname])
            cv, tr = core.simulate(plans, bars, days, rules)
            sm = core.summary(cv, tr)
            sm["disc"] = seg_stats(cv, None, SPLIT); sm["later"] = seg_stats(cv, SPLIT, None); sm["full"] = seg_stats(cv)
            if cname == "base":
                rt = []
                for sd in range(100):
                    rng = np.random.default_rng(sd)
                    rp = {ed: dict(plans[ed], cands=[(ids[i], at[i]) for i in rng.permutation(len(ids))[:slots + 2]])
                          for ed, (ids, at) in pools.items()}
                    c2, _ = core.simulate(rp, bars, days, rules)
                    rt.append(c2[-1][1] / core.CAPITAL - 1)
                sm["random_mean"] = float(np.mean(rt)); sm["beats_random"] = float(np.mean(np.array(rt) < sm["total_ret"]))
                pl.DataFrame(tr).write_csv(os.path.join(RES, f"alt_trades_{name}.csv"))
            res[cname] = sm
        out[name] = res
        b, x = res["base"], res["all_x2"]
        print(f"{name:20s} full={b['total_ret']:+6.1%} cagr={b['cagr']:+.1%} mdd={b['max_dd']:.1%} "
              f"sharpe={b['full']['sharpe']:.2f} trades={b['trades']} win={b['win_rate']:.1%} worst_acct={b['worst_acct_loss']:+.1%} "
              f"| DISC ret={b['disc']['ret']:+.1%} sh={b['disc']['sharpe']:.2f} | LATER ret={b['later']['ret']:+.1%} "
              f"mdd={b['later']['mdd']:.1%} sh={b['later']['sharpe']:.2f} | rnd={b['random_mean']:+.1%} beats={b['beats_random']:.0%} "
              f"| all_x2={x['total_ret']:+.1%}", flush=True)
    best = max(out, key=lambda k: out[k]["base"]["disc"]["sharpe"])
    a0 = out["A0_V7_top1"]["base"]; bb = out[best]["base"]
    better = best != "A0_V7_top1" and bb["later"]["ret"] > a0["later"]["ret"] and bb["later"]["mdd"] <= a0["later"]["mdd"] + 0.02
    print(f"\nselected on 2020-2023 Sharpe: {best}; better than V7 in 2024-2026: {better}")
    out["_selection"] = dict(best_on_discovery=best, better_than_V7_later=better)
    json.dump(out, open(os.path.join(RES, "alternatives_summary.json"), "w", encoding="utf-8"), indent=1, default=float)


if __name__ == "__main__":
    main()
