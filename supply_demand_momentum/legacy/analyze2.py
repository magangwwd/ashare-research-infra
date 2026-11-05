import os
# -*- coding: utf-8 -*-
import pandas as pd
import numpy as np

pd.set_option("display.unicode.east_asian_width", True)

t = pd.read_csv("trades2.csv", encoding="utf-8-sig", parse_dates=["entry_date", "exit_date"])
n = len(t)
win = (t["net_ret"] > 0).sum()
loss = (t["net_ret"] <= 0).sum()
win_rate = win / n
avg_win = t.loc[t["net_ret"] > 0, "net_ret"].mean()
avg_loss = t.loc[t["net_ret"] <= 0, "net_ret"].mean()
profit_factor = -t.loc[t["net_ret"] > 0, "net_ret"].sum() / t.loc[t["net_ret"] <= 0, "net_ret"].sum() if loss > 0 else np.inf
avg_ret = t["net_ret"].mean()
avg_hold = t["hold_days"].mean()
total_compound = (1 + t["net_ret"]).prod() - 1  # 假设逐笔串行复利(非真实组合,仅做参考)

print("==== 整体统计 (样本: 2015-01 ~ 2026-11, 流动性前300只主板非ST股票) ====")
print("交易笔数:", n)
print("胜率: %.1f%%" % (win_rate * 100))
print("平均单笔净收益: %.2f%%" % (avg_ret * 100))
print("平均盈利: %.2f%%  平均亏损: %.2f%%" % (avg_win * 100, avg_loss * 100))
print("盈亏比(profit factor): %.2f" % profit_factor)
print("平均持仓天数: %.1f" % avg_hold)
print("若逐笔串行复利(仅参考,非真实组合净值): %.1f%%" % (total_compound * 100))

print("\n==== 按出场原因 ====")
print(t.groupby("reason")["net_ret"].agg(["count", "mean", "sum"]))

print("\n==== 按评分分层 (8分 vs 9-10分) ====")
t["score_bucket"] = np.where(t["score"] >= 9, "9-10分(限价)", "8分(确认进场)")
print(t.groupby("score_bucket")["net_ret"].agg(["count", "mean", lambda s: (s > 0).mean()]))

print("\n==== 按形态 (DBR反转 vs RBR持续) ====")
print(t.groupby("pattern")["net_ret"].agg(["count", "mean", lambda s: (s > 0).mean()]))

print("\n==== 按年度(entry_date) ====")
t["year"] = t["entry_date"].dt.year
print(t.groupby("year")["net_ret"].agg(["count", "mean", "sum"]))

# ---- 简化组合净值曲线：固定N个并发槽位，等权，先进先出排队（容量外信号放弃） ----
SLOTS = 25
t_sorted = t.sort_values("entry_date").reset_index(drop=True)
events = []
for i, row in t_sorted.iterrows():
    events.append((row["entry_date"], "open", i, row))
    events.append((row["exit_date"], "close", i, row))
events.sort(key=lambda x: (x[0], 0 if x[1] == "close" else 1))

all_dates = pd.DatetimeIndex(sorted(set(t_sorted["entry_date"]).union(t_sorted["exit_date"])))
open_positions = {}
daily_nav = []
nav = 1.0
prev_date = None
pending_queue = []

# 用更直接的方法：按日期网格逐日推进，持仓按 slots 分配等权仓位
start = t_sorted["entry_date"].min()
end = t_sorted["exit_date"].max()
calendar = pd.DatetimeIndex(sorted(set(t_sorted["entry_date"]).union(t_sorted["exit_date"])))
# 为了得到更连续的日度净值，直接用所有交易区间内按比例分配日收益(近似法，持仓期内按1/SLOTS等权，若同时开仓数超过SLOTS则按比例缩放)

t_sorted["w"] = 1.0 / SLOTS
# 统计每日有多少笔交易在持仓中
intervals = t_sorted[["entry_date", "exit_date", "net_ret", "hold_days"]].copy()
intervals["daily_ret"] = (1 + intervals["net_ret"]) ** (1 / intervals["hold_days"].clip(lower=1)) - 1

date_range = pd.bdate_range(start, end)
daily_concurrent = pd.Series(0, index=date_range)
for _, row in intervals.iterrows():
    mask = (date_range >= row["entry_date"]) & (date_range <= row["exit_date"])
    daily_concurrent[mask] += 1

equity = [1.0]
for d in date_range:
    active = intervals[(intervals["entry_date"] <= d) & (intervals["exit_date"] >= d)]
    if len(active) == 0:
        equity.append(equity[-1])
        continue
    n_active = len(active)
    total_exposure = min(n_active, SLOTS) / SLOTS  # 持仓数<=槽位时按槽位比例投入,超过槽位时满仓(100%)
    per_trade_weight = total_exposure / n_active
    day_ret = (active["daily_ret"] * per_trade_weight).sum()
    equity.append(equity[-1] * (1 + day_ret))
equity = pd.Series(equity[1:], index=date_range)
equity.to_csv("equity_curve2.csv", encoding="utf-8-sig")

ret_total = equity.iloc[-1] / equity.iloc[0] - 1
n_years = (equity.index[-1] - equity.index[0]).days / 365.25
cagr = (1 + ret_total) ** (1 / n_years) - 1
roll_max = equity.cummax()
dd = equity / roll_max - 1
max_dd = dd.min()
daily_rets = equity.pct_change().dropna()
sharpe = daily_rets.mean() / daily_rets.std() * np.sqrt(252) if daily_rets.std() > 0 else np.nan

print("\n==== 简化组合净值曲线 (并发槽位=%d, 等权近似) ====" % SLOTS)
print("区间: ", equity.index[0].date(), "~", equity.index[-1].date())
print("总收益: %.2f%%" % (ret_total * 100))
print("年化收益(近似): %.2f%%" % (cagr * 100))
print("最大回撤: %.2f%%" % (max_dd * 100))
print("年化夏普(近似): %.2f" % sharpe)

# ---- 基准对比：同一股票池等权买入持有 ----
import pickle
with open(os.environ["SDM_LEGACY_CACHE"], "rb") as f:
    obj = pickle.load(f)
df_all = obj["df"]
with open("universe2_top300.pkl", "rb") as f:
    universe = pickle.load(f)
bench = df_all[df_all["ts_code"].isin(universe)].pivot_table(index="trade_date", columns="ts_code", values="close_qfq")
bench = bench.reindex(pd.bdate_range(bench.index.min(), bench.index.max())).ffill()
bench_ret = bench.pct_change().mean(axis=1).fillna(0)
bench_equity = (1 + bench_ret).cumprod()
bench_equity = bench_equity.reindex(equity.index).ffill()
bench_total = bench_equity.iloc[-1] / bench_equity.iloc[0] - 1
bench_dd = (bench_equity / bench_equity.cummax() - 1).min()
print("\n==== 基准: 同一200只股票池等权买入持有 ====")
print("总收益: %.2f%%" % (bench_total * 100))
print("最大回撤: %.2f%%" % (bench_dd * 100))
