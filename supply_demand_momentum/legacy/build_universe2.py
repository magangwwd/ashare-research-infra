import os
# -*- coding: utf-8 -*-
import pickle
import pandas as pd
import numpy as np

CACHE = os.environ["SDM_LEGACY_CACHE"]
with open(CACHE, "rb") as f:
    obj = pickle.load(f)
df = obj["df"]
trading_days = obj["trading_days"]

print("total rows", len(df), "n stocks", df["ts_code"].nunique())
print("date range", df["trade_date"].min(), df["trade_date"].max())
print("main board rows frac:", df["is_main_board"].mean())
print("is_st frac:", df["is_st"].mean())

df = df.sort_values(["ts_code", "trade_date"])
df["amt_ma20"] = df.groupby("ts_code")["amount"].transform(lambda s: s.rolling(20, min_periods=20).mean())

LIQ_THRESH = 50000  # 千元 = 5000万元
elig = (
    df["is_main_board"]
    & (~df["is_st"])
    & (df["listed_days_calendar"] >= 85)
    & (df["amt_ma20"] >= LIQ_THRESH)
)
df["eligible"] = elig
print("eligible-row frac:", elig.mean())

n_days = len(trading_days)
coverage = df[df["eligible"]].groupby("ts_code").size() / n_days
candidates = coverage[coverage >= 0.5].index
print("candidates with >=50% coverage:", len(candidates))

avg_amt = df[df["ts_code"].isin(candidates)].groupby("ts_code")["amount"].mean().sort_values(ascending=False)
topN = avg_amt.head(300).index.tolist()
with open("universe2_top300.pkl", "wb") as f:
    pickle.dump(topN, f)
print("saved top300 universe, n=", len(topN))

with open(CACHE, "wb") as f:
    pickle.dump({"df": df, "trading_days": trading_days}, f, protocol=4)
print("re-saved cache with eligible/amt_ma20 columns")
