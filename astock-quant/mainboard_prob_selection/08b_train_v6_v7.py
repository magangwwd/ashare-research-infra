"""Step 8b: extra pre-specified variants, same quarterly walk-forward as step 2 (full pool).

V6 pred_ex   : LightGBM regression on weekly excess return (ret_close - pool median),
               target clipped to +-15%  -> "rank by predicted gain"
V7 p_rel_ens : average of p_rel from step 2 (seed S) and two new seeds (S+1, S+2)
Gate for both stays p_abs from step 2.
"""
from __future__ import annotations

import datetime as dt
import os
import time

import lightgbm as lgb
import numpy as np
import polars as pl

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
TRAIN_FROM = dt.date(2015, 1, 1)
TEST_FROM = dt.date(2020, 1, 1)
SEED = 20261107
BASE = dict(learning_rate=0.03, num_leaves=31, min_child_samples=2000, colsample_bytree=0.7,
            subsample=0.7, subsample_freq=1, reg_lambda=10.0, n_estimators=300, n_jobs=8, verbose=-1)


def main():
    t0 = time.time()
    panel = pl.read_parquet(os.path.join(CACHE, "weekly_panel.parquet")).with_columns(
        (pl.col("ret_close") - pl.col("med_close")).clip(-0.15, 0.15).alias("y_ex"))
    feats = [c for c in panel.columns if c.startswith(("rk_", "mkt_"))]
    weeks = panel["signal_date"].unique().sort()
    q = weeks.filter(weeks >= TEST_FROM).to_frame().with_columns(
        (pl.col("signal_date").dt.year() * 10 + pl.col("signal_date").dt.quarter()).alias("q"))
    refits = q.group_by("q").agg(pl.col("signal_date").min().alias("start"),
                                 pl.col("signal_date").max().alias("end")).sort("q")
    base = pl.read_parquet(os.path.join(CACHE, "predictions.parquet"),
                           columns=["signal_date", "instrument_id", "p_rel"])
    out = []
    for row in refits.iter_rows(named=True):
        tr = panel.filter((pl.col("signal_date") >= TRAIN_FROM) & (pl.col("exit_date") < row["start"]) &
                          pl.col("buyable") & pl.col("y_rel").is_not_null())
        te = panel.filter((pl.col("signal_date") >= row["start"]) & (pl.col("signal_date") <= row["end"]))
        X = tr.select(feats).to_numpy().astype(np.float32)
        Xt = te.select(feats).to_numpy().astype(np.float32)
        reg = lgb.LGBMRegressor(objective="regression", random_state=SEED, **BASE).fit(X, tr["y_ex"].to_numpy())
        ens = [lgb.LGBMClassifier(objective="binary", random_state=SEED + k, **BASE)
               .fit(X, tr["y_rel"].to_numpy()).predict_proba(Xt)[:, 1] for k in (1, 2)]
        out.append(te.select("signal_date", "instrument_id").with_columns(
            pl.Series("pred_ex", reg.predict(Xt)), pl.Series("p_rel_s1", ens[0]), pl.Series("p_rel_s2", ens[1])))
        print(f"refit {row['q']}: train={tr.height:,} [{time.time()-t0:.0f}s]", flush=True)
    pred = pl.concat(out).join(base, on=["signal_date", "instrument_id"], how="left").with_columns(
        ((pl.col("p_rel") + pl.col("p_rel_s1") + pl.col("p_rel_s2")) / 3).alias("p_rel_ens")).drop("p_rel")
    pred.write_parquet(os.path.join(CACHE, "predictions_v67.parquet"))
    print(f"done in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
