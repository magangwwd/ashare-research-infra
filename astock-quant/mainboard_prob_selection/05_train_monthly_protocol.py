"""Step 5: protocol-aligned walk-forward training (matches the ChatGPT protocol).

Monthly refit, expanding window starting 2017-01 (initial training 2017-2019),
out-of-sample 2020-01 .. latest week. Only labels settled before the first signal of
the month are used. Four models per refit:

  M0 p_m0  : logistic regression, sparse stock-identity one-hot + rank features,
             absolute label (week up / down)  -- replica of the ChatGPT design
  M1 p_rel / p_abs : LightGBM relative (beat median) + absolute (gate), all features
  M2 p_rel_nomkt   : LightGBM relative, stock features only (no market-state features)
  M3 p_lr_rel      : logistic regression, rank features only, relative label
"""
from __future__ import annotations

import datetime as dt
import os
import time

import lightgbm as lgb
import numpy as np
import polars as pl
import scipy.sparse as sp
from sklearn.linear_model import LogisticRegression

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
TRAIN_FROM = dt.date(2017, 1, 1)
TEST_FROM = dt.date(2020, 1, 1)
SEED = 20261107
LGB = dict(objective="binary", learning_rate=0.05, num_leaves=31, min_child_samples=2000,
           colsample_bytree=0.7, subsample=0.7, subsample_freq=1, reg_lambda=10.0,
           n_estimators=200, n_jobs=8, random_state=SEED, verbose=-1)


def main() -> None:
    t0 = time.time()
    panel = pl.read_parquet(os.path.join(CACHE, "weekly_panel.parquet")).filter(
        pl.col("signal_date") >= TRAIN_FROM)
    rk = [c for c in panel.columns if c.startswith("rk_")]
    mk = [c for c in panel.columns if c.startswith("mkt_")]
    ids = panel["instrument_id"].unique().sort().to_list()
    id_ix = {s: i for i, s in enumerate(ids)}
    panel = panel.with_columns(pl.col("instrument_id").replace_strict(id_ix).alias("id_ix"))

    weeks = panel["signal_date"].unique().sort()
    test = weeks.filter(weeks >= TEST_FROM).to_frame().with_columns(
        (pl.col("signal_date").dt.year() * 100 + pl.col("signal_date").dt.month()).alias("m"))
    months = test.group_by("m").agg(pl.col("signal_date").min().alias("start"),
                                    pl.col("signal_date").max().alias("end")).sort("m")

    def design_sparse(d: pl.DataFrame):
        Xr = np.nan_to_num(d.select(rk).to_numpy().astype(np.float32), nan=0.5)
        oh = sp.csr_matrix((np.ones(d.height, np.float32),
                            (np.arange(d.height), d["id_ix"].to_numpy())),
                           shape=(d.height, len(ids)))
        return sp.hstack([sp.csr_matrix(Xr), oh], format="csr"), Xr

    out = []
    for row in months.iter_rows(named=True):
        tr = panel.filter((pl.col("exit_date") < row["start"]) & pl.col("buyable") &
                          pl.col("y_rel").is_not_null())
        te = panel.filter((pl.col("signal_date") >= row["start"]) &
                          (pl.col("signal_date") <= row["end"]))
        y_rel, y_abs = tr["y_rel"].to_numpy(), tr["y_abs"].to_numpy()
        Xa, Xta = (tr.select(rk + mk).to_numpy().astype(np.float32),
                   te.select(rk + mk).to_numpy().astype(np.float32))
        Xs, Xts = tr.select(rk).to_numpy().astype(np.float32), te.select(rk).to_numpy().astype(np.float32)
        m_rel = lgb.LGBMClassifier(**LGB).fit(Xa, y_rel)
        m_abs = lgb.LGBMClassifier(**LGB).fit(Xa, y_abs)
        m_nom = lgb.LGBMClassifier(**LGB).fit(Xs, y_rel)
        S, Xr = design_sparse(tr)
        St, Xrt = design_sparse(te)
        m0 = LogisticRegression(C=1.0, max_iter=200, solver="lbfgs").fit(S, y_abs)
        m3 = LogisticRegression(C=1.0, max_iter=200, solver="lbfgs").fit(Xr, y_rel)
        out.append(te.select("signal_date", "instrument_id").with_columns(
            pl.Series("p_rel", m_rel.predict_proba(Xta)[:, 1]),
            pl.Series("p_abs", m_abs.predict_proba(Xta)[:, 1]),
            pl.Series("p_rel_nomkt", m_nom.predict_proba(Xts)[:, 1]),
            pl.Series("p_m0", m0.predict_proba(St)[:, 1]),
            pl.Series("p_lr_rel", m3.predict_proba(Xrt)[:, 1])))
        print(f"{row['m']}: train={tr.height:,} test={te.height:,} [{time.time()-t0:.0f}s]",
              flush=True)
    pl.concat(out).write_parquet(os.path.join(CACHE, "predictions_monthly.parquet"))
    print(f"done in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
