"""Step 3: signal evaluation + 10万 single-stock account simulation.

Account rules (carried over from the previous protocol):
  capital 100,000 CNY, at most one stock, cash allowed;
  buy at Tuesday open (skip if opening at limit-up; fall back to rank 2..3);
  cost incl. fees <= 50% of equity; planned risk incl. fees <= 1% of equity;
  board lots of 100; T+1; exit at week-end open or close; if the exit price is at the
  down limit the position is carried and sold at the next sellable open/close.
Stop (assumption made here, parameterised): entry * (1 - clip(2*ATR14, 3%, 15%)),
checked from the day after entry; gap-through fills at the open.
P&L uses raw prices scaled by the adjustment-factor ratio (dividends approximated).
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

import numpy as np
import polars as pl

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
RES = os.path.join(HERE, "results")
CAPITAL = 100_000.0
STAMP_CUT = np.datetime64("2023-08-28")


# ----------------------------------------------------------------------------- data
def load():
    panel = pl.read_parquet(os.path.join(CACHE, "weekly_panel.parquet"),
                            columns=["signal_date", "entry_date", "exit_date", "instrument_id",
                                     "buyable", "ret_close", "ret_open", "atr_raw", "y_rel"])
    pred = pl.read_parquet(os.path.join(CACHE, "predictions.parquet"))
    df = pred.join(panel, on=["signal_date", "instrument_id"], how="inner")
    sim = pl.read_parquet(os.path.join(CACHE, "daily_sim.parquet")).sort(
        ["instrument_id", "trade_date"])
    bars = {}
    for (iid,), g in sim.group_by(["instrument_id"], maintain_order=True):
        d = g["trade_date"].to_numpy().astype("datetime64[D]")
        prev = g["previous_close"].to_numpy()
        st = g["is_st"].to_numpy()
        dl = g["down_limit"].to_numpy()
        dl = np.where(np.isnan(dl), np.round(prev * np.where(st, 0.95, 0.90), 2), dl)
        bars[iid] = (d, g["open"].to_numpy(), g["high"].to_numpy(), g["low"].to_numpy(),
                     g["close"].to_numpy(), g["volume_shares"].to_numpy(),
                     g["adjustment_factor"].to_numpy(), dl)
    days = np.array(sorted(sim["trade_date"].unique().to_numpy().astype("datetime64[D]")))
    return df, bars, days


def bar_at(bars, iid, day):
    b = bars.get(iid)
    if b is None:
        return None
    i = np.searchsorted(b[0], day)
    if i < len(b[0]) and b[0][i] == day and b[5][i] > 0:
        return tuple(x[i] for x in b[1:])  # o,h,l,c,v,adj,dl
    return None


def last_close(bars, iid, day):
    b = bars[iid]
    i = np.searchsorted(b[0], day, side="right") - 1
    return b[4][i], b[6][i]


# ------------------------------------------------------------------------- account
@dataclass
class Cfg:
    name: str
    score: str = "p_rel"          # p_rel | p_lr | random
    gate: float | None = 0.5      # min p_abs of the chosen stock; None = always invest
    exit: str = "close"           # close | open
    fee_mult: float = 1.0
    stop_k: float | None = 2.0     # None = no intraweek stop (risk sized on a 10% move)
    keep_top: float = 0.10         # exit="tue": keep while in this top fraction
    budget: float = 0.50
    risk: float = 0.01
    fallback: int = 3
    seed: int = 0


def fees(amount, day, side, fm):
    comm = max(5.0, amount * 0.00025 * fm)
    transfer = amount * 0.00001
    stamp = 0.0
    if side == "sell":
        stamp = amount * (0.001 if day < STAMP_CUT else 0.0005)
    return comm + transfer + stamp


def simulate(cfg: Cfg, weeks: dict, bars, days):
    slip = 0.001 * cfg.fee_mult
    rng = np.random.default_rng(cfg.seed)
    cash, pos = CAPITAL, None
    equity_curve, trades = [], []
    for day in days:
        # ---------------- manage open position
        if pos is not None and day > pos["entry_day"]:
            b = bar_at(bars, pos["id"], day)
            if b is not None:
                o, h, l, c, v, adj, dl = b
                k = adj / pos["adj0"]            # raw -> entry-equivalent price
                sell_px = None
                can = lambda p: p > dl + 0.001
                if pos["pending"]:
                    sell_px = o if can(o) else (c if can(c) else None)
                    reason = "carried"
                elif cfg.exit == "open" and day >= pos["exit_day"]:
                    sell_px, reason = (o if can(o) else None), "exit_open"
                    if sell_px is None:
                        pos["pending"] = True
                elif cfg.exit == "tue" and day >= pos["exit_day"]:
                    wk_now = weeks.get(day)
                    keep = (wk_now is not None and pos["id"] in wk_now["top_set"] and
                            (cfg.gate is None or wk_now["p_abs"].get(pos["id"], 0) >= cfg.gate))
                    if keep:
                        pos["exit_day"] = wk_now["next_entry"]
                    else:
                        sell_px, reason = (o if can(o) else None), "rebalance_open"
                        if sell_px is None:
                            pos["pending"] = True
                elif o * k <= pos["stop"]:
                    sell_px, reason = (o if can(o) else (c if can(c) else None)), "stop_gap"
                    if sell_px is None:
                        pos["pending"] = True
                elif l * k <= pos["stop"]:
                    sell_px, reason = pos["stop"] / k, "stop"
                elif cfg.exit == "close" and day >= pos["exit_day"]:
                    sell_px, reason = (c if can(c) else None), "exit_close"
                    if sell_px is None:
                        pos["pending"] = True
                if sell_px is not None:
                    px = sell_px * k * (1 - slip)
                    amount = pos["shares"] * px
                    cash += amount - fees(amount, day, "sell", cfg.fee_mult)
                    pnl = cash - pos["cash_before"]
                    trades.append(dict(id=pos["id"], entry=str(pos["entry_day"]),
                                       exit=str(day), reason=reason, shares=pos["shares"],
                                       cost=pos["cost"], pnl=pnl, ret=pnl / pos["cost"],
                                       score=pos["score"], p_abs=pos["p_abs"]))
                    pos = None
        # ---------------- new entry on Tuesdays
        wk = weeks.get(day)
        if pos is None and wk is not None:
            cand = wk["cand"]
            pick = None
            if cfg.score == "random":
                if (cfg.gate is None or wk["gate_p"] >= cfg.gate) and len(cand) > 0:
                    order = rng.permutation(len(cand))[:cfg.fallback]
                    pick = [cand[i] for i in order]
            else:
                ranked = wk["ranked"][cfg.score][:cfg.fallback]
                if ranked and (cfg.gate is None or ranked[0][2] >= cfg.gate):
                    pick = ranked
            for cnd in pick or []:
                iid, score, p_abs, atr = cnd
                b = bar_at(bars, iid, day)
                if b is None:
                    continue
                o, h, l, c, v, adj, dl = b
                if not wk["buyable"].get(iid, False):
                    continue
                equity = cash
                px = o * (1 + slip)
                dist = (float(np.clip(cfg.stop_k * atr, 0.03, 0.15)) if cfg.stop_k
                        else 0.10)
                stop = px * (1 - dist)
                per_share_risk = px - stop * (1 - slip) + px * (0.0005 * cfg.fee_mult + 0.001)
                n_risk = int((equity * cfg.risk) / per_share_risk // 100) * 100
                n_budget = int((equity * cfg.budget) / (px * 1.0004) // 100) * 100
                n = min(n_risk, n_budget)
                while n >= 100 and n * px + fees(n * px, day, "buy", cfg.fee_mult) > equity * cfg.budget:
                    n -= 100
                if n < 100:
                    continue
                amount = n * px
                f = fees(amount, day, "buy", cfg.fee_mult)
                if not cfg.stop_k:
                    stop = 0.0
                pos = dict(id=iid, shares=n, adj0=adj, stop=stop, entry_day=day,
                           exit_day=wk["next_entry"] if cfg.exit == "tue" else wk["exit_day"], pending=False, cash_before=cash,
                           cost=amount + f, score=score, p_abs=p_abs)
                cash -= amount + f
                break
        # ---------------- mark to market
        mv = 0.0
        if pos is not None:
            lc, ladj = last_close(bars, pos["id"], day)
            mv = pos["shares"] * lc * ladj / pos["adj0"]
        equity_curve.append((day, cash + mv, mv > 0))
    return equity_curve, trades


def metrics(curve, trades):
    eq = np.array([x[1] for x in curve])
    d = np.array([x[0] for x in curve])
    years = (d[-1] - d[0]).astype(int) / 365.25
    dd = 1 - eq / np.maximum.accumulate(eq)
    tr = np.array([t["ret"] for t in trades]) if trades else np.array([0.0])
    yearly = {}
    for y in range(2020, 2027):
        m = (d >= np.datetime64(f"{y}-01-01")) & (d <= np.datetime64(f"{y}-12-31"))
        if m.any():
            prev = eq[np.argmax(m) - 1] if np.argmax(m) > 0 else CAPITAL
            yearly[y] = eq[m][-1] / prev - 1
    return dict(final=eq[-1], total_ret=eq[-1] / CAPITAL - 1,
                cagr=(eq[-1] / CAPITAL) ** (1 / years) - 1, max_dd=dd.max(),
                trades=len(trades), win_rate=float((tr > 0).mean()) if trades else 0.0,
                avg_trade_ret=float(tr.mean()), exposure_days=float(np.mean([x[2] for x in curve])),
                yearly=yearly)


# ----------------------------------------------------------------------- signal eval
def signal_eval(df: pl.DataFrame) -> dict:
    ev = df.filter(pl.col("buyable") & pl.col("ret_close").is_not_null())
    ev = ev.with_columns((pl.col("ret_close") - pl.col("ret_close").mean().over("signal_date"))
                         .alias("ex"))
    out = {}
    for s in ["p_rel", "p_lr"]:
        ic = (ev.group_by("signal_date")
                .agg(pl.corr(pl.col(s).rank(), pl.col("ret_close").rank()).alias("ic"))
                .sort("signal_date"))
        dec = ev.with_columns(
            ((pl.col(s).rank("ordinal", descending=True).over("signal_date") - 1) * 10 //
             pl.len().over("signal_date")).alias("dec"))
        by_dec = (dec.group_by(["signal_date", "dec"]).agg(pl.col("ex").mean())
                     .group_by("dec").agg(pl.col("ex").mean().alias("mean_excess"),
                                          (pl.col("ex") > 0).mean().alias("hit"))
                     .sort("dec"))
        top = {}
        for k in (1, 5, 20):
            t = (ev.with_columns(pl.col(s).rank("ordinal", descending=True)
                                 .over("signal_date").alias("r"))
                   .filter(pl.col("r") <= k).group_by("signal_date")
                   .agg(pl.col("ret_close").mean().alias("ret"), pl.col("ex").mean().alias("ex")))
            top[k] = dict(mean_week_ret=t["ret"].mean(), mean_week_excess=t["ex"].mean(),
                          hit_abs=(t["ret"] > 0).mean(), hit_excess=(t["ex"] > 0).mean())
        ic_y = (ic.group_by(pl.col("signal_date").dt.year().alias("y"))
                  .agg(pl.col("ic").mean()).sort("y"))
        out[s] = dict(ic_mean=ic["ic"].mean(), ic_t=ic["ic"].mean() / ic["ic"].std() *
                      np.sqrt(ic.height), ic_pos_share=(ic["ic"] > 0).mean(),
                      ic_by_year=dict(zip(ic_y["y"].to_list(), ic_y["ic"].to_list())),
                      decile_excess=by_dec["mean_excess"].to_list(),
                      decile_hit=by_dec["hit"].to_list(), top=top)
    # gate diagnostics: market-level calibration of p_abs
    gate = (ev.group_by("signal_date")
              .agg(pl.col("p_abs").mean().alias("p"), (pl.col("ret_close") > 0).mean()
                   .alias("up_share"), pl.col("ret_close").mean().alias("pool_ret")))
    on = gate.filter(pl.col("p") >= 0.5)
    off = gate.filter(pl.col("p") < 0.5)
    out["gate_market"] = dict(weeks_on=on.height, weeks_off=off.height,
                              pool_ret_on=on["pool_ret"].mean(),
                              pool_ret_off=off["pool_ret"].mean(),
                              corr_p_upshare=gate.select(pl.corr("p", "up_share")).item())
    return out


def topk_portfolio(df, score, k, gate, cost=0.0035):
    """Diagnostic only: weekly equal-weight top-k, full equity, no stop."""
    ev = df.filter(pl.col("buyable") & pl.col("ret_close").is_not_null()).with_columns(
        pl.col(score).rank("ordinal", descending=True).over("signal_date").alias("r"))
    t = (ev.filter(pl.col("r") <= k).group_by("signal_date")
           .agg(pl.col("ret_close").mean().alias("ret"), pl.col("p_abs").mean().alias("pa"))
           .sort("signal_date"))
    if gate is not None:
        t = t.with_columns(pl.when(pl.col("pa") >= gate).then(pl.col("ret") - cost)
                           .otherwise(0.0).alias("net"))
    else:
        t = t.with_columns((pl.col("ret") - cost).alias("net"))
    eq = np.cumprod(1 + t["net"].to_numpy())
    dd = (1 - eq / np.maximum.accumulate(eq)).max()
    return dict(total_ret=eq[-1] - 1, max_dd=dd, weeks_invested=int((t["net"] != 0).sum()),
                weeks=t.height)


def pool_portfolio(df, cost=0.0035):
    ev = df.filter(pl.col("buyable") & pl.col("ret_close").is_not_null())
    t = ev.group_by("signal_date").agg(pl.col("ret_close").mean()).sort("signal_date")
    eq = np.cumprod(1 + t["ret_close"].to_numpy() - cost)
    return dict(total_ret=eq[-1] - 1, max_dd=(1 - eq / np.maximum.accumulate(eq)).max())


# ----------------------------------------------------------------------------- main
def main():
    t0 = time.time()
    df, bars, days = load()
    days = days[days >= np.datetime64("2020-01-01")]
    print(f"loaded in {time.time()-t0:.0f}s; test weeks={df['signal_date'].n_unique()}")

    sig = signal_eval(df)
    diag = {f"top{k}_{s}_{'gate' if g else 'nogate'}": topk_portfolio(df, s, k, g)
            for s in ("p_rel", "p_lr") for k in (1, 5, 20) for g in (None, 0.5)}
    diag["pool_equal_weight"] = pool_portfolio(df)

    # week dictionaries
    weeks = {}
    for (ed,), g in df.group_by(["entry_date"]):
        g_b = g.filter(pl.col("buyable"))
        ranked = {}
        for s in ("p_rel", "p_lr"):
            gs = g_b.sort(s, descending=True).head(10)
            ranked[s] = list(zip(gs["instrument_id"], gs[s], gs["p_abs"], gs["atr_raw"]))
        top = ranked["p_rel"][0] if ranked["p_rel"] else None
        n_top = max(1, int(round(g_b.height * 0.10)))
        top_set = set(g_b.sort("p_rel", descending=True).head(n_top)["instrument_id"])
        weeks[np.datetime64(ed, "D")] = dict(
            exit_day=np.datetime64(g["exit_date"][0], "D"), ranked=ranked,
            buyable=dict(zip(g["instrument_id"], g["buyable"])),
            cand=list(zip(g_b["instrument_id"], g_b["p_rel"], g_b["p_abs"], g_b["atr_raw"])),
            gate_p=top[2] if top else 0.0, top_set=top_set,
            p_abs=dict(zip(g_b["instrument_id"], g_b["p_abs"])))
    ks = sorted(weeks)
    for a, b in zip(ks, ks[1:] + [None]):
        weeks[a]["next_entry"] = b if b is not None else np.datetime64("2100-01-01")

    cfgs = [Cfg("A_lgb_close_gate"), Cfg("B_lgb_open_gate", exit="open"),
            Cfg("C_lgb_close_nogate", gate=None), Cfg("D_lgb_open_nogate", exit="open", gate=None),
            Cfg("E_lr_close_gate", score="p_lr"), Cfg("F_lr_close_nogate", score="p_lr", gate=None),
            Cfg("A2_fee2x", fee_mult=2.0), Cfg("C2_nogate_fee2x", gate=None, fee_mult=2.0),
            Cfg("G_stop3atr", stop_k=3.0), Cfg("H_nostop", stop_k=None),
            Cfg("I_tue_keep_top10", exit="tue", stop_k=None),
            Cfg("I2_tue_keep_fee2x", exit="tue", stop_k=None, fee_mult=2.0),
            Cfg("J_nostop_budget50", stop_k=None, risk=1.0),
            Cfg("K_tue_keep_budget50", exit="tue", stop_k=None, risk=1.0),
            Cfg("K2_tue_keep_budget50_fee2x", exit="tue", stop_k=None, risk=1.0, fee_mult=2.0)]
    accounts, all_trades = {}, {}
    for cfg in cfgs:
        curve, trades = simulate(cfg, weeks, bars, days)
        accounts[cfg.name] = metrics(curve, trades)
        all_trades[cfg.name] = trades
        if cfg.name == "A_lgb_close_gate":
            pl.DataFrame({"date": [str(x[0]) for x in curve], "equity": [x[1] for x in curve]}
                         ).write_csv(os.path.join(RES, "equity_A.csv"))
        m = accounts[cfg.name]
        print(f"{cfg.name:22s} ret={m['total_ret']:+.2%} mdd={m['max_dd']:.2%} "
              f"n={m['trades']} win={m['win_rate']:.1%} avg={m['avg_trade_ret']:+.2%}", flush=True)

    # random baselines (same gate weeks as A / always invest as C)
    rand = {}
    rand_specs = {"random_gate": dict(gate=0.5), "random_nogate": dict(gate=None),
                  "random_H_nostop": dict(gate=0.5, stop_k=None),
                  "random_J_budget50": dict(gate=0.5, stop_k=None, risk=1.0)}
    for nm, kw in rand_specs.items():
        rets = []
        for sd in range(100):
            curve, trades = simulate(Cfg(nm, score="random", seed=sd, **kw), weeks, bars, days)
            rets.append(curve[-1][1] / CAPITAL - 1)
        rets = np.array(rets)
        rand[nm] = dict(all=rets.tolist(), mean=rets.mean(), p05=np.percentile(rets, 5),
                        p50=np.median(rets), p95=np.percentile(rets, 95))
        print(f"{nm}: mean={rets.mean():+.2%} p05={rand[nm]['p05']:+.2%} "
              f"p95={rand[nm]['p95']:+.2%}", flush=True)
    pairs = {"A_lgb_close_gate": "random_gate", "C_lgb_close_nogate": "random_nogate",
             "H_nostop": "random_H_nostop", "J_nostop_budget50": "random_J_budget50"}
    for nm, base in pairs.items():
        r_ = np.array(rand[base]["all"])
        accounts[nm]["random_baseline"] = base
        accounts[nm]["share_random_below"] = float((r_ < accounts[nm]["total_ret"]).mean())
        print(f"{nm} beats {accounts[nm]['share_random_below']:.0%} of {base}")

    for nm, tr in all_trades.items():
        if tr:
            pl.DataFrame(tr).write_csv(os.path.join(RES, f"trades_{nm}.csv"))
    summary = dict(signal=sig, diagnostics=diag, accounts=accounts, random=rand)
    with open(os.path.join(RES, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1, default=float)
    print(json.dumps(dict(signal={k: {kk: vv for kk, vv in v.items() if kk in
                                       ("ic_mean", "ic_t", "ic_pos_share", "top", "decile_excess")}
                                   if isinstance(v, dict) and "ic_mean" in v else v
                                   for k, v in sig.items()}, diagnostics=diag),
                     ensure_ascii=False, indent=1, default=float))
    print(f"total {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
