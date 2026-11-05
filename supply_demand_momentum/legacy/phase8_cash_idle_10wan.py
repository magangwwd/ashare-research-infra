import os
# -*- coding: utf-8 -*-
"""
场景测试：只做股票战术交易(score>=9)，不持有ETF被动腿，没触发信号时多余的钱就是现金(0%收益)。
资金规模10万元，叠加阶段6的平方根冲击成本模型(换腿摩擦在这个场景下不需要了,因为不再有ETF可切换)。
跟"ETF被动腿+现金等权化"版本的10万元结果做对比。
"""
import pickle
import numpy as np
import pandas as pd

t = pd.read_csv("trades2.csv", encoding="utf-8-sig", parse_dates=["entry_date", "exit_date"])
t9 = t[t["score"] >= 9].copy()

SCRATCH_CACHE = os.environ["SDM_LEGACY_CACHE"]
with open(SCRATCH_CACHE, "rb") as f:
    obj = pickle.load(f)
df_all = obj["df"]


def avg_turnover(row):
    mask = (df_all["ts_code"] == row["ts_code"]) & \
           (df_all["trade_date"] >= row["entry_date"]) & (df_all["trade_date"] <= row["exit_date"])
    vals = df_all.loc[mask, "amount"] * 1000.0
    return vals.mean() if len(vals) else np.nan


print("计算冲击成本所需的真实成交额(1-2分钟)...")
t9["avg_turnover_cny"] = t9.apply(avg_turnover, axis=1)
t9 = t9.dropna(subset=["avg_turnover_cny"])

SLOTS = 10
CAPITAL = 100_000
IMPACT_K = 0.15
COMMISSION_FLOOR = 5.0  # 单笔佣金最低5元

position_size = CAPITAL / SLOTS
impact = IMPACT_K * np.sqrt(position_size / t9["avg_turnover_cny"])
impact = impact.clip(upper=0.15)

# 最低佣金门槛的额外成本(双边各5元封底,原模型按比例佣金算的部分已经在net_ret里,这里补差)
modeled_commission_oneway = position_size * 0.00025
extra_commission_oneway = max(0, COMMISSION_FLOOR - modeled_commission_oneway)
extra_commission_roundtrip_pct = 2 * extra_commission_oneway / position_size

t9["net_ret_cashidle"] = t9["net_ret"] - impact - extra_commission_roundtrip_pct
t9["daily_ret"] = (1 + t9["net_ret_cashidle"]) ** (1 / t9["hold_days"].clip(lower=1)) - 1

date_range = pd.bdate_range(t9["entry_date"].min(), t9["exit_date"].max())
equity = [1.0]
for d in date_range:
    active = t9[(t9["entry_date"] <= d) & (t9["exit_date"] >= d)]
    n_active = len(active)
    signal_exposure = min(n_active, SLOTS) / SLOTS  # 没触发信号的槛位=现金,收益按0算
    active_ret = (active["daily_ret"] * (signal_exposure / n_active)).sum() if n_active > 0 else 0.0
    day_ret = active_ret  # 没有被动腿,空闲槛位直接是0
    equity.append(equity[-1] * (1 + day_ret))
equity = pd.Series(equity[1:], index=date_range)

ret_total = equity.iloc[-1] / equity.iloc[0] - 1
n_years = (equity.index[-1] - equity.index[0]).days / 365.25
cagr = (1 + ret_total) ** (1 / n_years) - 1
dd = (equity / equity.cummax() - 1).min()
daily_r = equity.pct_change().dropna()
sharpe = daily_r.mean() / daily_r.std() * np.sqrt(252) if daily_r.std() > 0 else np.nan

avg_exposure = np.mean([min(len(t9[(t9["entry_date"] <= d) & (t9["exit_date"] >= d)]), SLOTS) / SLOTS
                         for d in date_range])

print("\n==== 场景: 只做股票战术交易,不持有ETF,空闲资金=现金(0%), 资金规模10万元 ====")
print("总收益(12年): %.1f%%  年化(近似): %.2f%%  最大回撤: %.1f%%  夏普(近似): %.2f" % (
    ret_total * 100, cagr * 100, dd * 100, sharpe))
print("平均实际持仓敞口占比(其余时间是空闲现金): %.1f%%" % (avg_exposure * 100))

print("\n==== 对照组 ====")
print("ETF被动腿+现金等权化版本(10万元,阶段6已测): 总收益150.4%  最大回撤-27.0%")
print("最初'无任何资金利用率修复'的空仓版本(但那版是score>=8全部信号,非score>=9,仅供参考数量级): 总收益18.4%")
