"""Step 12: win-rate report for the one-stock V7 strategy (corrected core engine).

Per-trade (net of all costs): win rate, avg win / avg loss, payoff ratio, expectancy,
longest losing streak, distribution; by year and by period; relative win (beat the
protection-pool average that week); compared with 100 random-stock accounts under the
same gate weeks and rules.
"""
import json
import os
import sys

import numpy as np
import polars as pl

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import core  # noqa: E402

CACHE, RES = os.path.join(HERE, "cache"), os.path.join(HERE, "results")


def stats(tr: pl.DataFrame) -> dict:
    r = tr["ret"].to_numpy(); a = tr["acct_ret"].to_numpy()
    w, l = r[r > 0], r[r <= 0]
    streak = cur = 0
    for x in r:
        cur = cur + 1 if x <= 0 else 0; streak = max(streak, cur)
    return dict(trades=len(r), win_rate=float((r > 0).mean()), avg_win=float(w.mean()) if len(w) else 0.0,
                avg_loss=float(l.mean()) if len(l) else 0.0,
                payoff=float(w.mean() / -l.mean()) if len(w) and len(l) and l.mean() < 0 else float("nan"),
                expectancy=float(r.mean()), median=float(np.median(r)), worst=float(r.min()), best=float(r.max()),
                worst_acct=float(a.min()), longest_losing_streak=int(streak),
                share_gt_plus5=float((r > 0.05).mean()), share_lt_minus5=float((r < -0.05).mean()))


def main():
    panel = pl.read_parquet(os.path.join(CACHE, "weekly_panel.parquet"),
                            columns=["signal_date", "entry_date", "exit_date", "instrument_id", "atr_raw",
                                     "buyable", "ret_close"])
    p = pl.read_parquet(os.path.join(CACHE, "predictions.parquet"), columns=["signal_date", "instrument_id", "p_abs"])
    e = pl.read_parquet(os.path.join(CACHE, "predictions_v67.parquet"), columns=["signal_date", "instrument_id", "p_rel_ens"])
    pred = p.join(e, on=["signal_date", "instrument_id"])
    bars, days = core.load_bars(os.path.join(CACHE, "daily_sim.parquet"))
    days = days[days >= np.datetime64("2020-01-01")]
    plans, picks = core.build_plans(pred, panel)
    out = {}
    for name, rules in (("budget50", core.Rules()), ("risk1pct", core.Rules(risk=0.01))):
        curve, trades = core.simulate(plans, bars, days, rules)
        tr = pl.DataFrame(trades).with_columns(pl.col("entry").str.to_date().alias("entry_d"))
        # relative win: trade return vs protection-pool average for that week (Tue open -> week close)
        pool = (panel.filter(pl.col("buyable") & (2 * pl.col("atr_raw") <= core.POOL_ATR_MAX))
                     .group_by("entry_date").agg(pl.col("ret_close").mean().alias("pool_ret")))
        tr = tr.join(pool, left_on="entry_d", right_on="entry_date", how="left")
        res = dict(all=stats(tr))
        res["relative_win_rate"] = float((tr["ret"] + 0.003 > tr["pool_ret"]).mean())  # gross vs pool
        res["by_year"] = {int(y): stats(g) for (y,), g in tr.group_by(pl.col("entry_d").dt.year()).agg(pl.all())
                          .explode(pl.exclude("entry_d")).group_by("entry_d", maintain_order=True)} if False else {}
        for y in range(2020, 2027):
            g = tr.filter(pl.col("entry_d").dt.year() == y)
            if g.height:
                res["by_year"][y] = stats(g)
        res["2020_2023"] = stats(tr.filter(pl.col("entry_d").dt.year() <= 2023))
        res["2024_2026"] = stats(tr.filter(pl.col("entry_d").dt.year() >= 2024))
        n_weeks = len(plans); n_inv = sum(v["invest"] for v in plans.values())
        res["weeks"] = dict(total=n_weeks, gate_open=n_inv, traded=len(trades), cash_share=1 - len(trades) / n_weeks)
        # random baseline (same gate weeks, random stock from protection pool)
        pools = {}
        for (sd,), g in pred.join(panel.select("signal_date", "instrument_id", "entry_date", "atr_raw"),
                                  on=["signal_date", "instrument_id"]).group_by(["signal_date"]):
            ed = np.datetime64(g["entry_date"][0], "D")
            q = g.filter(2 * pl.col("atr_raw") <= core.POOL_ATR_MAX)
            if ed in plans:
                pools[ed] = (q["instrument_id"].to_list(), q["atr_raw"].to_list())
        rw = []
        for sd in range(100):
            rng = np.random.default_rng(sd)
            rp = {ed: dict(plans[ed], cands=[(ids[i], at[i]) for i in rng.permutation(len(ids))[:3]])
                  for ed, (ids, at) in pools.items()}
            _, t2 = core.simulate(rp, bars, days, rules)
            rw.append([x["ret"] for x in t2])
        allr = np.concatenate([np.array(x) for x in rw])
        res["random"] = dict(win_rate=float((allr > 0).mean()), expectancy=float(allr.mean()),
                             win_rate_p05=float(np.percentile([np.mean(np.array(x) > 0) for x in rw], 5)),
                             win_rate_p95=float(np.percentile([np.mean(np.array(x) > 0) for x in rw], 95)),
                             share_random_accounts_with_lower_win_rate=float(np.mean(
                                 [np.mean(np.array(x) > 0) < res["all"]["win_rate"] for x in rw])))
        out[name] = res
        a = res["all"]
        print(f"[{name}] trades={a['trades']} win={a['win_rate']:.1%} avg_win={a['avg_win']:+.2%} "
              f"avg_loss={a['avg_loss']:+.2%} payoff={a['payoff']:.2f} exp={a['expectancy']:+.2%} "
              f"rel_win={res['relative_win_rate']:.1%} streak={a['longest_losing_streak']} "
              f"cash={res['weeks']['cash_share']:.0%} | random win={res['random']['win_rate']:.1%} "
              f"[{res['random']['win_rate_p05']:.1%},{res['random']['win_rate_p95']:.1%}] exp={res['random']['expectancy']:+.2%}")
        print("   by year:", {y: f"{v['win_rate']:.0%}/{v['trades']}/{v['expectancy']:+.2%}" for y, v in res["by_year"].items()})
        print("   2020-23:", f"{res['2020_2023']['win_rate']:.1%} exp {res['2020_2023']['expectancy']:+.2%}",
              "| 2024-26:", f"{res['2024_2026']['win_rate']:.1%} exp {res['2024_2026']['expectancy']:+.2%}")
    json.dump(out, open(os.path.join(RES, "winrate_report.json"), "w", encoding="utf-8"), indent=1, default=float)


if __name__ == "__main__":
    main()
