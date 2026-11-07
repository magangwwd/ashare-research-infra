"""Step 14: V7 strategy review - cash weeks, market environment, loss statistics, and
pre-registered refinements (registered before this analysis was run):
  R1 trend   : also require pool-median 20-day return (mkt_ret_20) > 0
  R2 gate55  : rank-1 p_abs >= 0.55 instead of 0.50
  R3 dual    : rank-1 p_abs >= 0.5 AND protection-pool mean p_abs >= 0.5
Selection: best 2020-2023 weekly Sharpe; "better" only if 2024-2026 return is higher and
drawdown not worse by > 2 points (same rule as step 13).
"""
import json
import os
import sys

import numpy as np
import polars as pl

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import core  # noqa: E402
alt = __import__("importlib").import_module  # noqa
import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location("alts", os.path.join(HERE, "13_alternatives.py"))
alts = importlib.util.module_from_spec(spec); sys.modules["alts"] = alts; spec.loader.exec_module(alts)
CACHE, RES = os.path.join(HERE, "cache"), os.path.join(HERE, "results")
SPLIT = np.datetime64("2024-01-01")


def main():
    panel = pl.read_parquet(os.path.join(CACHE, "weekly_panel.parquet"),
                            columns=["signal_date", "entry_date", "exit_date", "instrument_id", "atr_raw", "buyable",
                                     "ret_close", "mkt_ret_20", "mkt_breadth_20", "mkt_vol_20", "mkt_ret_5"])
    p = pl.read_parquet(os.path.join(CACHE, "predictions.parquet"), columns=["signal_date", "instrument_id", "p_abs"])
    e = pl.read_parquet(os.path.join(CACHE, "predictions_v67.parquet"), columns=["signal_date", "instrument_id", "p_rel_ens"])
    pred = p.join(e, on=["signal_date", "instrument_id"])
    bars, days = core.load_bars(os.path.join(CACHE, "daily_sim.parquet"))
    days = days[days >= np.datetime64("2020-01-01")]
    plans, picks = core.build_plans(pred, panel)

    # weekly context: market state at signal date, pool outcome that week
    pool = panel.filter(2 * pl.col("atr_raw") <= core.POOL_ATR_MAX)
    wk = (pool.join(pred, on=["signal_date", "instrument_id"])
              .group_by("signal_date").agg(
                  pl.col("entry_date").first(), pl.col("mkt_ret_20").first(), pl.col("mkt_breadth_20").first(),
                  pl.col("mkt_vol_20").first(), pl.col("mkt_ret_5").first(), pl.col("p_abs").mean().alias("pool_p_abs"),
                  pl.col("ret_close").filter(pl.col("buyable")).mean().alias("pool_ret"))
              .join(picks, on="signal_date").sort("signal_date"))
    out = {}
    cash = wk.filter(~pl.col("invest")); inv = wk.filter(pl.col("invest"))
    out["cash_vs_invest_weeks"] = dict(
        weeks=wk.height, invest_weeks=inv.height, cash_weeks=cash.height,
        pool_ret_in_invest_weeks=inv["pool_ret"].mean(), pool_ret_in_cash_weeks=cash["pool_ret"].mean(),
        pool_down_share_in_cash_weeks=float((cash["pool_ret"] < 0).mean()),
        pool_down_share_in_invest_weeks=float((inv["pool_ret"] < 0).mean()))

    curve, trades = core.simulate(plans, bars, days, core.Rules())
    names = pl.read_parquet(os.path.join(core.GOLD, "instruments.parquet"), columns=["instrument_id", "vendor_symbol", "name_zh"])
    tr = (pl.DataFrame(trades).with_columns(pl.col("entry").str.to_date().alias("entry_date"))
            .join(wk.select("entry_date", "mkt_ret_20", "mkt_breadth_20", "mkt_vol_20", "pool_ret"), on="entry_date", how="left")
            .join(names, left_on="id", right_on="instrument_id", how="left"))
    loss = tr.filter(pl.col("ret") <= 0)
    bins = [(-1, -0.10), (-0.10, -0.05), (-0.05, -0.02), (-0.02, 0.0)]
    out["losses"] = dict(
        losing_trades=loss.height, total_trades=tr.height, total_loss_cny=float(loss["pnl"].sum()),
        total_gain_cny=float(tr.filter(pl.col("ret") > 0)["pnl"].sum()),
        buckets={f"{a:+.0%}~{b:+.0%}": int(loss.filter((pl.col("ret") > a) & (pl.col("ret") <= b)).height) for a, b in bins},
        with_market_down=int(loss.filter(pl.col("pool_ret") < 0).height),
        with_market_up=int(loss.filter(pl.col("pool_ret") >= 0).height),
        worst10=loss.sort("ret").head(10).select("vendor_symbol", "name_zh", "entry", "exit", "ret", "acct_ret",
                                                "pnl", "pool_ret", "mkt_ret_20").to_dicts(),
        by_year={int(y): dict(losses=int(g.filter(pl.col("ret") <= 0).height), trades=g.height,
                              loss_cny=float(g.filter(pl.col("ret") <= 0)["pnl"].sum()), net_cny=float(g["pnl"].sum()))
                 for (y,), g in tr.group_by(pl.col("entry_date").dt.year())})
    # drawdown episodes
    d = np.array([x[0] for x in curve]); eq = np.array([x[1] for x in curve]); peak = np.maximum.accumulate(eq)
    dd = 1 - eq / peak; eps = []; i = 0
    while i < len(dd):
        if dd[i] > 0:
            j = i
            while j < len(dd) and dd[j] > 0: j += 1
            k = i + int(np.argmax(dd[i:j]))
            eps.append(dict(start=str(d[i - 1] if i else d[i]), trough=str(d[k]), depth=float(dd[k]),
                            recovered=str(d[j]) if j < len(dd) else "not yet", days=int((d[min(j, len(d) - 1)] - d[i - 1 if i else i]).astype(int))))
            i = j
        else:
            i += 1
    out["drawdowns_top5"] = sorted(eps, key=lambda x: -x["depth"])[:5]
    # market environment buckets
    def bucket(col, edges, labels):
        r = {}
        for (lo, hi), lab in zip(edges, labels):
            g = tr.filter((pl.col(col) > lo) & (pl.col(col) <= hi))
            if g.height:
                r[lab] = dict(trades=g.height, win=float((g["ret"] > 0).mean()), avg=float(g["ret"].mean()),
                              pnl=float(g["pnl"].sum()))
        return r
    q = tr["mkt_vol_20"].quantile(0.33), tr["mkt_vol_20"].quantile(0.67)
    out["by_market"] = dict(
        trend20=bucket("mkt_ret_20", [(-9, -0.05), (-0.05, 0), (0, 0.05), (0.05, 9)], ["<-5%", "-5~0%", "0~5%", ">5%"]),
        breadth20=bucket("mkt_breadth_20", [(0, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 1)], ["<30%", "30-50%", "50-70%", ">70%"]),
        vol20=bucket("mkt_vol_20", [(0, q[0]), (q[0], q[1]), (q[1], 9)], ["low", "mid", "high"]))

    # pre-registered refinements
    ctx = {np.datetime64(r["entry_date"], "D"): r for r in wk.to_dicts()}
    rules = {"V7": lambda r: r["invest"],
             "R1_trend": lambda r: r["invest"] and r["mkt_ret_20"] > 0,
             "R2_gate55": lambda r: r["gate_value"] >= 0.55,
             "R3_dual": lambda r: r["invest"] and r["pool_p_abs"] >= 0.5}
    ref = {}
    for nm, f in rules.items():
        pp = {ed: dict(v, invest=bool(f(ctx[ed]))) for ed, v in plans.items() if ed in ctx}
        cv, t2 = core.simulate(pp, bars, days, core.Rules())
        sm = core.summary(cv, t2)
        sm["disc"] = alts.seg_stats(cv, None, SPLIT); sm["later"] = alts.seg_stats(cv, SPLIT, None)
        cvx, _ = core.simulate(pp, bars, days, core.Rules(costs=core.COSTS["all_x2"]))
        sm["all_x2"] = cvx[-1][1] / core.CAPITAL - 1
        ref[nm] = sm
        print(f"{nm:10s} ret={sm['total_ret']:+.1%} mdd={sm['max_dd']:.1%} trades={sm['trades']} win={sm['win_rate']:.1%} "
              f"worst={sm['worst_acct_loss']:+.1%} | DISC {sm['disc']['ret']:+.1%} sh={sm['disc']['sharpe']:.2f} | "
              f"LATER {sm['later']['ret']:+.1%} mdd={sm['later']['mdd']:.1%} | all_x2={sm['all_x2']:+.1%}")
    best = max(ref, key=lambda k: ref[k]["disc"]["sharpe"])
    better = best != "V7" and ref[best]["later"]["ret"] > ref["V7"]["later"]["ret"] and \
        ref[best]["later"]["mdd"] <= ref["V7"]["later"]["mdd"] + 0.02
    out["refinements"] = ref; out["refinement_selection"] = dict(best_on_discovery=best, better_later=better)
    print("selected:", best, "better in 2024-26:", better)
    json.dump(out, open(os.path.join(RES, "loss_market_review.json"), "w", encoding="utf-8"), indent=1, default=str,
              ensure_ascii=False)
    print(json.dumps({k: out[k] for k in ("cash_vs_invest_weeks", "by_market", "drawdowns_top5")}, ensure_ascii=False, indent=1, default=str))
    L = out["losses"]
    print("losses:", {k: v for k, v in L.items() if k not in ("worst10",)})
    for w in L["worst10"]:
        print(f"  {w['vendor_symbol']} {w['name_zh']} {w['entry']}->{w['exit']} ret={w['ret']:+.1%} acct={w['acct_ret']:+.1%} "
              f"pool={w['pool_ret']:+.1%} mkt20={w['mkt_ret_20']:+.1%}")


if __name__ == "__main__":
    main()
