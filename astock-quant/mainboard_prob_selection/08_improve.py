"""Step 8: pre-registered improvement variants inside the protection pool (2*ATR14 <= 8%).

Discovery 2020-01..2023-12 picks ONE variant; confirmation 2024-01..2026-11 is reported
without re-tuning. Variants:
  V0 base      rank p_rel, gate = top pick p_abs >= 0.5
  V1 mktgate   rank p_rel, gate = pool mean p_abs >= 0.5
  V2 combo     rank = mean of p_rel rank and p_abs rank, gate as V0 (on chosen stock)
  V3 absrank   rank p_abs, gate as V0
  V4 nochase   V0 but skip candidates opening > +3% above Monday close
  V5 poolmodel V0 using models retrained on the protection pool only (08a)
  V6 exret     rank by LightGBM-predicted weekly excess return (08b), gate as V0
  V7 ensemble  rank by 3-seed average p_rel (08b), gate as V0
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys

import numpy as np
import polars as pl

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE, RES = os.path.join(HERE, "cache"), os.path.join(HERE, "results")
spec = importlib.util.spec_from_file_location("pb", os.path.join(HERE, "06_protocol_backtest.py"))
pb = importlib.util.module_from_spec(spec); sys.modules["pb"] = pb; spec.loader.exec_module(pb)
pb.RISK_FILTER = True
COST = 0.003
SPLIT = np.datetime64("2024-01-01")


def tstat(x):
    x = np.asarray(x, float)
    return float(x.mean() / x.std(ddof=1) * np.sqrt(len(x))) if len(x) > 2 else float("nan")


def load():
    panel = pl.read_parquet(os.path.join(CACHE, "weekly_panel.parquet"),
                            columns=["signal_date", "entry_date", "exit_date", "instrument_id", "buyable",
                                     "ret_close", "atr_raw", "e_open", "signal_close"])
    p = pl.read_parquet(os.path.join(CACHE, "predictions.parquet"),
                        columns=["signal_date", "instrument_id", "p_rel", "p_abs"])
    df = p.join(panel, on=["signal_date", "instrument_id"], how="inner")
    f8 = os.path.join(CACHE, "predictions_pool8.parquet")
    if os.path.exists(f8):
        p8 = pl.read_parquet(f8, columns=["signal_date", "instrument_id", "p_rel", "p_abs"]).rename(
            {"p_rel": "p_rel8", "p_abs": "p_abs8"})
        df = df.join(p8, on=["signal_date", "instrument_id"], how="left")
    f67 = os.path.join(CACHE, "predictions_v67.parquet")
    if os.path.exists(f67):
        df = df.join(pl.read_parquet(f67, columns=["signal_date", "instrument_id", "pred_ex", "p_rel_ens"]),
                     on=["signal_date", "instrument_id"], how="left")
    df = df.filter(pl.col("buyable") & (2 * pl.col("atr_raw") <= 0.08))
    df = df.with_columns(
        (pl.col("p_rel").rank().over("signal_date") + pl.col("p_abs").rank().over("signal_date")).alias("combo"),
        pl.col("p_abs").mean().over("signal_date").alias("mkt_pabs"),
        (pl.col("e_open") / pl.col("signal_close") - 1).alias("gap"))
    return df


VARIANTS = {
    "V0_base": dict(score="p_rel", gate="p_abs"),
    "V1_mktgate": dict(score="p_rel", gate="mkt_pabs"),
    "V2_combo": dict(score="combo", gate="p_abs"),
    "V3_absrank": dict(score="p_abs", gate="p_abs"),
    "V4_nochase": dict(score="p_rel", gate="p_abs", nochase=True),
    "V5_poolmodel": dict(score="p_rel8", gate="p_abs8"),
    "V6_exret": dict(score="pred_ex", gate="p_abs"),
    "V7_ensemble": dict(score="p_rel_ens", gate="p_abs"),
}


def weeks_for(df, v):
    weeks, stage1 = {}, []
    for (ed,), g in df.group_by(["entry_date"]):
        if v["score"] not in g.columns or g[v["score"]].null_count() == g.height:
            continue
        pool_ret = g["ret_close"].mean()
        c = g
        if v.get("nochase"):
            c = c.filter(pl.col("gap") <= 0.03)
        c = c.sort(v["score"], descending=True)
        if c.height == 0:
            continue
        gv = float(c[v["gate"]][0])
        top = c.head(3)
        weeks[np.datetime64(ed, "D")] = dict(
            exit_day=np.datetime64(g["exit_date"][0], "D"),
            ranked={"v": [(i, s, gv, a) for i, s, a in zip(top["instrument_id"], top[v["score"]], top["atr_raw"])]},
            cand=list(zip(g["instrument_id"], g["atr_raw"])), gate_ref={"g": gv})
        r1 = c["ret_close"][0]
        stage1.append(dict(d=np.datetime64(ed, "D"), on=gv >= 0.5, top=r1, pool=pool_ret))
    return weeks, sorted(stage1, key=lambda x: x["d"])


def seg(curve, a, b):
    d = np.array([x[0] for x in curve]); e = np.array([x[1] for x in curve])
    m = (d >= a) & (d < b)
    i0 = np.argmax(m)
    start = e[i0 - 1] if i0 > 0 else pb.CAPITAL
    return float(e[m][-1] / start - 1)


def main():
    df = load()
    _, bars, days = pb.bt.load()
    days = days[days >= np.datetime64("2020-01-01")]
    out = {}
    for name, v in VARIANTS.items():
        if v["score"] not in df.columns:
            print(f"{name}: predictions missing, skipped"); continue
        weeks, s1 = weeks_for(df, v)
        res = {}
        for part, sel in (("discovery", lambda d: d < SPLIT), ("confirm", lambda d: d >= SPLIT)):
            rows = [x for x in s1 if sel(x["d"])]
            net = np.array([(x["top"] - COST) if x["on"] and x["top"] is not None else 0.0 for x in rows])
            on = [x for x in rows if x["on"] and x["top"] is not None]
            ex = np.array([x["top"] - x["pool"] for x in on])
            res[part] = dict(weeks=len(rows), invested=len(on), mean_week_net=float(net.mean()),
                             net_t=tstat(net), excess_vs_pool=float(ex.mean()), excess_t=tstat(ex),
                             hit=float(np.mean([x["top"] > COST for x in on])))
        acc = {}
        for sizing, fm in (("budget", 1.0), ("budget", 2.0), ("plan", 1.0)):
            cv, tr = pb.simulate(pb.Cfg(name, "v", "g", "close", sizing, 0.5, fm), weeks, bars, days)
            m = pb.summarize(cv, tr)
            m["discovery_ret"] = seg(cv, np.datetime64("2020-01-01"), SPLIT)
            m["confirm_ret"] = seg(cv, SPLIT, np.datetime64("2100-01-01"))
            if sizing == "budget" and fm == 1.0:
                rt, rd, rc = [], [], []
                for sd in range(100):
                    c2, _ = pb.simulate(pb.Cfg("r", "random", "g", "close", sizing, 0.5, fm, sd), weeks, bars, days)
                    rt.append(c2[-1][1] / pb.CAPITAL - 1)
                    rd.append(seg(c2, np.datetime64("2020-01-01"), SPLIT)); rc.append(seg(c2, SPLIT, np.datetime64("2100-01-01")))
                m["random_mean"] = float(np.mean(rt)); m["beats_random"] = float(np.mean(np.array(rt) < m["total_ret"]))
                m["random_discovery"] = float(np.mean(rd)); m["random_confirm"] = float(np.mean(rc))
                m["beats_random_confirm"] = float(np.mean(np.array(rc) < m["confirm_ret"]))
                pl.DataFrame(tr).write_csv(os.path.join(RES, f"improve_trades_{name}.csv"))
            acc[f"{sizing}|fee{fm:g}"] = m
        out[name] = dict(stage1=res, accounts=acc)
        d, c, b = res["discovery"], res["confirm"], acc["budget|fee1"]
        print(f"{name:13s} DISC wk_net={d['mean_week_net']:+.3%} ex={d['excess_vs_pool']:+.2%}(t{d['excess_t']:.1f}) "
              f"inv={d['invested']} | CONF wk_net={c['mean_week_net']:+.3%} ex={c['excess_vs_pool']:+.2%}(t{c['excess_t']:.1f}) "
              f"inv={c['invested']} | ACC total={b['total_ret']:+.1%} cagr={b['cagr']:+.2%} mdd={b['max_dd']:.1%} "
              f"disc={b['discovery_ret']:+.1%} (rnd {b['random_discovery']:+.1%}) conf={b['confirm_ret']:+.1%} "
              f"(rnd {b['random_confirm']:+.1%}, beats {b['beats_random_confirm']:.0%}) "
              f"fee2={acc['budget|fee2']['total_ret']:+.1%} plan={acc['plan|fee1']['total_ret']:+.1%}", flush=True)
    # pre-registered selection: best discovery mean weekly net among variants with discovery excess t >= 2
    ok = {k: v for k, v in out.items() if v["stage1"]["discovery"]["excess_t"] >= 2}
    pool = ok or out
    best = max(pool, key=lambda k: pool[k]["stage1"]["discovery"]["mean_week_net"])
    print(f"\nselected on discovery: {best} (qualified: {list(ok)})")
    b = out[best]
    print(f"confirm period: stage1 excess={b['stage1']['confirm']['excess_vs_pool']:+.2%} "
          f"(t{b['stage1']['confirm']['excess_t']:.1f}); account {b['accounts']['budget|fee1']['confirm_ret']:+.1%} "
          f"vs random {b['accounts']['budget|fee1']['random_confirm']:+.1%}")
    out["_selected"] = best
    json.dump(out, open(os.path.join(RES, "improve_summary.json"), "w", encoding="utf-8"), indent=1, default=float)


if __name__ == "__main__":
    main()
