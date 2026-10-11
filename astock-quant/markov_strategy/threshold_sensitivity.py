#!/usr/bin/env python3
"""
markov_strategy/threshold_sensitivity.py

no_trade / normal 阈值敏感性测试。
对比五类策略，验证调低 no_trade 阈值后能否满足验收标准。

用法（从项目根目录执行）：
  python markov_strategy/threshold_sensitivity.py
"""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "daily_data"

# 主板前缀过滤
MAINBOARD_PREFIXES = {"600", "601", "603", "605", "000", "001", "002", "003"}

# Markov 参数
TRANSITION_HORIZON = 5
TRANSITION_STEP = 5
TRAIN_WINDOW = 504
LAPLACE_ALPHA = 1.0

COST_BPS = 5e-4  # 5 bps 双边
STATE_INT = {"Strong": 2, "Neutral": 1, "Weak": 0}


# ── 数据加载 ──────────────────────────────────────────────────────────────

def _is_mainboard(stem: str) -> bool:
    code = stem.split(".")[0]
    return code[:3] in MAINBOARD_PREFIXES


def _read_one(fp: Path) -> tuple[str, pd.Series] | None:
    try:
        df = pd.read_csv(fp, usecols=["Date", "Close"], parse_dates=["Date"])
        df = df.dropna(subset=["Date", "Close"]).set_index("Date")
        s = pd.to_numeric(df["Close"], errors="coerce").dropna()
        return fp.stem, s
    except Exception:
        return None


def load_close_matrix() -> pd.DataFrame:
    """并发读取所有主板 CSV，返回 Close 宽表。"""
    targets = [fp for fp in DATA_DIR.glob("*.csv") if _is_mainboard(fp.stem)]
    print(f"  找到主板文件：{len(targets)} 个", flush=True)

    frames: dict[str, pd.Series] = {}
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = {pool.submit(_read_one, fp): fp for fp in targets}
        done = 0
        for fut in as_completed(futures):
            result = fut.result()
            if result is not None:
                frames[result[0]] = result[1]
            done += 1
            if done % 500 == 0:
                print(f"  已加载 {done}/{len(targets)}...", flush=True)

    if not frames:
        sys.exit("未加载到任何主板数据，请检查 daily_data/ 目录。")

    wide = pd.DataFrame(frames).sort_index()
    return wide


# ── 市场收益序列 ──────────────────────────────────────────────────────────

def build_market_series(close: pd.DataFrame) -> pd.DataFrame:
    ret = close.pct_change().clip(-0.10, 0.10)

    # eligible：每股过去 60 日有 >= 20 个有效收益
    has_close = close.notna()
    eligible = has_close.rolling(60, min_periods=1).sum() >= 20

    valid_ret = ret.where(eligible)
    n_eligible = eligible.sum(axis=1)
    n_valid = valid_ret.notna().sum(axis=1)
    coverage = n_valid / n_eligible.replace(0, np.nan)

    mkt_ret = valid_ret.mean(axis=1)
    med_ret = valid_ret.median(axis=1)
    breadth = (valid_ret > 0).sum(axis=1) / n_valid.replace(0, np.nan)

    bad = coverage < 0.80
    mkt_ret[bad] = np.nan
    med_ret[bad] = np.nan
    breadth[bad] = np.nan

    first = mkt_ret.first_valid_index()
    mkt_nav = (1 + mkt_ret.fillna(0)).cumprod()
    med_nav = (1 + med_ret.fillna(0)).cumprod()
    if first is not None:
        mkt_nav.loc[:first] = np.nan
        med_nav.loc[:first] = np.nan

    ret20 = mkt_nav.pct_change(20)
    median_ret20 = med_nav.pct_change(20)
    breadth20 = breadth.rolling(20, min_periods=10).mean()
    drawdown60 = mkt_nav / mkt_nav.rolling(60, min_periods=1).max() - 1

    return pd.DataFrame({
        "market_ret": mkt_ret,
        "market_nav": mkt_nav,
        "breadth": breadth,
        "breadth20": breadth20,
        "ret20": ret20,
        "median_ret20": median_ret20,
        "drawdown60": drawdown60,
        "coverage": coverage,
    })


# ── 状态标签（无未来函数）────────────────────────────────────────────────

def label_states(mkt: pd.DataFrame, lookback: int = 252) -> pd.Series:
    ret20 = mkt["ret20"]
    # shift(1)：t 日 P33/P67 只用到 t-1 日为止的历史
    p33 = ret20.shift(1).rolling(lookback, min_periods=lookback // 2).quantile(0.33)
    p67 = ret20.shift(1).rolling(lookback, min_periods=lookback // 2).quantile(0.67)

    states = pd.Series("Neutral", index=ret20.index)
    states[(ret20 > p67) & (ret20 > 0)] = "Strong"
    states[(ret20 < p33) & (ret20 < 0)] = "Weak"
    states[ret20.isna() | p33.isna()] = np.nan

    return states, p33, p67


# ── Rolling Markov ────────────────────────────────────────────────────────

def _count_transitions(codes: np.ndarray, horizon: int, step: int, alpha: float) -> np.ndarray:
    counts = np.full((3, 3), alpha)
    n = len(codes)
    for i in range(0, n - horizon, step):
        c, f = codes[i], codes[i + horizon]
        if c >= 0 and f >= 0:
            counts[c, f] += 1
    return counts / counts.sum(axis=1, keepdims=True)


def build_markov_signals(states: pd.Series) -> pd.DataFrame:
    state_int = states.map(STATE_INT).fillna(-1).astype(int).values
    dates = states.index
    n = len(dates)

    p_s = np.full(n, np.nan)
    p_n = np.full(n, np.nan)
    p_w = np.full(n, np.nan)

    for i in range(TRAIN_WINDOW, n):
        cur = state_int[i]
        if cur < 0:
            continue
        window = state_int[i - TRAIN_WINDOW: i]
        mat = _count_transitions(window, TRANSITION_HORIZON, TRANSITION_STEP, LAPLACE_ALPHA)
        p_s[i] = mat[cur, STATE_INT["Strong"]]
        p_n[i] = mat[cur, STATE_INT["Neutral"]]
        p_w[i] = mat[cur, STATE_INT["Weak"]]

    return pd.DataFrame({
        "p_strong": p_s,
        "p_neutral": p_n,
        "p_weak": p_w,
        "markov_edge": p_s - p_w,
    }, index=dates)


# ── 市场评分 ──────────────────────────────────────────────────────────────

def _trend_score(state: str, ret20: float) -> float:
    if state == "Strong":
        return 35.0
    if state == "Weak":
        return 4.0
    # Neutral
    return 20.0 if ret20 > 0 else 10.0


def _breadth_score(b20: float) -> float:
    return float(np.clip(b20 * 25.0, 0.0, 25.0))


def _median_score(m20: float) -> float:
    if m20 >= 0.04:
        return 15.0
    if m20 >= 0.02:
        return 12.0
    if m20 >= 0.00:
        return 8.0
    if m20 >= -0.05:
        return 4.0
    return 2.0


def _risk_score(dd60: float) -> float:
    if dd60 > -0.03:
        return 15.0
    if dd60 > -0.06:
        return 10.0
    if dd60 > -0.10:
        return 5.0
    return 0.0


def _markov_score(edge: float) -> float:
    if np.isnan(edge):
        return 5.0  # 无信号时给中性分
    if edge > 0.20:
        return 10.0
    if edge > 0.10:
        return 7.0
    if edge > -0.10:
        return 5.0
    if edge > -0.20:
        return 2.0
    return 0.0


def build_scores(mkt: pd.DataFrame, states: pd.Series, markov: pd.DataFrame) -> pd.DataFrame:
    ret20 = mkt["ret20"]
    b20 = mkt["breadth20"]
    m20 = mkt["median_ret20"]
    dd60 = mkt["drawdown60"]
    edge = markov["markov_edge"].reindex(mkt.index)

    t_scores, b_scores, m_scores, r_scores, mk_scores, totals = [], [], [], [], [], []

    for date in mkt.index:
        s = states.get(date)
        r = ret20.get(date)
        if pd.isna(s) or pd.isna(r):
            t_scores.append(np.nan)
            b_scores.append(np.nan)
            m_scores.append(np.nan)
            r_scores.append(np.nan)
            mk_scores.append(np.nan)
            totals.append(np.nan)
            continue

        ts = _trend_score(s, r)
        bs = _breadth_score(b20.get(date, np.nan)) if not pd.isna(b20.get(date, np.nan)) else np.nan
        ms = _median_score(m20.get(date, np.nan)) if not pd.isna(m20.get(date, np.nan)) else np.nan
        rs = _risk_score(dd60.get(date, np.nan)) if not pd.isna(dd60.get(date, np.nan)) else np.nan
        mks = _markov_score(edge.get(date, np.nan))

        parts = [x for x in [ts, bs, ms, rs, mks] if not pd.isna(x)]
        total = sum(parts) if parts else np.nan

        t_scores.append(ts); b_scores.append(bs); m_scores.append(ms)
        r_scores.append(rs); mk_scores.append(mks); totals.append(total)

    return pd.DataFrame({
        "trend_score": t_scores,
        "breadth_score": b_scores,
        "median_score": m_scores,
        "risk_score": r_scores,
        "markov_score": mk_scores,
        "market_score": totals,
        "state": states.values,
    }, index=mkt.index)


# ── 回测引擎 ─────────────────────────────────────────────────────────────

def _stats(daily_ret: pd.Series, exposure: pd.Series) -> dict:
    exp = exposure.shift(1)                       # t-1 信号作用于 t
    gross = exp * daily_ret
    cost = COST_BPS * exp.diff().abs().fillna(0)
    net = (gross - cost).fillna(0)

    nav = (1 + net).cumprod()
    n = len(nav)
    years = n / 252.0

    cagr = nav.iloc[-1] ** (1.0 / years) - 1
    dd = nav / nav.cummax() - 1
    max_dd = dd.min()
    vol = net.std() * np.sqrt(252)
    sharpe = cagr / vol if vol > 0 else 0.0
    calmar = cagr / abs(max_dd) if max_dd != 0 else 0.0
    avg_exp = exp.fillna(0).mean()
    no_trade_pct = (exp.fillna(0) == 0).mean()

    return {
        "CAGR%": round(cagr * 100, 2),
        "MaxDD%": round(max_dd * 100, 2),
        "Sharpe": round(sharpe, 3),
        "Calmar": round(calmar, 3),
        "Exposure%": round(avg_exp * 100, 1),
        "NoTrade%": round(no_trade_pct * 100, 1),
    }


def _exposure_from_score(score: pd.Series, no_trade_t: float, normal_t: float) -> pd.Series:
    exp = pd.Series(np.nan, index=score.index)
    valid = score.notna()
    exp[valid & (score >= normal_t)] = 1.0
    exp[valid & (score >= no_trade_t) & (score < normal_t)] = 0.5
    exp[valid & (score < no_trade_t)] = 0.0
    return exp


# ── 主程序 ───────────────────────────────────────────────────────────────

def _row(label: str, r: dict, bh: dict, ret20_r: dict) -> str:
    cagr_flag = "Y" if r["CAGR%"] >= bh["CAGR%"] * 0.70 else "N"
    sh_flag = "Y" if r["Sharpe"] >= ret20_r["Sharpe"] * 0.90 else "N"
    exp_flag = "Y" if 35.0 <= r["Exposure%"] <= 75.0 else "N"
    return (
        f"{label:<28}"
        f"{r['CAGR%']:>7.2f}%"
        f"{r['MaxDD%']:>8.2f}%"
        f"{r['Sharpe']:>8.3f}"
        f"{r['Calmar']:>8.3f}"
        f"{r['Exposure%']:>8.1f}%"
        f"{r['NoTrade%']:>8.1f}%"
        f"  {cagr_flag}{sh_flag}{exp_flag}"
    )


def main():
    print("=== Markov 市场状态过滤器 — 阈值敏感性测试 ===\n")

    print("1/5 加载主板 Close 数据...")
    close = load_close_matrix()
    print(f"  主板股票数={close.shape[1]}  日期={close.index[0].date()} ~ {close.index[-1].date()}\n")

    print("2/5 构建市场收益序列...")
    mkt = build_market_series(close)
    daily_ret = mkt["market_ret"]

    print("3/5 标注市场状态...")
    states, p33, p67 = label_states(mkt)
    state_counts = states.dropna().value_counts()
    print(f"  Strong={state_counts.get('Strong',0)}  Neutral={state_counts.get('Neutral',0)}  Weak={state_counts.get('Weak',0)}\n")

    print("4/5 Rolling Markov 矩阵估计...")
    markov = build_markov_signals(states)

    print("5/5 计算市场评分...")
    scores_df = build_scores(mkt, states, markov)
    score = scores_df["market_score"]

    # 最新一日打分（调试确认）
    last = scores_df.dropna(subset=["market_score"]).iloc[-1]
    print(f"\n  最新评分日期：{last.name.date()}")
    print(f"  state={last['state']}  score={last['market_score']:.2f}")
    print(f"  trend={last['trend_score']:.1f}  breadth={last['breadth_score']:.2f}")
    print(f"  median={last['median_score']:.1f}  risk={last['risk_score']:.1f}  markov={last['markov_score']:.1f}\n")

    # ── 基准策略 ──────────────────────────────────────────────────────────
    exp_bh = pd.Series(1.0, index=daily_ret.index)
    exp_ret20 = (mkt["ret20"].shift(1) > 0).astype(float)
    state_map = {"Strong": 1.0, "Neutral": 0.5, "Weak": 0.0}
    exp_state = states.shift(1).map(state_map)

    bh = _stats(daily_ret, exp_bh)
    ret20_r = _stats(daily_ret, exp_ret20)
    state_r = _stats(daily_ret, exp_state)

    HDR = (
        f"{'策略':<28}{'CAGR':>8}{'MaxDD':>9}{'Sharpe':>9}{'Calmar':>9}"
        f"{'仓位':>9}{'空仓%':>9}  验收"
    )
    SEP = "-" * 88

    print(HDR)
    print(SEP)
    print(_row("A: Buy & Hold", bh, bh, ret20_r))
    print(_row("B: ret20>0 简单过滤", ret20_r, bh, ret20_r))
    print(_row("C: 状态直接映射", state_r, bh, ret20_r))
    print()

    # ── 阈值敏感性：评分 + Markov (策略 E) ────────────────────────────────
    print("── 策略E：市场评分 + Markov（normal阈值=70）──")
    print(HDR)
    print(SEP)
    for nt in [30, 35, 40, 45, 50, 55]:
        exp = _exposure_from_score(score, nt, 70)
        r = _stats(daily_ret, exp)
        print(_row(f"  E: no_trade<{nt} normal>=70", r, bh, ret20_r))
    print()

    print("── 策略E：市场评分 + Markov（normal阈值=65）──")
    print(HDR)
    print(SEP)
    for nt in [30, 35, 40, 45, 50]:
        exp = _exposure_from_score(score, nt, 65)
        r = _stats(daily_ret, exp)
        print(_row(f"  E: no_trade<{nt} normal>=65", r, bh, ret20_r))
    print()

    # ── 不含 Markov（策略 D）──────────────────────────────────────────────
    score_d = score - scores_df["markov_score"] + 5.0   # Markov 替换为中性分 5.0
    print("── 策略D：市场评分（不含Markov，Markov固定5分，normal阈值=70）──")
    print(HDR)
    print(SEP)
    for nt in [30, 35, 40, 45, 50]:
        exp = _exposure_from_score(score_d, nt, 70)
        r = _stats(daily_ret, exp)
        print(_row(f"  D: no_trade<{nt} normal>=70", r, bh, ret20_r))
    print()

    # ── 验收标准说明 ───────────────────────────────────────────────────────
    print("验收符号：Y/N = [CAGR>=BH*70%] [Sharpe>=ret20>0*90%] [仓位35%-75%]")
    print(f"参考值：BH CAGR={bh['CAGR%']:.2f}%  ret20>0 Sharpe={ret20_r['Sharpe']:.3f}")
    print("\n完成。")


if __name__ == "__main__":
    main()
