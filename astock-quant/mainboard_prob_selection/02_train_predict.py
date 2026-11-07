"""Step 2: walk-forward training and out-of-sample prediction.

Quarterly retrain, expanding window from 2015-01. A training row is used only if its
label is settled (exit_date) strictly before the first signal date it will predict.
Two LightGBM models per refit:
  p_rel: P(stock beats the cross-sectional median this week)  -> ranking
  p_abs: P(stock goes up this week)                           -> cash / invest gate
Plus a logistic-regression p_rel on the same features as a simpler baseline.
"""
from __future__ import annotations

import os
import time

import lightgbm as lgb
import numpy as np
import polars as pl
from sklearn.linear_model import LogisticRegression

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
TRAIN_FROM = pl.date(2015, 1, 1)
TEST_FROM = "2020-01-01"
SEED = 20261107

LGB_PARAMS = dict(objective="binary", learning_rate=0.03, num_leaves=31,
                  min_child_samples=2000, colsample_bytree=0.7, subsample=0.7,
                  subsample_freq=1, reg_lambda=10.0, n_estimators=300,
                  n_jobs=8, random_state=SEED, verbose=-1)


def main() -> None:
    t0 = time.time()
    panel = pl.read_parquet(os.path.join(CACHE, "weekly_panel.parquet"))
    feats = [c for c in panel.columns if c.startswith(("rk_", "mkt_"))]
    print(f"{len(feats)} features")

    weeks = panel.select("signal_date").unique().sort("signal_date")["signal_date"]
    import datetime as _dt
    test_weeks = weeks.filter(weeks >= _dt.date.fromisoformat(TEST_FROM))
    # quarterly refit points
    q = test_weeks.to_frame().with_columns(
        (pl.col("signal_date").dt.year() * 10 + pl.col("signal_date").dt.quarter()).alias("q"))
    refits = q.group_by("q").agg(pl.col("signal_date").min().alias("start"),
                                 pl.col("signal_date").max().alias("end")).sort("q")

    out = []
    imp = np.zeros(len(feats))
    for row in refits.iter_rows(named=True):
        tr = panel.filter((pl.col("signal_date") >= TRAIN_FROM) &
                          (pl.col("exit_date") < row["start"]) &
                          pl.col("buyable") & pl.col("y_rel").is_not_null())
        te = panel.filter((pl.col("signal_date") >= row["start"]) &
                          (pl.col("signal_date") <= row["end"]))
        X = tr.select(feats).to_numpy().astype(np.float32)
        Xt = te.select(feats).to_numpy().astype(np.float32)
        m_rel = lgb.LGBMClassifier(**LGB_PARAMS).fit(X, tr["y_rel"].to_numpy())
        m_abs = lgb.LGBMClassifier(**LGB_PARAMS).fit(X, tr["y_abs"].to_numpy())
        imp += m_rel.booster_.feature_importance("gain")
        # logistic baseline (rank features only, NaN -> 0.5)
        rk = [i for i, c in enumerate(feats) if c.startswith("rk_")]
        Xl = np.nan_to_num(X[:, rk], nan=0.5)
        Xlt = np.nan_to_num(Xt[:, rk], nan=0.5)
        m_lr = LogisticRegression(C=1.0, max_iter=300).fit(Xl, tr["y_rel"].to_numpy())
        out.append(te.select("signal_date", "instrument_id").with_columns(
            pl.Series("p_rel", m_rel.predict_proba(Xt)[:, 1]),
            pl.Series("p_abs", m_abs.predict_proba(Xt)[:, 1]),
            pl.Series("p_lr", m_lr.predict_proba(Xlt)[:, 1]),
            pl.lit(row["start"]).alias("model_from")))
        print(f"refit {row['q']}: train={tr.height:,} test={te.height:,} "
              f"[{time.time()-t0:.0f}s]", flush=True)

    pred = pl.concat(out)
    pred.write_parquet(os.path.join(CACHE, "predictions.parquet"))
    fi = (pl.DataFrame({"feature": feats, "gain": imp / imp.sum()})
            .sort("gain", descending=True))
    fi.write_csv(os.path.join(HERE, "results", "feature_importance.csv"))
    print(fi.head(15))
    print(f"done: {pred.height:,} predictions in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    main()
