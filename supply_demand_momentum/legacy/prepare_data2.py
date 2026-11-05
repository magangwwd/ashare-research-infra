import os
# -*- coding: utf-8 -*-
"""使用 project/down_data 的 gold 层数据（1990-2026 全历史，已预先标注 MAIN_BOARD + 点时ST）
重新构建一份覆盖更长周期（2015-01 ~ 最新）的主板股票池缓存，用于验证手册策略在多轮牛熊周期下的表现。"""
import glob
import pickle
import time

import numpy as np
import pandas as pd

ROOT = os.environ["ASTOCK_MARKET_DATA_ROOT"]
START_YEAR = 2015
CACHE = os.environ["SDM_LEGACY_CACHE"]


def load_partitioned(dataset, start_year):
    paths = []
    for p in sorted(glob.glob(f"{ROOT}\\{dataset}\\year=*\\month=*\\data.parquet")):
        yr = int(p.split("year=")[1][:4])
        if yr >= start_year:
            paths.append(p)
    dfs = [pd.read_parquet(p) for p in paths]
    return pd.concat(dfs, ignore_index=True)


def main():
    t0 = time.time()
    print("loading daily_prices ...")
    prices = load_partitioned("daily_prices", START_YEAR)
    prices["trade_date"] = pd.to_datetime(prices["trade_date"])
    prices = prices.drop_duplicates(["instrument_id", "trade_date"])
    print("rows:", len(prices), "elapsed:", round(time.time() - t0, 1))

    print("loading adjustment_factors ...")
    adj = load_partitioned("adjustment_factors", START_YEAR)
    adj["trade_date"] = pd.to_datetime(adj["trade_date"])
    adj = adj.drop_duplicates(["instrument_id", "trade_date"])
    print("rows:", len(adj), "elapsed:", round(time.time() - t0, 1))

    print("loading instruments / name_history / trading_calendar ...")
    instruments = pd.read_parquet(f"{ROOT}\\instruments.parquet")
    instruments["list_date"] = pd.to_datetime(instruments["list_date"], errors="coerce")
    instruments["delist_date"] = pd.to_datetime(instruments["delist_date"], errors="coerce")

    name_hist = pd.read_parquet(f"{ROOT}\\name_history.parquet")
    name_hist["effective_date"] = pd.to_datetime(name_hist["effective_date"], errors="coerce")
    name_hist = name_hist.dropna(subset=["effective_date"]).sort_values(["instrument_id", "effective_date"])

    cal = pd.read_parquet(f"{ROOT}\\trading_calendar.parquet")
    cal = cal[cal["exchange"] == "SSE"].copy()
    cal["calendar_date"] = pd.to_datetime(cal["calendar_date"])
    trading_days = pd.DatetimeIndex(sorted(cal.loc[cal["is_open"], "calendar_date"].unique()))
    trading_days = trading_days[trading_days >= prices["trade_date"].min()]

    print("merging adj_factor into prices ...")
    df = prices.merge(adj[["instrument_id", "trade_date", "adjustment_factor"]],
                       on=["instrument_id", "trade_date"], how="left")
    df = df.sort_values(["instrument_id", "trade_date"]).reset_index(drop=True)
    last_factor = df.groupby("instrument_id")["adjustment_factor"].transform("last")
    for col in ["open", "high", "low", "close", "previous_close"]:
        df[f"{col}_qfq"] = df[col].astype(float) * df["adjustment_factor"] / last_factor

    df = df.rename(columns={"volume_shares": "vol", "turnover_cny": "amount", "return_pct": "pct_chg"})
    df["vol"] = df["vol"].astype(float)
    df["amount"] = df["amount"].astype(float) / 1000.0  # 转为"千元"口径，与原脚本阈值一致
    df["pct_chg"] = df["pct_chg"].astype(float)

    print("attaching main-board / list_date / ST ...")
    df = df.merge(
        instruments[["instrument_id", "board", "exchange", "list_date", "delist_date"]],
        on="instrument_id", how="left",
    )
    df["is_main_board"] = df["board"] == "MAIN_BOARD"

    merged = pd.merge_asof(
        df[["instrument_id", "trade_date"]].sort_values("trade_date"),
        name_hist[["instrument_id", "effective_date", "is_st"]].sort_values("effective_date"),
        left_on="trade_date", right_on="effective_date", by="instrument_id", direction="backward",
    )
    df = df.sort_values("trade_date").reset_index(drop=True)
    merged = merged.reset_index(drop=True)
    df["is_st"] = merged["is_st"].fillna(False).astype(bool)

    df["ts_code"] = df["vendor_symbol"]

    limit_pct = np.where(df["is_st"], 0.05, 0.10)
    df["limit_up_flag"] = df["pct_chg"] >= (limit_pct * 100 - 0.6)
    df["limit_down_flag"] = df["pct_chg"] <= -(limit_pct * 100 - 0.6)

    listed_calendar_days = (df["trade_date"] - df["list_date"]).dt.days
    df["listed_days_calendar"] = listed_calendar_days

    df = df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)

    with open(CACHE, "wb") as f:
        pickle.dump({"df": df, "trading_days": trading_days}, f, protocol=4)
    print("done. rows=", len(df), "n_stocks=", df["ts_code"].nunique(),
          "date_range=", df["trade_date"].min(), df["trade_date"].max(),
          "elapsed=", round(time.time() - t0, 1), "s")


if __name__ == "__main__":
    main()
