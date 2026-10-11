"""V1 锁升观察者：出场规则(第3项) 与 v0.5 底部过滤(第4项) 的历史回测。

用法（在 stock_state_strategy 根目录）:
    python v1_exit_filter_backtest.py
可选:
    --state-cache PATH   指定 daily_state_cache.parquet（默认自动在 output/ 与 artifacts/ 下找最新的）
    --output-dir DIR     默认 output/v1_1_backtest

信号: 当日 transition == lock_to_markup 且 defined_state == markup 且 pool_status == A_STRATEGY_CANDIDATE
执行: T+1 开盘买入（一字涨停/停牌/高开>3% 不买），成本 买0.13% 卖0.18%
止损: clip(1.5*ATR20/收盘, 4%, 8%)；买入当天触及 -> 次一可卖开盘；之后跳空 -> 开盘；盘中 -> 止损价
时间: 买入后第 20 个交易日收盘（T+21 收盘）
状态出场: 当日收盘判定，次一可卖开盘执行
    orig          : markup_to_distribution（V1 原规则）
    v11_uncertain : orig + defined_state 变为 uncertain
    v11_removed   : orig + pool_status 变为 REMOVED
    v11_both      : orig + uncertain 或 REMOVED（第3项建议规则）
    v11_both_2d   : orig + 连续2日 uncertain 或 REMOVED（抗噪声版本）
    v11_leave     : 只要 defined_state 不再是 markup 就走
    stop_time     : 只有止损+时间（无任何状态出场，对照）
过滤(第4项): 与 v0.5 同口径的日线合成分(ret_1d/5d/20d, ma20偏离, 20日波动, 量比, 20日成交额，全部负向)，
    在流动性池(每日剔除20日均成交额最低30%)内截面排名，剔除底部 10% / 20% 的信号。
统计: 全部信号独立交易(per_signal) + 单持仓顺序模拟(single_position，按 estimated_pl_ratio、net_r_percentile 排序)；
    开发期 2023-2024 / 诊断期 2025-2026 分开报告。
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from ownership_pipeline import ROOT, load_config, resolve
from state_definition_pool import load_market_history

BUY_COST, SELL_COST = 0.0013, 0.0018
ATR_WINDOW, ATR_MULT, MIN_STOP, MAX_STOP = 20, 1.5, 0.04, 0.08
HOLD_SESSIONS = 20
MAX_ENTRY_GAP = 0.03
EPS = 1e-4
LIQ_DROP = 0.30
SIGNAL_START = pd.Timestamp("2023-01-01")
PERIODS = {"dev_2023_2024": (2023, 2024), "diag_2025_2026": (2025, 2026)}
EXIT_RULES = ["orig", "v11_uncertain", "v11_removed", "v11_both", "v11_both_2d", "v11_leave", "stop_time"]
FILTERS = {"none": None, "drop_bottom10": 0.10, "drop_bottom20": 0.20}
COMPOSITE_FEATURES = ["ret_1d", "ret_5d", "ret_20d", "ma20_gap", "vol_20d", "amt_ratio", "log_amount_20d"]


def find_state_cache(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    found = [p for base in ("output", "artifacts") for p in (ROOT / base).rglob("daily_state_cache.parquet")]
    if not found:
        raise FileNotFoundError("找不到 daily_state_cache.parquet，请用 --state-cache 指定")
    latest = {p: pd.read_parquet(p, columns=["trade_date"])["trade_date"].max() for p in found}
    for p, d in latest.items():
        print(f"  state cache {p} -> 截止 {pd.Timestamp(d).date()}")
    return max(latest, key=lambda p: (pd.Timestamp(latest[p]), p.stat().st_mtime))


def add_price_features(panel: pd.DataFrame) -> pd.DataFrame:
    """前复权无关的比例特征：ATR20%、v0.5 日线合成分所需特征。"""
    p = panel.sort_values(["instrument_id", "trade_idx"], kind="stable").reset_index(drop=True)
    f = p["adjustment_factor"].astype(float)
    for c in ("open", "high", "low", "close"):
        p[f"a_{c}"] = p[c].astype(float) * f
    g = p.groupby("instrument_id", sort=False)
    prev_close = g["a_close"].shift(1)
    tr = pd.concat([p["a_high"] - p["a_low"], (p["a_high"] - prev_close).abs(), (p["a_low"] - prev_close).abs()], axis=1).max(axis=1)
    p["atr20_pct"] = tr.groupby(p["instrument_id"]).transform(lambda s: s.rolling(ATR_WINDOW, min_periods=ATR_WINDOW).mean()) / p["a_close"]
    c = p["a_close"]
    p["ret_1d"] = c / prev_close - 1
    p["ret_5d"] = c / g["a_close"].shift(5) - 1
    p["ret_20d"] = c / g["a_close"].shift(20) - 1
    p["ma20_gap"] = c / g["a_close"].transform(lambda s: s.rolling(20, min_periods=20).mean()) - 1
    p["vol_20d"] = p["ret_1d"].groupby(p["instrument_id"]).transform(lambda s: s.rolling(20, min_periods=15).std())
    amt = p["turnover_cny"].astype(float)
    amt20 = amt.groupby(p["instrument_id"]).transform(lambda s: s.rolling(20, min_periods=18).mean())
    p["amt_ratio"] = amt / amt20
    p["amount_20d"] = amt20
    p["log_amount_20d"] = np.log(amt20.where(amt20 > 0))
    p = p.replace([np.inf, -np.inf], np.nan)
    # 流动性池内截面合成分（越低越差），与 v0.5 口径一致：各特征百分位取反后等权平均
    p["liq_rank"] = p.groupby("trade_date")["amount_20d"].rank(pct=True)
    pool = p["liq_rank"] > LIQ_DROP
    ranks = p.loc[pool, ["trade_date"] + COMPOSITE_FEATURES].groupby("trade_date").rank(pct=True)
    signed = sum(1 - ranks[k] for k in COMPOSITE_FEATURES)
    n = sum(ranks[k].notna().astype(int) for k in COMPOSITE_FEATURES)
    p["composite"] = np.nan
    p.loc[pool, "composite"] = (signed / n.replace(0, np.nan)).to_numpy()
    p["composite_pct"] = p.loc[pool].groupby("trade_date")["composite"].rank(pct=True)
    return p


def build_arrays(p: pd.DataFrame, states: pd.DataFrame) -> dict:
    s = states[["instrument_id", "trade_date", "defined_state", "pool_status", "transition", "confidence"]]
    p = p.merge(s, on=["instrument_id", "trade_date"], how="left", validate="one_to_one")
    arrays = {}
    for inst, g in p.groupby("instrument_id", sort=False):
        arrays[inst] = {
            "idx": g["trade_idx"].to_numpy(np.int64),
            "date": g["trade_date"].to_numpy(),
            "open": g["a_open"].to_numpy(float), "high": g["a_high"].to_numpy(float),
            "low": g["a_low"].to_numpy(float), "close": g["a_close"].to_numpy(float),
            "raw_open": g["open"].to_numpy(float), "raw_high": g["high"].to_numpy(float),
            "up": g["up_limit"].to_numpy(float), "down": g["down_limit"].to_numpy(float),
            "atr": g["atr20_pct"].to_numpy(float),
            "state": g["defined_state"].fillna("").to_numpy(object),
            "pool": g["pool_status"].fillna("").to_numpy(object),
            "trans": g["transition"].fillna("none").to_numpy(object),
        }
    return arrays, p


def _state_trigger(a: dict, d: int, rule: str, streak: int) -> tuple[bool, int]:
    """收盘时是否触发状态出场；返回 (触发, 新的 uncertain/REMOVED 连续天数)。"""
    st, pool, tr = a["state"][d], a["pool"][d], a["trans"][d]
    if st == "":  # 当日无状态数据：不做判断
        return False, streak
    dist = tr == "markup_to_distribution"
    weak = st == "uncertain" or pool == "REMOVED"
    streak = streak + 1 if weak else 0
    if rule == "stop_time":
        return False, streak
    if rule == "orig":
        return dist, streak
    if rule == "v11_uncertain":
        return dist or st == "uncertain", streak
    if rule == "v11_removed":
        return dist or pool == "REMOVED", streak
    if rule == "v11_both":
        return dist or weak, streak
    if rule == "v11_both_2d":
        return dist or streak >= 2, streak
    if rule == "v11_leave":
        return dist or st != "markup", streak
    raise ValueError(rule)


def simulate(a: dict, i: int, rule: str):
    """i = 信号日在该股序列中的位置。买不进返回 (None, 原因)。"""
    n = len(a["idx"])
    e = i + 1
    if e >= n or a["idx"][e] != a["idx"][i] + 1:
        return None, "entry_missing"
    if np.isfinite(a["up"][e]) and a["raw_open"][e] >= a["up"][e] * (1 - EPS):
        return None, "limit_up_open"
    if a["open"][e] / a["close"][i] - 1 > MAX_ENTRY_GAP:
        return None, "gap_over_3pct"
    atr = a["atr"][i]
    if not np.isfinite(atr):
        return None, "atr_missing"
    stop_pct = float(np.clip(ATR_MULT * atr, MIN_STOP, MAX_STOP))
    entry = a["open"][e]
    stop = entry * (1 - stop_pct)
    time_idx = a["idx"][e] + HOLD_SESSIONS
    pending = None
    streak = 0

    def done(j, px, reason):
        net = px * (1 - SELL_COST) / (entry * (1 + BUY_COST)) - 1
        return {"entry_date": a["date"][e], "exit_date": a["date"][j], "exit_reason": reason,
                "hold_sessions": int(a["idx"][j] - a["idx"][e]), "stop_pct": stop_pct,
                "net_return": net, "net_r": net / stop_pct}, None

    if a["low"][e] <= stop:
        pending = "stop_entry_day"
    trig, streak = _state_trigger(a, e, rule, streak)
    if pending is None and trig:
        pending = "state"
    for j in range(e + 1, n):
        sellable = not (np.isfinite(a["down"][j]) and a["raw_high"][j] <= a["down"][j] * (1 + EPS))
        if pending:
            if sellable:
                return done(j, a["open"][j], pending)
            continue
        if a["idx"][j] > time_idx:  # 计划平仓日停牌：之后第一个可卖开盘
            if sellable:
                return done(j, a["open"][j], "time_late")
            pending = "time_late"
            continue
        if a["low"][j] <= stop:
            if sellable:
                return done(j, min(stop, a["open"][j]), "stop")
            pending = "stop"
            continue
        if a["idx"][j] == time_idx:
            if sellable:
                return done(j, a["close"][j], "time")
            pending = "time"
            continue
        trig, streak = _state_trigger(a, j, rule, streak)
        if trig:
            pending = "state_" + rule
    return None, "open_at_data_end"


def stats(t: pd.DataFrame) -> dict:
    if t.empty:
        return {"trades": 0}
    r = t["net_r"].to_numpy(float)
    ret = t["net_return"].to_numpy(float)
    gl = -r[r < 0].sum()
    equity = np.cumprod(1 + ret)
    dd = float((equity / np.maximum.accumulate(equity) - 1).min())
    return {
        "trades": int(len(t)), "win_rate": float((ret > 0).mean()),
        "mean_net_return": float(ret.mean()), "mean_net_r": float(r.mean()),
        "net_r_t": float(r.mean() / r.std(ddof=1) * math.sqrt(len(r))) if len(r) > 1 and r.std(ddof=1) > 0 else math.nan,
        "profit_factor": float(r[r > 0].sum() / gl) if gl > 0 else math.inf,
        "compound_return_full_weight": float(equity[-1] - 1), "max_dd_full_weight": dd,
        "avg_hold_sessions": float(t["hold_sessions"].mean()),
        **{f"exit_{k}": float(v) for k, v in t["exit_reason"].str.replace(r"^state_.*", "state", regex=True).value_counts(normalize=True).items()},
    }


def single_position(trades: pd.DataFrame) -> pd.DataFrame:
    """每天只看排名第一的信号；空仓才买；买不进当天放弃（与 V1 defer=false 一致）。"""
    chosen, free_after = [], pd.Timestamp.min
    for d, g in trades.sort_values(["signal_date", "rank"]).groupby("signal_date", sort=True):
        top = g.iloc[0]
        if pd.isna(top["entry_date"]) or pd.Timestamp(top["entry_date"]) <= free_after:
            continue
        chosen.append(top)
        free_after = pd.Timestamp(top["exit_date"])
    return pd.DataFrame(chosen)


def summarize(trades: pd.DataFrame, label: str) -> list[dict]:
    rows = []
    for period, (y0, y1) in PERIODS.items():
        yrs = trades["signal_date"].dt.year
        rows.append({"mode": label, "period": period, **stats(trades[(yrs >= y0) & (yrs <= y1)])})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/ownership_state.json")
    ap.add_argument("--state-cache", default=None)
    ap.add_argument("--candidate-rows", default=None, help="用于单持仓排序（estimated_pl_ratio 等）；缺省自动查找")
    ap.add_argument("--output-dir", default="output/v1_1_backtest")
    args = ap.parse_args()
    config = load_config((ROOT / args.config).resolve())
    market_root = resolve(config["market_data_root"])
    out = (ROOT / args.output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)

    cache = find_state_cache(args.state_cache)
    print(f"使用 state cache: {cache}", flush=True)
    states = pd.read_parquet(cache, columns=["instrument_id", "trade_date", "defined_state", "pool_status", "transition", "confidence"])
    states["trade_date"] = pd.to_datetime(states["trade_date"])
    end = states["trade_date"].max()

    print("加载行情 ...", flush=True)
    panel, _, _ = load_market_history(market_root, pd.Timestamp("2022-06-01"), end)
    panel = add_price_features(panel)
    arrays, merged = build_arrays(panel, states)
    del panel

    sig = merged[
        merged["trade_date"].ge(SIGNAL_START)
        & merged["transition"].eq("lock_to_markup")
        & merged["defined_state"].eq("markup")
        & merged["pool_status"].eq("A_STRATEGY_CANDIDATE")
    ][["instrument_id", "trade_date", "composite", "composite_pct", "confidence"]].rename(columns={"trade_date": "signal_date"})
    missing_states = int(len(states[states["trade_date"].ge(SIGNAL_START) & states["transition"].eq("lock_to_markup")
                                    & states["defined_state"].eq("markup") & states["pool_status"].eq("A_STRATEGY_CANDIDATE")]) - len(sig))

    # 单持仓排序字段
    cand_path = Path(args.candidate_rows) if args.candidate_rows else next(
        iter(sorted((ROOT / "artifacts").rglob("candidate_rows.parquet"), key=lambda p: p.stat().st_mtime, reverse=True)), None)
    if cand_path is not None and cand_path.exists():
        cand = pd.read_parquet(cand_path, columns=["signal_date", "instrument_id", "estimated_pl_ratio", "net_r_percentile"])
        cand["signal_date"] = pd.to_datetime(cand["signal_date"])
        sig = sig.merge(cand.drop_duplicates(["signal_date", "instrument_id"]), on=["signal_date", "instrument_id"], how="left")
    else:
        sig["estimated_pl_ratio"] = np.nan
        sig["net_r_percentile"] = np.nan
    sig = sig.sort_values(["signal_date", "estimated_pl_ratio", "net_r_percentile", "confidence", "instrument_id"],
                          ascending=[True, False, False, False, True], na_position="last")
    sig["rank"] = sig.groupby("signal_date").cumcount()

    pos_lookup = {inst: pd.Series(np.arange(len(a["date"])), index=pd.DatetimeIndex(a["date"])) for inst, a in arrays.items()}
    all_trades, skipped = [], {}
    for rule in EXIT_RULES:
        for r in sig.itertuples(index=False):
            a = arrays[r.instrument_id]
            i = int(pos_lookup[r.instrument_id][r.signal_date])
            res, why = simulate(a, i, rule)
            row = {"rule": rule, "signal_date": r.signal_date, "instrument_id": r.instrument_id, "rank": r.rank,
                   "composite_pct": r.composite_pct, "estimated_pl_ratio": r.estimated_pl_ratio}
            if res is None:
                skipped[(rule, why)] = skipped.get((rule, why), 0) + 1
                row |= {"entry_date": pd.NaT, "skip_reason": why}
            else:
                row |= res
            all_trades.append(row)
    trades = pd.DataFrame(all_trades)
    trades.to_csv(out / "all_signal_trades.csv", index=False, encoding="utf-8-sig")

    rows, singles = [], []
    for rule in EXIT_RULES:
        for fname, cut in FILTERS.items():
            t = trades[trades["rule"].eq(rule)]
            if cut is not None:
                t = t[~(t["composite_pct"] <= cut)]  # 缺失合成分(不在流动性池)的信号保留
            filled = t.dropna(subset=["entry_date"])
            for x in summarize(filled, "per_signal"):
                rows.append({"rule": rule, "filter": fname, **x})
            sp = single_position(t)
            sp = sp.dropna(subset=["entry_date"]) if not sp.empty else sp
            if not sp.empty:
                sp["filter"] = fname
                singles.append(sp)
                for x in summarize(sp, "single_position"):
                    rows.append({"rule": rule, "filter": fname, **x})
    res = pd.DataFrame(rows)
    res.to_csv(out / "summary.csv", index=False, encoding="utf-8-sig")
    if singles:
        pd.concat(singles).to_csv(out / "single_position_trades.csv", index=False, encoding="utf-8-sig")

    # 第3项配对比较：同一批信号，各规则相对 orig 的 net_r 差
    filled = trades.dropna(subset=["entry_date"])
    wide = filled.pivot_table(index=["signal_date", "instrument_id"], columns="rule", values="net_r")
    paired = []
    for rule in EXIT_RULES:
        if rule == "orig":
            continue
        for period, (y0, y1) in PERIODS.items():
            yrs = wide.index.get_level_values(0).year
            dlt = (wide[rule] - wide["orig"])[(yrs >= y0) & (yrs <= y1)].dropna()
            paired.append({"rule": rule, "period": period, "n": len(dlt), "mean_delta_r_vs_orig": dlt.mean(),
                           "t": dlt.mean() / dlt.std(ddof=1) * math.sqrt(len(dlt)) if len(dlt) > 1 else math.nan,
                           "share_better": (dlt > 0).mean(), "share_changed": (dlt.abs() > 1e-9).mean()})
    pd.DataFrame(paired).to_csv(out / "paired_exit_vs_orig.csv", index=False, encoding="utf-8-sig")

    # 第4项：按合成分分档看原规则的结果
    o = filled[filled["rule"].eq("orig")].copy()
    o["bucket"] = pd.cut(o["composite_pct"], [0, 0.1, 0.2, 0.5, 0.8, 1.0], labels=["0-10%", "10-20%", "20-50%", "50-80%", "80-100%"])
    o["period"] = np.where(o["signal_date"].dt.year <= 2024, "dev_2023_2024", "diag_2025_2026")
    buckets = o.groupby(["period", "bucket"], observed=False).agg(
        trades=("net_r", "size"), mean_net_r=("net_r", "mean"), win_rate=("net_return", lambda s: (s > 0).mean()),
        mean_net_return=("net_return", "mean")).reset_index()
    buckets.to_csv(out / "composite_bucket_orig.csv", index=False, encoding="utf-8-sig")

    meta = {"state_cache": str(cache), "state_end": str(end.date()), "signals": int(len(sig)),
            "signals_without_price_row": missing_states,
            "signals_by_year": {str(k): int(v) for k, v in sig["signal_date"].dt.year.value_counts().sort_index().items()},
            "signals_missing_composite": int(sig["composite_pct"].isna().sum()),
            "ranking_source": str(cand_path) if cand_path else None,
            "skipped": {f"{k[0]}|{k[1]}": v for k, v in skipped.items() if k[0] == "orig"}}
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    pd.set_option("display.width", 250)
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    show = ["rule", "filter", "mode", "period", "trades", "win_rate", "mean_net_r", "net_r_t", "profit_factor", "max_dd_full_weight"]
    print(res[[c for c in show if c in res]].round(3).to_string(index=False))
    print(pd.DataFrame(paired).round(3).to_string(index=False))
    print(buckets.round(3).to_string(index=False))
    print(f"结果已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
