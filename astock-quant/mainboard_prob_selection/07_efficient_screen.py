"""Step 7: efficient research loop on the existing quarterly predictions (no retraining).

Stage 1 - cost-hurdle screen (seconds): for holding periods H = 1, 2, 4 weeks
  (Tuesday open -> Friday close of week H) measure, on non-overlapping periods:
  IC, top-1 / top-5 (gated by p_abs >= 0.5) net of round-trip cost, excess over the
  pool, and the "filter" value (pool after removing the bottom 30% / 50% by p_rel).
  Pools: all eligible stocks, and the ChatGPT protection rule pool (2*ATR14 <= 8%).
Stage 2 - account test (10万, one stock, cash allowed, 50% budget, 1% planned risk with
  distance max(2ATR, 2%), sizing "plan" = no intraweek stop) only for variants that
  pass stage 1, plus H=1 as reference; 100-seed random baselines from the full pool
  and from the filtered pool.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time

import numpy as np
import polars as pl

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE, RES = os.path.join(HERE, "cache"), os.path.join(HERE, "results")
spec = importlib.util.spec_from_file_location("pb", os.path.join(HERE, "06_protocol_backtest.py"))
pb = importlib.util.module_from_spec(spec); sys.modules["pb"] = pb; spec.loader.exec_module(pb)
COST = {1: 0.0030, 2: 0.0060}          # round trip: base, fee x2
HS = (1, 2, 4)


def tstat(x):
    x = np.asarray(x, float); x = x[~np.isnan(x)]
    return float(x.mean() / x.std(ddof=1) * np.sqrt(len(x))) if len(x) > 2 else float("nan")


def build_frame():
    panel = pl.read_parquet(os.path.join(CACHE, "weekly_panel.parquet"),
                            columns=["signal_date", "entry_date", "exit_date", "instrument_id",
                                     "buyable", "atr_raw"])
    pred = pl.read_parquet(os.path.join(CACHE, "predictions.parquet"),
                           columns=["signal_date", "instrument_id", "p_rel", "p_abs"])
    df = pred.join(panel, on=["signal_date", "instrument_id"], how="inner")
    sched = df.select("signal_date", "entry_date", "exit_date").unique().sort("signal_date")
    ex = sched["exit_date"].to_list()
    for H in HS:
        sched = sched.with_columns(pl.Series(f"exit_{H}", ex[H - 1:] + [None] * (H - 1)))
    df = df.join(sched.drop("entry_date", "exit_date"), on="signal_date")
    sim = (pl.read_parquet(os.path.join(CACHE, "daily_sim.parquet"),
                           columns=["instrument_id", "trade_date", "open", "close", "adjustment_factor"])
             .with_columns((pl.col("open") * pl.col("adjustment_factor")).alias("o_h"),
                           (pl.col("close") * pl.col("adjustment_factor")).alias("c_h"))
             .sort(["instrument_id", "trade_date"]))
    ent = sim.select("instrument_id", pl.col("trade_date").alias("entry_date"), "o_h")
    df = df.join(ent, on=["instrument_id", "entry_date"], how="left")
    closes = sim.select("instrument_id", "trade_date", "c_h").sort("trade_date")
    for H in HS:
        k = f"exit_{H}"
        tmp = (df.select("instrument_id", k).filter(pl.col(k).is_not_null()).unique()
                 .sort(k).join_asof(closes, left_on=k, right_on="trade_date", by="instrument_id",
                                    strategy="backward")
                 .select("instrument_id", k, pl.col("c_h").alias(f"c_{H}")))
        df = df.join(tmp, on=["instrument_id", k], how="left").with_columns(
            (pl.col(f"c_{H}") / pl.col("o_h") - 1).alias(f"ret_{H}w"))
    return df.filter(pl.col("buyable")), sched


def screen(df, sched, pool_name):
    weeks = sched["signal_date"].to_list()
    out = {}
    for H in HS:
        r = f"ret_{H}w"
        d = df.filter(pl.col(r).is_not_null()).with_columns(
            pl.col("p_rel").rank("ordinal", descending=True).over("signal_date").alias("rk"),
            pl.len().over("signal_date").alias("n"),
            pl.col(r).mean().over("signal_date").alias("pool"))
        wk = (d.group_by("signal_date").agg(
                pl.corr(pl.col("p_rel").rank(), pl.col(r).rank()).alias("ic"),
                pl.col(r).filter(pl.col("rk") == 1).first().alias("top1"),
                pl.col("p_abs").filter(pl.col("rk") == 1).first().alias("gate_p"),
                pl.col(r).filter(pl.col("rk") <= 5).mean().alias("top5"),
                pl.col("pool").first().alias("pool"),
                pl.col(r).filter(pl.col("rk") <= pl.col("n") * 0.7).mean().alias("keep70"),
                pl.col(r).filter(pl.col("rk") <= pl.col("n") * 0.5).mean().alias("keep50"))
              .sort("signal_date"))
        res_ph = []
        for ph in range(H):                       # non-overlapping phases
            sel = set(weeks[ph::H])
            w = wk.filter(pl.col("signal_date").is_in(list(sel)))
            g = w.filter(pl.col("gate_p") >= 0.5)
            res_ph.append(dict(
                periods=w.height, gated=g.height, ic=w["ic"].mean(), ic_t=tstat(w["ic"]),
                top1_net=(g["top1"] - COST[1]).mean(), top1_net_t=tstat(g["top1"] - COST[1]),
                top1_net_fee2=(g["top1"] - COST[2]).mean(),
                top5_net=(g["top5"] - COST[1]).mean(), top5_net_t=tstat(g["top5"] - COST[1]),
                pool_net_gated=(g["pool"] - COST[1]).mean(),
                top1_ex=(g["top1"] - g["pool"]).mean(), top1_ex_t=tstat(g["top1"] - g["pool"]),
                top5_ex=(g["top5"] - g["pool"]).mean(), top5_ex_t=tstat(g["top5"] - g["pool"]),
                filt70_ex=(w["keep70"] - w["pool"]).mean(), filt70_t=tstat(w["keep70"] - w["pool"]),
                filt50_ex=(w["keep50"] - w["pool"]).mean(), filt50_t=tstat(w["keep50"] - w["pool"])))
        avg = {k: float(np.nanmean([p[k] for p in res_ph])) for k in res_ph[0]}
        avg["per_week_top1_net"] = avg["top1_net"] / H
        avg["pass_top1"] = bool(avg["top1_net"] > 0 and avg["top1_ex_t"] >= 2)
        avg["pass_top5"] = bool(avg["top5_net"] > 0 and avg["top5_ex_t"] >= 2)
        out[f"H{H}"] = avg
        print(f"[{pool_name}] H={H}w periods={avg['periods']:.0f} gated={avg['gated']:.0f} "
              f"IC={avg['ic']:.3f}(t{avg['ic_t']:.1f}) top1 net={avg['top1_net']:+.2%}"
              f"(t{avg['top1_net_t']:.1f}, ex t{avg['top1_ex_t']:.1f}) fee2={avg['top1_net_fee2']:+.2%} "
              f"top5 net={avg['top5_net']:+.2%}(ex t{avg['top5_ex_t']:.1f}) "
              f"pool gated net={avg['pool_net_gated']:+.2%} filt70={avg['filt70_ex']:+.2%}(t{avg['filt70_t']:.1f}) "
              f"filt50={avg['filt50_ex']:+.2%}(t{avg['filt50_t']:.1f}) "
              f"PASS1={avg['pass_top1']} PASS5={avg['pass_top5']}")
    return out


def account_weeks(df, sched, H, filter8):
    exit_map = dict(zip(sched["entry_date"].to_list(), sched[f"exit_{H}"].to_list()))
    weeks = {}
    for (ed,), g in df.group_by(["entry_date"]):
        if exit_map.get(ed) is None:
            continue
        if filter8:
            g = g.filter(2 * pl.col("atr_raw") <= 0.08)
        if g.height == 0:
            continue
        gs = g.sort("p_rel", descending=True)
        top = gs.head(3)
        half = gs.head(max(1, gs.height // 2))
        weeks[np.datetime64(ed, "D")] = dict(
            exit_day=np.datetime64(exit_map[ed], "D"),
            ranked={"p_rel": list(zip(top["instrument_id"], top["p_rel"], top["p_abs"], top["atr_raw"]))},
            cand=list(zip(g["instrument_id"], g["atr_raw"])),
            cand_f50=list(zip(half["instrument_id"], half["atr_raw"])),
            gate_ref={"p_abs": gs["p_abs"][0]})
    return weeks


def main():
    t0 = time.time()
    df, sched = build_frame()
    print(f"frame ready {df.height:,} rows [{time.time()-t0:.0f}s]")
    scr = {"all": screen(df, sched, "all"),
           "filter8": screen(df.filter(2 * pl.col("atr_raw") <= 0.08), sched, "filter8")}

    _, bars, days = pb.bt.load()
    days = days[days >= np.datetime64("2020-01-01")]
    acc = {}
    for pool in ("filter8", "all"):
        for H in HS:
            s = scr[pool][f"H{H}"]
            if not (s["pass_top1"] or s["pass_top5"] or H == 1):
                print(f"skip account test {pool} H={H} (failed cost hurdle)")
                continue
            pb.RISK_FILTER = pool == "filter8"
            weeks = account_weeks(df, sched, H, pool == "filter8")
            for sizing in ("plan", "budget"):
                for fm in (1.0, 2.0):
                    cv, tr = pb.simulate(pb.Cfg("m", "p_rel", "p_abs", "close", sizing, 0.5, fm), weeks, bars, days)
                    m = pb.summarize(cv, tr)
                    rnd = {}
                    for nm, key in (("random_all", "cand"), ("random_top50", "cand_f50")):
                        w2 = {k: dict(v, cand=v[key]) for k, v in weeks.items()}
                        rt, rw = [], []
                        for sd in range(100):
                            c2, _ = pb.simulate(pb.Cfg("r", "random", "p_abs", "close", sizing, 0.5, fm, sd),
                                                w2, bars, days)
                            rt.append(c2[-1][1] / pb.CAPITAL - 1); rw.append(pb.weekly(c2))
                        rt, rw = np.array(rt), np.array(rw)
                        rnd[nm] = dict(mean=float(rt.mean()), p05=float(np.percentile(rt, 5)),
                                       p95=float(np.percentile(rt, 95)))
                        if nm == "random_all":
                            diff = pb.weekly(cv) - rw.mean(axis=0)
                            m["t_vs_random"] = tstat(diff)
                            m["beats_random_share"] = float((rt < m["total_ret"]).mean())
                    m["random"] = rnd
                    key = f"{pool}|H{H}|{sizing}|fee{fm:g}"
                    acc[key] = m
                    print(f"{key:28s} ret={m['total_ret']:+7.2%} cagr={m['cagr']:+.2%} mdd={m['max_dd']:.2%} "
                          f"n={m['trades']} win={m['win_rate']:.0%} beats_rnd={m['beats_random_share']:.0%} "
                          f"t={m['t_vs_random']:.2f} | rnd_all={rnd['random_all']['mean']:+.2%} "
                          f"rnd_top50={rnd['random_top50']['mean']:+.2%} [{time.time()-t0:.0f}s]", flush=True)
                    if sizing == "budget" and fm == 1.0:
                        pl.DataFrame(tr).write_csv(os.path.join(RES, f"eff_trades_{pool}_H{H}.csv"))
                        print("   yearly:", {y: f"{v:+.1%}" for y, v in m["yearly"].items()})
    json.dump(dict(screen=scr, accounts=acc), open(os.path.join(RES, "efficient_summary.json"), "w",
                                                   encoding="utf-8"), indent=1, default=float)
    print(f"total {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
