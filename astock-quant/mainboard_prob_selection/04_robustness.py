"""Step 4: robustness checks for the no-stop variants (H, J) chosen after step 3."""
import importlib.util, json, os, sys
import numpy as np
import polars as pl
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("bt", os.path.join(HERE, "03_backtest.py"))
bt = importlib.util.module_from_spec(spec); sys.modules["bt"] = bt; spec.loader.exec_module(bt)
from importlib import import_module

df, bars, days = bt.load()
days = days[days >= np.datetime64("2020-01-01")]
# rebuild week dicts exactly as in 03 (copy of the block)
weeks = {}
for (ed,), g in df.group_by(["entry_date"]):
    g_b = g.filter(pl.col("buyable"))
    ranked = {s: list(zip(*[g_b.sort(s, descending=True).head(10)[c] for c in
                            ("instrument_id", s, "p_abs", "atr_raw")])) for s in ("p_rel", "p_lr")}
    top = ranked["p_rel"][0] if ranked["p_rel"] else None
    weeks[np.datetime64(ed, "D")] = dict(
        exit_day=np.datetime64(g["exit_date"][0], "D"), ranked=ranked,
        buyable=dict(zip(g["instrument_id"], g["buyable"])),
        cand=list(zip(g_b["instrument_id"], g_b["p_rel"], g_b["p_abs"], g_b["atr_raw"])),
        gate_p=top[2] if top else 0.0, top_set=set(), p_abs={})
ks = sorted(weeks)
for a, b in zip(ks, ks[1:] + [None]):
    weeks[a]["next_entry"] = b if b is not None else np.datetime64("2100-01-01")

C = bt.Cfg
cfgs = [C("J", stop_k=None, risk=1.0), C("J_fee2x", stop_k=None, risk=1.0, fee_mult=2.0),
        C("J_fee3x", stop_k=None, risk=1.0, fee_mult=3.0),
        C("J_open", stop_k=None, risk=1.0, exit="open"), C("J_lr", stop_k=None, risk=1.0, score="p_lr"),
        C("J_nogate", stop_k=None, risk=1.0, gate=None), C("J_gate055", stop_k=None, risk=1.0, gate=0.55),
        C("J_gate045", stop_k=None, risk=1.0, gate=0.45),
        C("H", stop_k=None), C("H_fee2x", stop_k=None, fee_mult=2.0),
        C("J_stop3atr", stop_k=3.0, risk=1.0)]
res = {}
for c in cfgs:
    curve, trades = bt.simulate(c, weeks, bars, days)
    m = bt.metrics(curve, trades); res[c.name] = m
    tr = np.array([t["ret"] for t in trades])
    tstat = tr.mean() / tr.std(ddof=1) * np.sqrt(len(tr))
    m["trade_t"] = float(tstat)
    print(f"{c.name:11s} ret={m['total_ret']:+.2%} cagr={m['cagr']:+.2%} mdd={m['max_dd']:.2%} "
          f"n={m['trades']} win={m['win_rate']:.1%} avg={m['avg_trade_ret']:+.2%} t={tstat:.2f} "
          f"yearly=" + " ".join(f"{y}:{v:+.1%}" for y, v in m["yearly"].items()), flush=True)
    if c.name == "J":
        pl.DataFrame(trades).write_csv(os.path.join(HERE, "results", "trades_J.csv"))
        pl.DataFrame({"date": [str(x[0]) for x in curve], "equity": [x[1] for x in curve]}
                     ).write_csv(os.path.join(HERE, "results", "equity_J.csv"))
# random under J structure, yearly
rand_y = {y: [] for y in range(2020, 2027)}; rand_tot = []
for sd in range(100):
    curve, trades = bt.simulate(C("rJ", score="random", stop_k=None, risk=1.0, seed=sd), weeks, bars, days)
    m = bt.metrics(curve, trades); rand_tot.append(m["total_ret"])
    for y, v in m["yearly"].items(): rand_y[y].append(v)
print("random J yearly mean:", " ".join(f"{y}:{np.mean(v):+.1%}" for y, v in rand_y.items()))
print("J beats random in years:", sum(res["J"]["yearly"][y] > np.mean(rand_y[y]) for y in rand_y), "/ 7")
json.dump(dict(variants=res, random_J_yearly_mean={y: float(np.mean(v)) for y, v in rand_y.items()},
               random_J_total=dict(mean=float(np.mean(rand_tot)), p95=float(np.percentile(rand_tot, 95)))),
          open(os.path.join(HERE, "results", "robustness.json"), "w", encoding="utf-8"),
          indent=1, default=float)
