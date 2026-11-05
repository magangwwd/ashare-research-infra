# -*- coding: utf-8 -*-
"""
A股主板供需动量量化策略 —— 回测引擎（长周期版，2015-01 ~ 2026-11）
基于 project/down_data 的 gold 层全历史主板数据（已点时标注 MAIN_BOARD + ST），
对流动性最好的300只主板非ST股票跑一遍手册中第3/4/5章定义的
基底识别 -> 形态分类 -> 六因子打分 -> T+1执行 的完整规则，覆盖2015股灾、
2018熊市、2020疫情、2021-22下跌、2024-26反弹等多轮牛熊周期，
输出逐笔交易记录与统计指标，用于验证策略是否具备正期望。
"""
import os
import pickle
import numpy as np
import pandas as pd

K_BASE = float(os.environ.get("K_BASE", 1.0))  # 基底窄幅阈值 = k_base * ATR14（支持环境变量覆盖，用于参数敏感性测试）
BUFFER_PCT = 0.003    # 止损/确认缓冲 = 0.3%
MAX_HOLD_DAYS = 40    # 最长持有交易日数（超时强制平仓，原手册未设此上限，为回测可终止性新增）
MAX_STUCK_DAYS = 5    # 一字跌停无法卖出的最长等待天数（对应手册9.4）
COMMISSION = 0.00025  # 单边佣金
STAMP_TAX = 0.0005    # 卖出印花税
RR_GATE = float(os.environ.get("RR_GATE", 3.0))  # 盈亏比硬性门槛（支持环境变量覆盖）

with open(os.environ["SDM_LEGACY_CACHE"], "rb") as f:
    obj = pickle.load(f)
df_all = obj["df"]
trading_days = obj["trading_days"]

with open("universe2_top300.pkl", "rb") as f:
    universe = pickle.load(f)

df_all = df_all[df_all["ts_code"].isin(universe)].copy()
df_all = df_all.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)


def find_zones(g):
    """对单只股票识别供给/需求区域，返回 zone 列表（按形成日期排序）。"""
    n = len(g)
    high = g["high_qfq"].values
    low = g["low_qfq"].values
    openp = g["open_qfq"].values
    close = g["close_qfq"].values
    vol = g["vol"].values
    dates = g["trade_date"].values

    rng = high - low
    tr = np.maximum(rng, np.maximum(np.abs(high - np.roll(close, 1)), np.abs(low - np.roll(close, 1))))
    tr[0] = rng[0]
    atr = pd.Series(tr).rolling(14, min_periods=14).mean().values
    body = np.abs(close - openp)
    is_narrow = rng <= K_BASE * np.nan_to_num(atr, nan=np.inf)
    is_small_body = body <= 0.5 * np.where(rng == 0, 1e-9, rng)
    narrow_flag = is_narrow & is_small_body
    narrow_flag[np.isnan(atr)] = False

    vol20 = pd.Series(vol).rolling(20, min_periods=20).mean().values

    zones = []
    i = 0
    while i < n:
        if not narrow_flag[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and narrow_flag[j + 1]:
            j += 1
        run_len = j - i + 1
        base_start = max(i, j - 5)  # 若连续窄幅 >6 根，只取最后6根作为基底
        base_end = j
        n_base = base_end - base_start + 1
        if n_base < 3:
            i = j + 1
            continue

        # leg_in: 基底前3根方向
        pre_s = max(0, base_start - 3)
        if base_start - pre_s < 2 or base_end + 3 >= n:
            i = j + 1
            continue
        leg_in_slope = np.polyfit(range(base_start - pre_s), close[pre_s:base_start], 1)[0] \
            if base_start - pre_s >= 2 else 0.0
        leg_out_bars = slice(base_end + 1, base_end + 4)
        leg_out_slope = np.polyfit(range(3), close[leg_out_bars], 1)[0]

        leg_in_up = leg_in_slope > 0
        leg_out_up = leg_out_slope > 0

        if leg_out_up:
            ztype = "demand"
            pattern = "RBR" if leg_in_up else "DBR"
            proximal = np.max(np.maximum(openp[base_start:base_end + 1], close[base_start:base_end + 1]))
            distal = np.min(low[base_start:base_end + 1])
        else:
            ztype = "supply"
            pattern = "DBD" if not leg_in_up else "RBD"
            proximal = np.min(np.minimum(openp[base_start:base_end + 1], close[base_start:base_end + 1]))
            distal = np.max(high[base_start:base_end + 1])

        # 脱离速度打分（基底后3根）
        body_ratio = np.mean(np.abs(close[leg_out_bars] - openp[leg_out_bars]) /
                              np.where(rng[leg_out_bars] == 0, 1e-9, rng[leg_out_bars]))
        v20 = vol20[base_end] if not np.isnan(vol20[base_end]) else np.nanmean(vol[:base_end + 1]) + 1e-9
        vol_ratio = np.mean(vol[leg_out_bars]) / (v20 + 1e-9)
        gap = False
        for k in range(base_end + 1, base_end + 4):
            if ztype == "demand" and low[k] > high[k - 1]:
                gap = True
            if ztype == "supply" and high[k] < low[k - 1]:
                gap = True
        if body_ratio >= 0.6 and vol_ratio >= 1.5 or gap:
            strength_score = 2
        elif body_ratio >= 0.4 and vol_ratio >= 1.0:
            strength_score = 1
        else:
            strength_score = 0

        zones.append(dict(
            ztype=ztype, pattern=pattern, proximal=proximal, distal=distal,
            n_base=n_base, strength_score=strength_score,
            formed_idx=base_end, formed_date=dates[base_end],
        ))
        i = j + 1
    return zones


def wma(series, n):
    weights = np.arange(1, n + 1)
    return series.rolling(n).apply(lambda x: np.dot(x, weights) / weights.sum(), raw=True)


def simulate_stock(g, zones):
    """对单只股票：遍历需求区，寻找首次/二次回踩事件生成信号，模拟T+1执行与止损止盈。"""
    g = g.reset_index(drop=True)
    dates = g["trade_date"].values
    close = g["close_qfq"].values
    openp = g["open_qfq"].values
    high = g["high_qfq"].values
    low = g["low_qfq"].values
    one_word_up = g["limit_up_flag"].values & (g["high"].values == g["low"].values)
    one_word_down = g["limit_down_flag"].values & (g["high"].values == g["low"].values)
    eligible = g["eligible"].values if "eligible" in g.columns else np.ones(len(g), dtype=bool)
    n = len(g)

    wma30 = wma(pd.Series(close), 30).values

    demand_zones = [z for z in zones if z["ztype"] == "demand"]
    supply_zones = [z for z in zones if z["ztype"] == "supply"]

    # 每个区域的"已触达次数"记录（按时间推进维护）
    for z in zones:
        z["touch_idx"] = []  # 记录发生回踩的 bar 索引

    trades = []

    for dz in demand_zones:
        formed_idx = dz["formed_idx"]
        in_zone_prev = False
        touches_before = 0
        idx = formed_idx + 1
        while idx < n:
            in_zone_now = dz["distal"] <= close[idx] <= dz["proximal"]
            if in_zone_now and not in_zone_prev:
                # ---- 发生一次回踩事件 ----
                retest_count_before = touches_before
                if retest_count_before == 0:
                    retest_score = 2
                elif retest_count_before == 1:
                    retest_score = 1
                else:
                    retest_score = 0

                entry_signal_idx = idx
                entry_price_plan = dz["proximal"]
                stop_price = dz["distal"] * (1 - BUFFER_PCT)
                risk = entry_price_plan - stop_price

                # 向左看：寻找最近且在该时点尚未被回踩过的相反（供给）区域
                target1 = None
                for sz in supply_zones:
                    if sz["formed_idx"] >= entry_signal_idx:
                        continue
                    if sz["proximal"] <= entry_price_plan:
                        continue
                    tested = any(t <= entry_signal_idx for t in sz["touch_idx"])
                    if tested:
                        continue
                    if target1 is None or sz["proximal"] < target1["proximal"]:
                        target1 = sz

                if target1 is None or risk <= 0:
                    rr = 0
                else:
                    rr = (target1["proximal"] - entry_price_plan) / risk

                if rr >= 3:
                    rr_score = 2
                elif rr >= 2:
                    rr_score = 1
                else:
                    rr_score = 0

                w = wma30[entry_signal_idx]
                w20 = wma30[entry_signal_idx - 20] if entry_signal_idx >= 20 and not np.isnan(wma30[entry_signal_idx - 20]) else np.nan
                if not np.isnan(w) and not np.isnan(w20):
                    slope_up = w > w20
                else:
                    slope_up = True  # 数据不足时不惩罚
                if slope_up:
                    big_picture_score = 2
                elif rr >= 2:
                    big_picture_score = 1
                else:
                    big_picture_score = 0

                time_score = 1  # n_base 已在构造时裁剪到 3-6，恒为1

                arrival_score = 1
                if target1 is not None:
                    for sz in supply_zones:
                        if formed_idx < sz["formed_idx"] < entry_signal_idx:
                            if entry_price_plan < sz["proximal"] <= target1["proximal"]:
                                arrival_score = 0
                                break

                total_score = (dz["strength_score"] + rr_score + big_picture_score +
                               retest_score + time_score + arrival_score)

                hard_gate_ok = (rr >= RR_GATE) and eligible[entry_signal_idx] and (not one_word_up[entry_signal_idx]) \
                    and (not one_word_down[entry_signal_idx])

                if total_score >= 8 and hard_gate_ok:
                    trade = try_execute(
                        g, entry_signal_idx, total_score, entry_price_plan, stop_price,
                        target1["proximal"] if target1 else None, dz["pattern"],
                        one_word_up, one_word_down, close, openp, high, low, dates, n
                    )
                    if trade:
                        trades.append(trade)

                touches_before += 1
                dz["touch_idx"].append(idx)
            in_zone_prev = in_zone_now
            idx += 1

    # 同样记录供给区的触达次数（用于其他需求区信号的 target "未测试" 判断，上面已用 touch_idx 动态追加，
    # 但供给区自身被触达的事件需要单独扫描登记）
    for sz in supply_zones:
        f_idx = sz["formed_idx"]
        in_zone_prev = False
        idx = f_idx + 1
        while idx < n:
            in_zone_now = sz["distal"] >= close[idx] >= sz["proximal"] if sz["proximal"] <= sz["distal"] else (sz["proximal"] >= close[idx] >= sz["distal"])
            if in_zone_now and not in_zone_prev:
                sz["touch_idx"].append(idx)
            in_zone_prev = in_zone_now
            idx += 1

    return trades


def try_execute(g, sig_idx, score, entry_plan, stop_price, target_price, pattern,
                 one_word_up, one_word_down, close, openp, high, low, dates, n):
    entry_idx = None
    if score >= 9:
        t1 = sig_idx + 1
        if t1 >= n or one_word_up[t1]:
            return None
        entry_idx = t1
        entry_price = openp[t1]
    else:  # score == 8, 确认进场
        confirm_level = entry_plan * (1 + BUFFER_PCT)
        found = False
        for k in range(sig_idx + 1, min(sig_idx + 4, n)):
            if low[k] <= stop_price:
                return None  # 机械化撤单：确认前先触及止损
            if close[k] >= confirm_level:
                if k + 1 >= n or one_word_up[k + 1]:
                    return None
                entry_idx = k + 1
                entry_price = openp[k + 1]
                found = True
                break
        if not found:
            return None

    if target_price is None:
        return None

    # T+1：最早可卖出日为 entry_idx+1
    idx = entry_idx + 1
    stuck = 0
    while idx < n:
        if one_word_down[idx]:
            stuck += 1
            if stuck >= MAX_STUCK_DAYS:
                exit_price = close[idx]
                return make_trade(entry_idx, entry_price, idx, exit_price, "强制止损(连续跌停)", score, pattern, dates)
            idx += 1
            continue
        stuck = 0
        if low[idx] <= stop_price:
            exit_price = openp[idx] if openp[idx] <= stop_price else stop_price
            return make_trade(entry_idx, entry_price, idx, exit_price, "止损", score, pattern, dates)
        if high[idx] >= target_price:
            exit_price = target_price
            return make_trade(entry_idx, entry_price, idx, exit_price, "止盈", score, pattern, dates)
        if idx - entry_idx >= MAX_HOLD_DAYS:
            exit_price = close[idx]
            return make_trade(entry_idx, entry_price, idx, exit_price, "超时平仓", score, pattern, dates)
        idx += 1
    exit_price = close[n - 1]
    return make_trade(entry_idx, entry_price, n - 1, exit_price, "数据结束平仓", score, pattern, dates)


def make_trade(entry_idx, entry_price, exit_idx, exit_price, reason, score, pattern, dates):
    gross_ret = exit_price / entry_price - 1
    net_ret = gross_ret - COMMISSION * 2 - STAMP_TAX
    return dict(
        entry_date=pd.Timestamp(dates[entry_idx]), exit_date=pd.Timestamp(dates[exit_idx]),
        entry_price=entry_price, exit_price=exit_price, hold_days=exit_idx - entry_idx,
        reason=reason, score=score, pattern=pattern, gross_ret=gross_ret, net_ret=net_ret,
    )


all_trades = []
for code, g in df_all.groupby("ts_code"):
    g = g.sort_values("trade_date").reset_index(drop=True)
    if len(g) < 80:
        continue
    zones = find_zones(g)
    trades = simulate_stock(g, zones)
    for t in trades:
        t["ts_code"] = code
    all_trades.extend(trades)

trades_df = pd.DataFrame(all_trades)
OUT_FILE = os.environ.get("OUT_FILE", "trades2.csv")
trades_df.to_csv(OUT_FILE, index=False, encoding="utf-8-sig")
print("n_trades:", len(trades_df))
if len(trades_df):
    print(trades_df[["score", "pattern", "reason"]].describe(include="all"))
