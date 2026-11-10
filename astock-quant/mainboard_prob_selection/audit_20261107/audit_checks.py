"""Read-only audit of research inputs; writes only inside audit_20261107.

Run: python -I audit_20261107/audit_checks.py
Uses existing predictions, never retrains or overwrites research/live outputs.
"""
from __future__ import annotations

import ast
import datetime as dt
import hashlib
import importlib.metadata
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import polars as pl
from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent
sys.stdout.reconfigure(encoding="utf-8")


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


def emit(name, obj):
    print(name, json.dumps(obj, ensure_ascii=False, default=str), flush=True)


def main():
    evidence = {}
    versions = {n: importlib.metadata.version(n) for n in
                ("numpy", "polars", "lightgbm", "scikit-learn", "scipy", "pyarrow")}
    evidence["environment"] = {"python": sys.version, "packages": versions}
    manifest = []
    for f in sorted(ROOT.glob("*.py")) + sorted(ROOT.glob("*.md")) + sorted((ROOT / "results").glob("*.json")):
        b = f.read_bytes()
        manifest.append({"file": str(f.relative_to(ROOT)), "bytes": len(b),
                         "sha256": hashlib.sha256(b).hexdigest()})
        if f.suffix == ".py":
            ast.parse(b.decode("utf-8-sig"), filename=str(f))
    evidence["file_manifest"] = manifest
    panel = pl.read_parquet(ROOT / "cache/weekly_panel.parquet")
    pred = pl.read_parquet(ROOT / "cache/predictions.parquet")
    ens = pl.read_parquet(ROOT / "cache/predictions_v67.parquet")
    feats = [c for c in panel.columns if c.startswith(("rk_", "mkt_"))]
    evidence["panel"] = {
        "shape": panel.shape, "features": len(feats),
        "first_signal": panel["signal_date"].min(), "last_signal": panel["signal_date"].max(),
        "weeks": panel["signal_date"].n_unique(), "stocks": panel["instrument_id"].n_unique(),
        "duplicates": panel.select(pl.struct("signal_date", "instrument_id").is_duplicated().sum()).item(),
        "missing_ret": panel["ret_close"].null_count(),
        "missing_ret_buyable": panel.filter(pl.col("buyable"))["ret_close"].null_count(),
        "feature_nan": int(np.isnan(panel.select(feats).to_numpy()).sum()),
        "feature_inf": int(np.isinf(panel.select(feats).to_numpy()).sum()),
    }
    evidence["prediction_integrity"] = {
        "base_rows": pred.height, "ensemble_rows": ens.height,
        "base_duplicates": pred.select(pl.struct("signal_date", "instrument_id").is_duplicated().sum()).item(),
        "ensemble_duplicates": ens.select(pl.struct("signal_date", "instrument_id").is_duplicated().sum()).item(),
        "ensemble_formula_max_error": ens.select(
            (pl.col("p_rel_ens") - (pl.col("p_rel_s1") + pl.col("p_rel_s2") +
             pred["p_rel"]) / 3).abs().max()).item()
            if ens.select("signal_date", "instrument_id").equals(pred.select("signal_date", "instrument_id")) else "row order differs",
    }
    refits = pred.select("model_from").unique().sort("model_from")["model_from"].to_list()
    cuts = []
    for start in refits:
        tr = panel.filter((pl.col("signal_date") >= dt.date(2015, 1, 1)) &
                          (pl.col("exit_date") < start) & pl.col("buyable") & pl.col("y_rel").is_not_null())
        cuts.append({"refit": start, "training_rows": tr.height, "last_label_exit": tr["exit_date"].max()})
    evidence["training_cuts"] = cuts
    emit("integrity", {k: evidence[k] for k in ("panel", "prediction_integrity")})

    im = module("audit_im", "08_improve.py")
    pb = im.pb
    _, bars, days = pb.bt.load()
    days = days[days >= np.datetime64("2020-01-01")]
    original = im.load()
    weeks, _ = im.weeks_for(original, im.VARIANTS["V7_ensemble"])
    cv, tr = pb.simulate(pb.Cfg("audit", "v", "g", "close", "budget"), weeks, bars, days)
    m = pb.summarize(cv, tr)
    saved = json.loads((ROOT / "results/improve_summary.json").read_text(encoding="utf-8"))["V7_ensemble"]
    evidence["v7_reproduction"] = {"metrics": m, "saved": saved["accounts"]["budget|fee1"],
        "return_delta": m["total_ret"] - saved["accounts"]["budget|fee1"]["total_ret"],
        "equity_minus_realized_pnl": cv[-1][1] - pb.CAPITAL - sum(x["pnl"] for x in tr)}
    emit("v7_reproduction", evidence["v7_reproduction"])

    # Preserve signal-date ranking, gate on its top 1, then check only those original top 3.
    full = pred.join(panel.select("signal_date", "instrument_id", "entry_date", "exit_date", "buyable", "atr_raw"),
                     on=["signal_date", "instrument_id"]).join(
                         ens.select("signal_date", "instrument_id", "p_rel_ens"), on=["signal_date", "instrument_id"])
    full = full.filter(2 * pl.col("atr_raw") <= 0.08)
    corrected, changes = {}, []
    for (ed,), g in full.group_by(["entry_date"]):
        ranked = g.sort("p_rel_ens", descending=True)
        fixed = ranked.head(3)
        gv = float(ranked["p_abs"][0])
        available = fixed.filter(pl.col("buyable"))
        original_rank = ranked.filter(pl.col("buyable"))
        if not original_rank.height:
            continue
        raw_id = ranked["instrument_id"][0]
        old_id = original_rank["instrument_id"][0]
        new_id = available["instrument_id"][0] if available.height else None
        old_on = bool(original_rank["p_abs"][0] >= 0.5)
        new_on = gv >= 0.5
        if old_id != new_id or old_on != new_on or raw_id != old_id:
            changes.append({"entry": str(ed), "raw_top1": raw_id, "old_pick": old_id,
                "fixed_top3_pick": new_id, "old_gate": old_on, "fixed_gate": new_on,
                "raw_top3": "|".join(fixed["instrument_id"].to_list())})
        candidates = g.filter(pl.col("buyable"))
        corrected[np.datetime64(ed, "D")] = {
            "exit_day": np.datetime64(g["exit_date"][0], "D"),
            "ranked": {"v": [(r["instrument_id"], r["p_rel_ens"], gv, r["atr_raw"])
                              for r in available.iter_rows(named=True)]},
            "cand": list(zip(candidates["instrument_id"], candidates["atr_raw"])), "gate_ref": {"g": gv}}
    c2, t2 = pb.simulate(pb.Cfg("fixed_top3", "v", "g", "close", "budget"), corrected, bars, days)
    evidence["fixed_top3_sensitivity"] = {"affected_weeks": len(changes), "metrics": pb.summarize(c2, t2),
        "gate_changed_weeks": sum(x["old_gate"] != x["fixed_gate"] for x in changes),
        "return_delta": c2[-1][1] / pb.CAPITAL - 1 - m["total_ret"], "examples": changes[:10],
        "interpretation": "Alternative execution faithful to live top-1 gate and original top-3 fallback; existing predictions unchanged."}
    if changes:
        pl.DataFrame(changes).write_csv(OUT / "ranking_changes.csv")
    pl.DataFrame(t2).write_csv(OUT / "v7_fixed_top3_trades.csv")
    emit("fixed_top3_sensitivity", evidence["fixed_top3_sensitivity"])

    # NumPy datetime64[W] anchors at Thursday; demonstrate and compare t statistics.
    example_days = np.arange(np.datetime64("2026-11-02"), np.datetime64("2026-11-07"))
    evidence["weekly_buckets"] = [{"day": str(d), "numpy_week_start": str(d.astype("datetime64[W]").astype("datetime64[D]"))}
                                 for d in example_days]
    def iso_weekly(curve):
        d = [dt.date.fromisoformat(str(x[0])).isocalendar()[:2] for x in curve]
        idx = [i for i in range(len(d)) if i == len(d)-1 or d[i] != d[i+1]]
        e = np.r_[pb.CAPITAL, [curve[i][1] for i in idx]]
        return e[1:] / e[:-1] - 1
    rnd_old, rnd_iso, rtot = [], [], []
    for seed in range(100):
        cr, _ = pb.simulate(pb.Cfg("random", "random", "g", "close", "budget", seed=seed), weeks, bars, days)
        rnd_old.append(pb.weekly(cr)); rnd_iso.append(iso_weekly(cr)); rtot.append(cr[-1][1]/pb.CAPITAL-1)
    old_diff = pb.weekly(cv) - np.mean(rnd_old, axis=0)
    iso_diff = iso_weekly(cv) - np.mean(rnd_iso, axis=0)
    evidence["account_weekly_t"] = {"numpy_thursday_t": im.tstat(old_diff), "iso_monday_t": im.tstat(iso_diff),
        "random_mean": float(np.mean(rtot)), "beats_random": float(np.mean(np.array(rtot) < m["total_ret"]))}
    v0weeks, _ = im.weeks_for(original, im.VARIANTS["V0_base"])
    v0curve, _ = pb.simulate(pb.Cfg("v0", "v", "g", "close", "budget"), v0weeks, bars, days)
    v0rnd_old, v0rnd_iso = [], []
    for seed in range(100):
        cr, _ = pb.simulate(pb.Cfg("random", "random", "g", "close", "budget", seed=seed), v0weeks, bars, days)
        v0rnd_old.append(pb.weekly(cr)); v0rnd_iso.append(iso_weekly(cr))
    evidence["v0_account_weekly_t"] = {
        "numpy_thursday_t": im.tstat(pb.weekly(v0curve)-np.mean(v0rnd_old, axis=0)),
        "iso_monday_t": im.tstat(iso_weekly(v0curve)-np.mean(v0rnd_iso, axis=0))}
    evidence["multiplicity"] = {"t_2_45_df_331_one_sided_p": float(stats.t.sf(2.45, 331)),
        "bonferroni_30_one_sided": float(min(1, stats.t.sf(2.45, 331)*30)),
        "v7_confirm_t": saved["stage1"]["confirm"]["excess_t"],
        "v7_confirm_n": saved["stage1"]["confirm"]["invested"],
        "v7_confirm_p_one_sided": float(stats.t.sf(saved["stage1"]["confirm"]["excess_t"], saved["stage1"]["confirm"]["invested"]-1)),
        "v7_confirm_p_two_sided": float(2*stats.t.sf(saved["stage1"]["confirm"]["excess_t"], saved["stage1"]["confirm"]["invested"]-1))}
    emit("statistics", {k: evidence[k] for k in ("weekly_buckets", "account_weekly_t", "multiplicity")})

    # Verify fee-multiplier semantics directly, independent of market regulation.
    day = np.datetime64("2026-11-06")
    evidence["fees"] = {"amount": 50000, "buy_fee1": pb.bt.fees(50000, day, "buy", 1),
        "buy_fee2": pb.bt.fees(50000, day, "buy", 2), "sell_fee1": pb.bt.fees(50000, day, "sell", 1),
        "sell_fee2": pb.bt.fees(50000, day, "sell", 2)}
    live = pl.read_parquet(ROOT / "live/cache/weekly_panel.parquet", columns=["signal_date", "entry_date", "exit_date"])
    evidence["live"] = {"last_signal": live["signal_date"].max(),
        "last_schedule": live.filter(pl.col("signal_date") == live["signal_date"].max()).unique().to_dicts(),
        "forward_log_exists": (ROOT/"live/forward_log.csv").exists()}
    trades = pl.DataFrame(tr).sort("entry").with_columns(
        (pl.lit(pb.CAPITAL)+pl.col("pnl").cum_sum()-pl.col("pnl")).alias("equity_before"))
    trades = trades.with_columns((pl.col("pnl")/pl.col("equity_before")).alias("account_return"))
    evidence["v7_worst_trade"] = trades.sort("account_return").head(1).to_dicts()[0]
    latest = full.filter(pl.col("signal_date")==full["signal_date"].max()).sort("p_rel_ens", descending=True)
    evidence["latest_quarterly_top3"] = latest.head(3).select("instrument_id", "p_rel_ens", "p_abs", "model_from").to_dicts()
    evidence["daily_integrity"] = pl.scan_parquet(ROOT/"cache/daily_sim.parquet").select(
        pl.len().alias("rows"), pl.col("trade_date").min().alias("first_day"),
        pl.col("trade_date").max().alias("last_day"),
        pl.struct("instrument_id", "trade_date").is_duplicated().sum().alias("duplicates"),
        ((pl.col("high")<pl.max_horizontal("open", "close", "low"))|
         (pl.col("low")>pl.min_horizontal("open", "close", "high"))).sum().alias("invalid_ohlc"),
        (pl.col("adjustment_factor")<=0).sum().alias("nonpositive_factors"),
        pl.col("adjustment_factor").is_null().sum().alias("null_factors"),
        ((pl.col("trade_date")>=pl.date(2020,1,1))&pl.col("down_limit").is_null()).sum().alias("missing_down_limits_2020plus")
    ).collect().to_dicts()[0]
    evidence["fee_and_live_note"] = "Historical source, model provenance and append-only enforcement are not supplied by cache validation."
    (OUT / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    emit("finished", {"output": str(OUT / "evidence.json"), "fees": evidence["fees"], "live": evidence["live"]})


if __name__ == "__main__":
    main()
