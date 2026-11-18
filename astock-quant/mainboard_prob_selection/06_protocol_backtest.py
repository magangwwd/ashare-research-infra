"""Step 6: protocol-aligned account test for M0..M3 with significance and Holm correction.

Account protocol (from the ChatGPT thread): 100,000 CNY, at most one stock, cash allowed,
buy cost incl. fees <= 50% equity, planned risk incl. fees <= 1% equity, lots of 100,
predict before Tuesday, conditional buy at Tuesday open, exit at Friday open or close,
if unsellable keep holding. "Planned risk does not cap actual loss" -> the primary
sizing ("plan") uses a planned stop distance clip(2*ATR14, 3%, 15%) for sizing only and
does NOT execute an intraweek stop. Sensitivities: "stop" (stop executed), "budget"
(50% budget only, no 1% risk cap), fee x2.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from dataclasses import dataclass

import numpy as np
import polars as pl
from scipy import stats

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE, RES = os.path.join(HERE, "cache"), os.path.join(HERE, "results")
spec = importlib.util.spec_from_file_location("bt", os.path.join(HERE, "03_backtest.py"))
bt = importlib.util.module_from_spec(spec); sys.modules["bt"] = bt; spec.loader.exec_module(bt)
CAPITAL = bt.CAPITAL
# ChatGPT S1 protection rule: distance = max(2*ATR14, 2%); candidates with distance > 8% are not bought
RISK_FILTER = "--no-filter8" not in sys.argv
TAG = "filter8" if RISK_FILTER else "nofilter"
MODELS = {"M0_lr_identity_abs": ("p_m0", "p_m0"),      # (rank column, gate column)
          "M1_lgb_rel_absgate": ("p_rel", "p_abs"),
          "M2_lgb_rel_nomkt": ("p_rel_nomkt", "p_abs"),
          "M3_lr_rel": ("p_lr_rel", "p_abs")}


@dataclass
class Cfg:
    name: str
    score: str
    gate_col: str
    exit: str = "close"
    sizing: str = "plan"        # plan | stop | budget
    gate: float | None = 0.5
    fee_mult: float = 1.0
    seed: int = 0


def build_weeks(df: pl.DataFrame):
    weeks = {}
    cols = sorted({c for v in MODELS.values() for c in v})
    for (ed,), g in df.group_by(["entry_date"]):
        g_b = g.filter(pl.col("buyable"))
        if RISK_FILTER:
            g_b = g_b.filter(2 * pl.col("atr_raw") <= 0.08)
        ranked = {}
        for s, gc in MODELS.values():
            top = g_b.sort(s, descending=True).head(3)
            ranked[s] = list(zip(top["instrument_id"], top[s], top[gc], top["atr_raw"]))
        weeks[np.datetime64(ed, "D")] = dict(
            exit_day=np.datetime64(g["exit_date"][0], "D"), ranked=ranked,
            cand=list(zip(g_b["instrument_id"], g_b["atr_raw"])),
            gate_ref={c: (g_b.sort("p_rel", descending=True)[c][0] if g_b.height else 0.0)
                      for c in cols})
    return weeks


def simulate(cfg: Cfg, weeks, bars, days):
    slip = 0.001 * cfg.fee_mult
    rng = np.random.default_rng(cfg.seed)
    cash, pos, curve, trades = CAPITAL, None, [], []
    for day in days:
        if pos is not None and day > pos["entry_day"]:
            b = bt.bar_at(bars, pos["id"], day)
            if b is not None:
                o, h, l, c, v, adj, dl = b
                k = adj / pos["adj0"]
                can = lambda p: p > dl + 0.001
                px, why = None, None
                if pos["pending"]:
                    px, why = (o if can(o) else (c if can(c) else None)), "carried"
                elif cfg.exit == "open" and day >= pos["exit_day"]:
                    px, why = (o if can(o) else None), "exit_open"
                    pos["pending"] = px is None
                elif cfg.sizing == "stop" and o * k <= pos["stop"]:
                    px, why = (o if can(o) else (c if can(c) else None)), "stop_gap"
                    pos["pending"] = px is None
                elif cfg.sizing == "stop" and l * k <= pos["stop"]:
                    px, why = pos["stop"] / k, "stop"
                elif cfg.exit == "close" and day >= pos["exit_day"]:
                    px, why = (c if can(c) else None), "exit_close"
                    pos["pending"] = px is None
                if px is not None:
                    amt = pos["shares"] * px * k * (1 - slip)
                    cash += amt - bt.fees(amt, day, "sell", cfg.fee_mult)
                    pnl = cash - pos["cash_before"]
                    trades.append(dict(id=pos["id"], entry=str(pos["entry_day"]), exit=str(day),
                                       reason=why, cost=pos["cost"], pnl=pnl, ret=pnl / pos["cost"]))
                    pos = None
        wk = weeks.get(day)
        if pos is None and wk is not None:
            if cfg.score == "random":
                ok = cfg.gate is None or wk["gate_ref"][cfg.gate_col] >= cfg.gate
                pick = ([(wk["cand"][i][0], None, None, wk["cand"][i][1])
                         for i in rng.permutation(len(wk["cand"]))[:3]] if ok and wk["cand"] else [])
            else:
                r = wk["ranked"][cfg.score]
                pick = r if r and (cfg.gate is None or r[0][2] >= cfg.gate) else []
            for iid, _, _, atr in pick:
                b = bt.bar_at(bars, iid, day)
                if b is None:
                    continue
                o, h, l, c, v, adj, dl = b
                p_in = o * (1 + slip)
                dist = max(2.0 * atr, 0.02) if RISK_FILTER else float(np.clip(2.0 * atr, 0.03, 0.15))
                stop = p_in * (1 - dist)
                n_budget = int(cash * 0.5 / (p_in * 1.0004) // 100) * 100
                if cfg.sizing == "budget":
                    n = n_budget
                else:
                    risk_ps = p_in - stop * (1 - slip) + p_in * (0.0005 * cfg.fee_mult + 0.001)
                    n = min(n_budget, int(cash * 0.01 / risk_ps // 100) * 100)
                while n >= 100 and n * p_in + bt.fees(n * p_in, day, "buy", cfg.fee_mult) > cash * 0.5:
                    n -= 100
                if n < 100:
                    continue
                amt = n * p_in
                f = bt.fees(amt, day, "buy", cfg.fee_mult)
                pos = dict(id=iid, shares=n, adj0=adj, stop=stop, entry_day=day,
                           exit_day=wk["exit_day"], pending=False, cash_before=cash, cost=amt + f)
                cash -= amt + f
                break
        mv = 0.0
        if pos is not None:
            lc, la = bt.last_close(bars, pos["id"], day)
            mv = pos["shares"] * lc * la / pos["adj0"]
        curve.append((day, cash + mv))
    return curve, trades


def weekly(curve):
    d = np.array([x[0] for x in curve]); e = np.array([x[1] for x in curve])
    wk = (d.astype("datetime64[W]"))
    last = np.r_[wk[1:] != wk[:-1], True]
    ew = np.r_[CAPITAL, e[last]]
    return ew[1:] / ew[:-1] - 1


def summarize(curve, trades):
    e = np.array([x[1] for x in curve]); d = np.array([x[0] for x in curve])
    yrs = (d[-1] - d[0]).astype(int) / 365.25
    yearly = {}
    for y in range(2020, 2027):
        m = (d >= np.datetime64(f"{y}-01-01")) & (d <= np.datetime64(f"{y}-12-31"))
        i0 = np.argmax(m)
        yearly[y] = float(e[m][-1] / (e[i0 - 1] if i0 > 0 else CAPITAL) - 1)
    tr = np.array([t["ret"] for t in trades]) if trades else np.zeros(1)
    return dict(total_ret=float(e[-1] / CAPITAL - 1), cagr=float((e[-1] / CAPITAL) ** (1 / yrs) - 1),
                max_dd=float((1 - e / np.maximum.accumulate(e)).max()), trades=len(trades),
                win_rate=float((tr > 0).mean()), avg_trade=float(tr.mean()), yearly=yearly)


def holm(p: dict) -> dict:
    items = sorted(p.items(), key=lambda kv: kv[1]); m = len(items); adj, run = {}, 0.0
    for i, (k, v) in enumerate(items):
        run = max(run, min(1.0, (m - i) * v)); adj[k] = run
    return adj


def signal_quality(df):
    ev = df.filter(pl.col("buyable") & pl.col("ret_close").is_not_null()).with_columns(
        (pl.col("ret_close") - pl.col("ret_close").mean().over("signal_date")).alias("ex"))
    out = {}
    for nm, (s, _) in MODELS.items():
        ic = ev.group_by("signal_date").agg(pl.corr(pl.col(s).rank(), pl.col("ret_close").rank()).alias("ic"))
        d = ev.with_columns(((pl.col(s).rank("ordinal", descending=True).over("signal_date") - 1) * 10
                             // pl.len().over("signal_date")).alias("dec"))
        dec = d.group_by(["signal_date", "dec"]).agg(pl.col("ex").mean()).group_by("dec").agg(
            pl.col("ex").mean()).sort("dec")["ex"].to_list()
        t1 = (ev.with_columns(pl.col(s).rank("ordinal", descending=True).over("signal_date").alias("r"))
                .filter(pl.col("r") == 1))
        out[nm] = dict(ic=ic["ic"].mean(), ic_t=ic["ic"].mean() / ic["ic"].std() * np.sqrt(ic.height),
                       top_decile_ex=dec[0], bottom_decile_ex=dec[-1],
                       top1_week_ret=t1["ret_close"].mean(), top1_week_ex=t1["ex"].mean())
    return out


def main():
    t0 = time.time()
    panel = pl.read_parquet(os.path.join(CACHE, "weekly_panel.parquet"),
                            columns=["signal_date", "entry_date", "exit_date", "instrument_id",
                                     "buyable", "ret_close", "atr_raw"])
    pred = pl.read_parquet(os.path.join(CACHE, "predictions_monthly.parquet"))
    df = pred.join(panel, on=["signal_date", "instrument_id"], how="inner")
    _, bars, days = bt.load()
    days = days[days >= np.datetime64("2020-01-01")]
    weeks = build_weeks(df)
    print("risk filter (2ATR<=8%):", RISK_FILTER)
    sig = signal_quality(df.filter(2 * pl.col("atr_raw") <= 0.08) if RISK_FILTER else df)
    for k, v in sig.items():
        print(f"{k:22s} IC={v['ic']:.3f} t={v['ic_t']:.1f} top10%={v['top_decile_ex']:+.2%} "
              f"bot10%={v['bottom_decile_ex']:+.2%} top1={v['top1_week_ret']:+.2%}/wk")

    results, pvals = {}, {}
    rnd_cache = {}
    for sizing in ("plan", "budget", "stop"):
        for ex in ("close", "open"):
            for fm in (1.0, 2.0):
                if sizing == "stop" and fm == 2.0:
                    continue
                key = (sizing, ex, fm)
                rw, rt = [], []
                for sd in range(100):
                    cv, _ = simulate(Cfg("rnd", "random", "p_abs", ex, sizing, 0.5, fm, sd), weeks, bars, days)
                    rw.append(weekly(cv)); rt.append(cv[-1][1] / CAPITAL - 1)
                rw, rt = np.array(rw), np.array(rt)
                rnd_cache[key] = dict(mean=float(rt.mean()), p05=float(np.percentile(rt, 5)),
                                      p95=float(np.percentile(rt, 95)))
                for nm, (s, gc) in MODELS.items():
                    cv, tr = simulate(Cfg(nm, s, gc, ex, sizing, 0.5, fm), weeks, bars, days)
                    m = summarize(cv, tr)
                    diff = weekly(cv) - rw.mean(axis=0)
                    t = diff.mean() / diff.std(ddof=1) * np.sqrt(len(diff))
                    m.update(t_vs_random=float(t), p_one_sided=float(1 - stats.t.cdf(t, len(diff) - 1)),
                             beats_random_share=float((rt < m["total_ret"]).mean()))
                    name = f"{nm}|{sizing}|{ex}|fee{fm:g}"
                    results[name] = m
                    if sizing == "plan" and fm == 1.0:
                        pvals[name] = m["p_one_sided"]
                    if name.startswith("M1") and sizing == "plan" and fm == 1.0:
                        pl.DataFrame(tr).write_csv(os.path.join(RES, f"protocol_trades_M1_{ex}_{TAG}.csv"))
                print(f"done {key} [{time.time()-t0:.0f}s]", flush=True)
    hp = holm(pvals)
    for k in results:
        results[k]["p_holm_primary"] = hp.get(k)
    json.dump(dict(signal=sig, accounts=results, random=({f"{a}|{b}|fee{c:g}": v for (a, b, c), v
                                                          in rnd_cache.items()})),
              open(os.path.join(RES, f"protocol_summary_{TAG}.json"), "w", encoding="utf-8"),
              indent=1, default=float, ensure_ascii=False)
    print(f"\n{'account':42s} {'ret':>8s} {'mdd':>7s} {'n':>4s} {'win':>6s} {'>rnd':>5s} {'t':>5s} {'p_holm':>7s}")
    for k, m in results.items():
        ph = f"{m['p_holm_primary']:.3f}" if m["p_holm_primary"] is not None else "   -"
        print(f"{k:42s} {m['total_ret']:+8.2%} {m['max_dd']:7.2%} {m['trades']:4d} {m['win_rate']:6.1%} "
              f"{m['beats_random_share']:5.0%} {m['t_vs_random']:5.2f} {ph:>7s}")
    print("random:", json.dumps({f"{a}|{b}|fee{c:g}": round(v["mean"], 4) for (a, b, c), v in rnd_cache.items()}))
    for k in ("M1_lgb_rel_absgate|plan|close|fee1", "M1_lgb_rel_absgate|budget|close|fee1"):
        print(k, {y: f"{v:+.1%}" for y, v in results[k]["yearly"].items()})
    print(f"total {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
