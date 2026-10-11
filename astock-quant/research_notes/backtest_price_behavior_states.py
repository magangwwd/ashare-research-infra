from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd


HORIZONS = (5, 10, 20)
POSITIVE_LABELS = {"trend_up", "base_building", "panic_reversal_candidate"}
NEGATIVE_LABELS = {
    "high_volume_stall",
    "failed_breakout_risk",
    "trend_down",
    "crowding_risk",
    "anchor_pressure_risk",
    "support_break_risk",
    "panic_risk",
}


def _clip01(s: pd.Series) -> pd.Series:
    return s.clip(lower=0, upper=1)


def _safe_div(a: pd.Series, b: pd.Series) -> pd.Series:
    out = a / b.replace(0, np.nan)
    return out.replace([np.inf, -np.inf], np.nan)


def _future_min_low(low: pd.Series, horizon: int) -> pd.Series:
    shifted = [low.shift(-step) for step in range(1, horizon + 1)]
    stacked = pd.concat(shifted, axis=1)
    valid = stacked.notna().sum(axis=1).eq(horizon)
    return stacked.min(axis=1).where(valid)


def read_daily_data(data_dir: Path, sample_size: int | None = None) -> pd.DataFrame:
    paths = sorted(data_dir.glob("*.csv"))
    if sample_size:
        paths = paths[:sample_size]

    frames: list[pd.DataFrame] = []
    columns = ["Date", "Close", "High", "Low", "Open", "Volume", "TurnoverRate"]
    for path in paths:
        try:
            df = pd.read_csv(path, usecols=lambda c: c in columns)
        except Exception:
            continue
        if not {"Date", "Close", "High", "Low", "Open", "Volume"}.issubset(df.columns):
            continue
        if "TurnoverRate" not in df.columns:
            df["TurnoverRate"] = np.nan

        df["ts_code"] = path.stem
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        for col in ["Close", "High", "Low", "Open", "Volume", "TurnoverRate"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["Date", "Close", "High", "Low", "Open", "Volume"])
        df = df[(df["Close"] > 0) & (df["High"] > 0) & (df["Low"] > 0)]
        if len(df) >= 160:
            frames.append(df)

    if not frames:
        raise RuntimeError(f"No usable daily csv files found in {data_dir}")
    return pd.concat(frames, ignore_index=True)


def add_symbol_features(group: pd.DataFrame) -> pd.DataFrame:
    g = group.sort_values("Date").copy()
    if "ts_code" not in g.columns:
        g["ts_code"] = getattr(group, "name", None)
    g["listed_days"] = np.arange(1, len(g) + 1)

    close = g["Close"]
    high = g["High"]
    low = g["Low"]
    volume = g["Volume"]
    turnover = g["TurnoverRate"]
    daily_range = (high - low).replace(0, np.nan)

    g["ret_1d"] = close.pct_change(1)
    for window in [5, 10, 20, 60, 120, 250]:
        g[f"ret_{window}d"] = close.pct_change(window)

    g["ma20"] = close.rolling(20, min_periods=20).mean()
    g["ma60"] = close.rolling(60, min_periods=60).mean()
    g["ma20_slope_5d"] = g["ma20"].pct_change(5)
    g["ma60_slope_20d"] = g["ma60"].pct_change(20)

    g["high20"] = high.rolling(20, min_periods=20).max()
    g["low20"] = low.rolling(20, min_periods=20).min()
    g["high60"] = high.rolling(60, min_periods=60).max()
    g["low60"] = low.rolling(60, min_periods=60).min()
    g["prev_high20"] = high.shift(1).rolling(20, min_periods=20).max()
    g["prev_low20"] = low.shift(1).rolling(20, min_periods=20).min()

    g["range_pos20"] = _safe_div(close - g["low20"], g["high20"] - g["low20"])
    g["range_pos60"] = _safe_div(close - g["low60"], g["high60"] - g["low60"])
    g["range_width60"] = _safe_div(g["high60"], g["low60"]) - 1
    g["gap_from_high60"] = _safe_div(close, g["high60"]) - 1

    g["clv"] = _safe_div(close - low, daily_range)
    g["upper_shadow"] = _safe_div(high - np.maximum(g["Open"], close), daily_range)
    g["lower_shadow"] = _safe_div(np.minimum(g["Open"], close) - low, daily_range)
    g["amplitude"] = _safe_div(high - low, close)
    g["amplitude20"] = g["amplitude"].rolling(20, min_periods=20).mean()

    g["volume_ma20"] = volume.rolling(20, min_periods=20).mean()
    g["volume_ma60"] = volume.rolling(60, min_periods=60).mean()
    g["volume_std20"] = volume.rolling(20, min_periods=20).std(ddof=0)
    g["volume_z20"] = _safe_div(volume - g["volume_ma20"], g["volume_std20"])
    g["volume_ratio20"] = _safe_div(volume, g["volume_ma20"])
    g["volume_5_20"] = _safe_div(volume.rolling(5, min_periods=5).mean(), g["volume_ma20"])

    g["turnover_ma20"] = turnover.rolling(20, min_periods=8).mean()
    g["turnover_std20"] = turnover.rolling(20, min_periods=8).std(ddof=0)
    g["turnover_z20"] = _safe_div(turnover - g["turnover_ma20"], g["turnover_std20"])

    g["support_reclaim"] = ((low < g["prev_low20"]) & (close > g["prev_low20"])).astype(float)
    g["breakout_fail_intraday"] = (
        (high > g["prev_high20"] * 1.005)
        & (close < g["prev_high20"])
        & (g["clv"] < 0.5)
    ).astype(float)

    for horizon in HORIZONS:
        g[f"fwd_ret_{horizon}d"] = close.shift(-horizon) / close - 1
        future_low = _future_min_low(low, horizon)
        g[f"fwd_drawdown_{horizon}d"] = future_low / close - 1

    return g


def add_cross_sectional_scores(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    pct_cols = [
        "ret_5d",
        "ret_20d",
        "ret_60d",
        "ret_120d",
        "ma20_slope_5d",
        "ma60_slope_20d",
        "volume_z20",
        "volume_ratio20",
        "turnover_z20",
        "amplitude20",
    ]
    for col in pct_cols:
        df[f"{col}_pct"] = df.groupby("Date", observed=True)[col].rank(pct=True)

    trend_components = [
        df["ret_20d_pct"],
        df["ret_60d_pct"],
        df["ma20_slope_5d_pct"],
        _clip01(df["range_pos60"]),
    ]
    df["trend_score"] = np.nanmean(np.vstack([s.to_numpy() for s in trend_components]), axis=0) * 100

    participation_components = [
        df["volume_z20_pct"],
        df["volume_ratio20_pct"],
        df["turnover_z20_pct"],
    ]
    df["participation_score"] = (
        np.nanmean(np.vstack([s.to_numpy() for s in participation_components]), axis=0) * 100
    )

    pressure_components = [
        _clip01(df["range_pos60"]),
        _clip01(df["upper_shadow"]),
        _clip01(1 - df["clv"]),
        df["volume_z20_pct"],
        (df["gap_from_high60"] > -0.05).astype(float),
    ]
    df["pressure_score"] = np.nanmean(np.vstack([s.to_numpy() for s in pressure_components]), axis=0) * 100

    panic_components = [
        1 - df["ret_1d"].groupby(df["Date"], observed=True).rank(pct=True),
        _clip01(1 - df["clv"]),
        df["volume_z20_pct"],
        (df["Close"] < df["prev_low20"]).astype(float),
    ]
    df["panic_score"] = np.nanmean(np.vstack([s.to_numpy() for s in panic_components]), axis=0) * 100

    support_components = [
        _clip01(df["lower_shadow"]),
        _clip01(df["clv"]),
        df["support_reclaim"],
        _clip01(df["range_pos20"]),
    ]
    df["support_score"] = np.nanmean(np.vstack([s.to_numpy() for s in support_components]), axis=0) * 100

    crowding_components = [
        df["ret_5d_pct"],
        df["volume_z20_pct"],
        df["turnover_z20_pct"],
        df["amplitude20_pct"],
        _clip01(df["range_pos60"]),
    ]
    df["crowding_score"] = np.nanmean(np.vstack([s.to_numpy() for s in crowding_components]), axis=0) * 100

    relative_components = [df["ret_20d_pct"], df["ret_60d_pct"]]
    df["relative_strength_score"] = (
        np.nanmean(np.vstack([s.to_numpy() for s in relative_components]), axis=0) * 100
    )

    raw = (
        0.35 * df["trend_score"]
        + 0.20 * df["support_score"]
        + 0.20 * df["relative_strength_score"]
        + 0.10 * df["participation_score"]
        + 0.15 * (100 - df["pressure_score"])
        - 0.12 * df["crowding_score"]
        - 0.08 * df["panic_score"]
    )
    df["behavior_score_raw"] = raw
    df["behavior_score"] = df.groupby("Date", observed=True)["behavior_score_raw"].rank(pct=True) * 100
    df["score_quintile"] = np.ceil(df["behavior_score"] / 20).clip(1, 5)

    return df


def add_labels(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    df["trend_up"] = (
        (df["trend_score"] >= 70)
        & (df["relative_strength_score"] >= 65)
        & (df["Close"] > df["ma20"])
        & (df["ma20"] > df["ma60"])
        & (df["range_pos60"] >= 0.65)
    )
    df["high_volume_stall"] = (
        (df["range_pos60"] >= 0.75)
        & (df["volume_z20"] >= 1.0)
        & (df["clv"] <= 0.45)
        & (df["upper_shadow"] >= 0.20)
    )
    df["base_building"] = (
        (df["ret_60d"].abs() <= 0.12)
        & (df["range_width60"] <= 0.45)
        & (df["range_pos60"].between(0.25, 0.75))
        & (df["volume_5_20"] <= 1.05)
        & (df["panic_score"] < 60)
    )
    df["failed_breakout_risk"] = (
        (df["breakout_fail_intraday"] > 0)
        & (df["volume_z20"] >= 0.5)
        & (df["range_pos60"] >= 0.65)
    )
    df["panic_reversal_candidate"] = (
        (
            (df["Low"] < df["prev_low20"] * 0.995)
            & (df["Close"] > df["prev_low20"])
            & (df["lower_shadow"] >= 0.25)
            & (df["volume_z20"] >= 0.5)
        )
        | (
            (df["ret_1d"] <= -0.06)
            & (df["lower_shadow"] >= 0.30)
            & (df["clv"] >= 0.45)
            & (df["volume_z20"] >= 0.8)
        )
    )
    df["trend_down"] = (
        (df["ret_20d"] <= -0.08)
        & (df["ret_60d"] <= -0.15)
        & (df["Close"] < df["ma20"])
        & (df["ma20"] < df["ma60"])
        & (df["range_pos60"] <= 0.35)
    )

    df["crowding_risk"] = (
        (df["ret_5d"] >= 0.12)
        & (df["range_pos60"] >= 0.75)
        & ((df["volume_z20"] >= 1.0) | (df["turnover_z20"] >= 1.0))
    )
    df["anchor_pressure_risk"] = (
        (df["gap_from_high60"] >= -0.03)
        & (df["clv"] < 0.50)
        & (df["upper_shadow"] >= 0.20)
    )
    df["support_break_risk"] = (df["Close"] < df["prev_low20"]) & (df["volume_z20"] >= 0.5)
    df["panic_risk"] = (df["ret_1d"] <= -0.06) & (df["clv"] <= 0.35) & (df["volume_z20"] >= 1.0)

    priority = [
        "failed_breakout_risk",
        "high_volume_stall",
        "panic_reversal_candidate",
        "trend_down",
        "trend_up",
        "base_building",
    ]
    df["state_label"] = "unclassified"
    for label in priority:
        df.loc[(df["state_label"] == "unclassified") & df[label], "state_label"] = label

    return df


def summarize_events(df: pd.DataFrame, label_col: str, labels: list[str]) -> pd.DataFrame:
    rows = []
    for horizon in HORIZONS:
        fwd = f"fwd_ret_{horizon}d"
        dd = f"fwd_drawdown_{horizon}d"
        base = df.dropna(subset=[fwd, dd])
        base_win = (base[fwd] > 0).mean()
        base_loss = (base[fwd] < 0).mean()
        base_mean = base[fwd].mean()
        base_deep_dd = (base[dd] <= -0.08).mean()

        for label in labels:
            if label_col == "state_label":
                sample = base[base[label_col] == label]
            else:
                sample = base[base[label].fillna(False)]
            if len(sample) == 0:
                continue

            win_rate = (sample[fwd] > 0).mean()
            loss_rate = (sample[fwd] < 0).mean()
            deep_dd_rate = (sample[dd] <= -0.08).mean()
            expected = "up" if label in POSITIVE_LABELS else "down" if label in NEGATIVE_LABELS else "neutral"
            if expected == "up":
                direction_accuracy = win_rate
                base_accuracy = base_win
            elif expected == "down":
                direction_accuracy = loss_rate
                base_accuracy = base_loss
            else:
                direction_accuracy = np.nan
                base_accuracy = np.nan

            rows.append(
                {
                    "label_type": label_col,
                    "label": label,
                    "horizon": horizon,
                    "n": len(sample),
                    "coverage_pct": len(sample) / len(base) * 100,
                    "expected_direction": expected,
                    "direction_accuracy": direction_accuracy,
                    "direction_accuracy_lift_pp": (direction_accuracy - base_accuracy) * 100
                    if pd.notna(direction_accuracy)
                    else np.nan,
                    "mean_ret": sample[fwd].mean(),
                    "mean_ret_lift_pp": (sample[fwd].mean() - base_mean) * 100,
                    "median_ret": sample[fwd].median(),
                    "win_rate": win_rate,
                    "loss_rate": loss_rate,
                    "avg_drawdown": sample[dd].mean(),
                    "deep_drawdown_rate": deep_dd_rate,
                    "deep_drawdown_lift_pp": (deep_dd_rate - base_deep_dd) * 100,
                }
            )
    return pd.DataFrame(rows)


def summarize_baseline(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for horizon in HORIZONS:
        fwd = f"fwd_ret_{horizon}d"
        dd = f"fwd_drawdown_{horizon}d"
        sample = df.dropna(subset=[fwd, dd])
        rows.append(
            {
                "horizon": horizon,
                "n": len(sample),
                "mean_ret": sample[fwd].mean(),
                "median_ret": sample[fwd].median(),
                "win_rate": (sample[fwd] > 0).mean(),
                "loss_rate": (sample[fwd] < 0).mean(),
                "avg_drawdown": sample[dd].mean(),
                "deep_drawdown_rate": (sample[dd] <= -0.08).mean(),
            }
        )
    return pd.DataFrame(rows)


def summarize_score_quintiles(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for horizon in HORIZONS:
        fwd = f"fwd_ret_{horizon}d"
        dd = f"fwd_drawdown_{horizon}d"
        sample = df.dropna(subset=[fwd, dd, "score_quintile"])
        for q, part in sample.groupby("score_quintile", observed=True):
            rows.append(
                {
                    "horizon": horizon,
                    "score_quintile": int(q),
                    "n": len(part),
                    "mean_ret": part[fwd].mean(),
                    "median_ret": part[fwd].median(),
                    "win_rate": (part[fwd] > 0).mean(),
                    "avg_drawdown": part[dd].mean(),
                    "deep_drawdown_rate": (part[dd] <= -0.08).mean(),
                }
            )
    return pd.DataFrame(rows)


def summarize_ic(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for horizon in HORIZONS:
        fwd = f"fwd_ret_{horizon}d"
        sample = df.dropna(subset=["behavior_score", fwd])
        daily_ics = []
        for _, part in sample.groupby("Date", observed=True):
            if len(part) < 50:
                continue
            corr = part["behavior_score"].corr(part[fwd], method="spearman")
            if pd.notna(corr):
                daily_ics.append(corr)
        arr = np.asarray(daily_ics, dtype=float)
        rows.append(
            {
                "horizon": horizon,
                "n_dates": len(arr),
                "mean_spearman_ic": arr.mean() if len(arr) else np.nan,
                "median_spearman_ic": np.median(arr) if len(arr) else np.nan,
                "positive_ic_rate": (arr > 0).mean() if len(arr) else np.nan,
                "ic_t_stat": arr.mean() / (arr.std(ddof=1) / math.sqrt(len(arr)))
                if len(arr) > 1 and arr.std(ddof=1) > 0
                else np.nan,
            }
        )
    return pd.DataFrame(rows)


def fmt_pct(x: float) -> str:
    if pd.isna(x):
        return ""
    return f"{x * 100:.2f}%"


def fmt_pp(x: float) -> str:
    if pd.isna(x):
        return ""
    return f"{x:+.2f}"


def md_table(df: pd.DataFrame, columns: list[str], formatters: dict[str, callable] | None = None) -> str:
    formatters = formatters or {}
    if df.empty:
        return "_无样本_"
    lines = []
    lines.append("| " + " | ".join(columns) + " |")
    lines.append("| " + " | ".join(["---"] * len(columns)) + " |")
    for _, row in df[columns].iterrows():
        vals = []
        for col in columns:
            val = row[col]
            if col in formatters:
                vals.append(str(formatters[col](val)))
            elif isinstance(val, float):
                vals.append(f"{val:.4f}")
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def write_report(
    out_path: Path,
    df: pd.DataFrame,
    baseline: pd.DataFrame,
    event_summary: pd.DataFrame,
    quintiles: pd.DataFrame,
    ic: pd.DataFrame,
    start_date: str,
) -> None:
    state20 = event_summary[
        (event_summary["label_type"] == "state_label")
        & (event_summary["horizon"] == 20)
        & (event_summary["n"] >= 200)
    ].sort_values("direction_accuracy_lift_pp", ascending=False)
    risk20 = event_summary[
        (event_summary["label_type"] == "risk_tag")
        & (event_summary["horizon"] == 20)
        & (event_summary["n"] >= 200)
    ].sort_values("direction_accuracy_lift_pp", ascending=False)
    q20 = quintiles[quintiles["horizon"] == 20].sort_values("score_quintile")

    fmt = {
        "mean_ret": fmt_pct,
        "median_ret": fmt_pct,
        "win_rate": fmt_pct,
        "loss_rate": fmt_pct,
        "avg_drawdown": fmt_pct,
        "deep_drawdown_rate": fmt_pct,
        "direction_accuracy": fmt_pct,
        "direction_accuracy_lift_pp": fmt_pp,
        "mean_ret_lift_pp": fmt_pp,
        "deep_drawdown_lift_pp": fmt_pp,
        "coverage_pct": lambda x: f"{x:.2f}%",
        "mean_spearman_ic": lambda x: f"{x:.4f}" if pd.notna(x) else "",
        "median_spearman_ic": lambda x: f"{x:.4f}" if pd.notna(x) else "",
        "positive_ic_rate": fmt_pct,
        "ic_t_stat": lambda x: f"{x:.2f}" if pd.notna(x) else "",
    }

    q_spread = np.nan
    if not q20.empty and {1, 5}.issubset(set(q20["score_quintile"])):
        q1 = q20.loc[q20["score_quintile"] == 1, "mean_ret"].iloc[0]
        q5 = q20.loc[q20["score_quintile"] == 5, "mean_ret"].iloc[0]
        q_spread = q5 - q1

    ic20 = ic.loc[ic["horizon"] == 20, "mean_spearman_ic"].iloc[0]

    conclusion = []
    conclusion.append("可行。价格行为状态和心理风险标签可以被统一为可计算特征，并能批量回测。")
    if pd.notna(q_spread) and q_spread > 0:
        conclusion.append(
            f"连续 behavior_score 在 20 日窗口的 Q5-Q1 平均收益差为 {q_spread * 100:.2f} 个百分点，说明分层有一定信息量。"
        )
    else:
        conclusion.append("连续 behavior_score 的高低分层没有体现稳定正向收益差，暂不适合作为独立选股因子。")
    if pd.notna(ic20) and ic20 > 0.01:
        conclusion.append(f"20 日 Spearman IC 均值为 {ic20:.4f}，有弱预测信息，但仍属于辅助因子级别。")
    else:
        conclusion.append(f"20 日 Spearman IC 均值为 {ic20:.4f}，预测强度偏弱，更适合做风险过滤和复盘标签。")

    text = f"""# 价格行为状态与心理风险标签回测

生成日期：2026-06-05

## 回测口径

- 数据来源：`daily_data/*.csv`
- 起始日期：{start_date}
- 股票数：{df['ts_code'].nunique()}
- 事件样本行：{len(df)}
- 预测窗口：未来 5/10/20 个交易日
- 收益口径：信号日收盘价到未来第 N 个交易日收盘价
- 回撤口径：信号日收盘价到未来 N 日最低价
- 注意：这是研究回测，不含交易成本、停牌处理、涨跌停可成交性和行业/市值中性化。

## 结论

{chr(10).join(f"- {line}" for line in conclusion)}

## 全样本基准

{md_table(baseline, ['horizon', 'n', 'mean_ret', 'median_ret', 'win_rate', 'loss_rate', 'avg_drawdown', 'deep_drawdown_rate'], fmt)}

## Behavior Score 分层

分位数按每日横截面计算，Q5 为最高行为分，Q1 为最低行为分。

{md_table(q20, ['score_quintile', 'n', 'mean_ret', 'median_ret', 'win_rate', 'avg_drawdown', 'deep_drawdown_rate'], fmt)}

## IC 检验

{md_table(ic, ['horizon', 'n_dates', 'mean_spearman_ic', 'median_spearman_ic', 'positive_ic_rate', 'ic_t_stat'], fmt)}

## 20 日价格行为状态

{md_table(state20, ['label', 'n', 'coverage_pct', 'expected_direction', 'direction_accuracy', 'direction_accuracy_lift_pp', 'mean_ret', 'mean_ret_lift_pp', 'avg_drawdown', 'deep_drawdown_rate', 'deep_drawdown_lift_pp'], fmt)}

## 20 日心理风险标签

{md_table(risk20, ['label', 'n', 'coverage_pct', 'expected_direction', 'direction_accuracy', 'direction_accuracy_lift_pp', 'mean_ret', 'mean_ret_lift_pp', 'avg_drawdown', 'deep_drawdown_rate', 'deep_drawdown_lift_pp'], fmt)}

## 应用判断

- 若用于单独预测价格方向，当前标签只能算弱信号，需要继续做行业、市值、波动率和市场状态分层。
- 若用于筛选报告候选股，应用价值更明确：可以识别高位分歧、突破失败、拥挤和支撑破位，作为“不追高”和“等待确认”的风险过滤器。
- 更适合的用法是与财务分、LightGBM、Kronos 联合：模型给方向，价格行为状态给入场质量和风险状态。
- 下一步应把 `stock_state_features.csv` 接入 LightGBM 特征，比较加入前后的 OOS IC、Top-K 命中率和回撤。
"""
    out_path.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest price behavior states and psychology risk tags.")
    parser.add_argument("--data-dir", type=Path, default=Path("daily_data"))
    parser.add_argument("--out-dir", type=Path, default=Path("research_notes"))
    parser.add_argument("--start-date", default="2023-01-01")
    parser.add_argument("--min-history", type=int, default=120)
    parser.add_argument("--sample-size", type=int, default=None)
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)

    raw = read_daily_data(args.data_dir, args.sample_size)
    featured = raw.groupby("ts_code", group_keys=False).apply(add_symbol_features).reset_index(drop=True)
    featured = featured[featured["listed_days"] >= args.min_history].copy()
    featured = featured[featured["Date"] >= pd.Timestamp(args.start_date)].copy()
    featured = add_cross_sectional_scores(featured)
    featured = add_labels(featured)

    eval_cols = [f"fwd_ret_{h}d" for h in HORIZONS] + [f"fwd_drawdown_{h}d" for h in HORIZONS]
    eval_df = featured.dropna(subset=["behavior_score"] + eval_cols, how="all").copy()

    baseline = summarize_baseline(eval_df)
    state_labels = [
        "trend_up",
        "high_volume_stall",
        "base_building",
        "failed_breakout_risk",
        "panic_reversal_candidate",
        "trend_down",
    ]
    risk_labels = ["crowding_risk", "anchor_pressure_risk", "support_break_risk", "panic_risk"]
    state_summary = summarize_events(eval_df, "state_label", state_labels)
    risk_summary = summarize_events(eval_df, "risk_tag", risk_labels)
    event_summary = pd.concat([state_summary, risk_summary], ignore_index=True)
    quintiles = summarize_score_quintiles(eval_df)
    ic = summarize_ic(eval_df)

    event_summary.to_csv(args.out_dir / "price_behavior_state_event_summary.csv", index=False, encoding="utf-8-sig")
    baseline.to_csv(args.out_dir / "price_behavior_state_baseline.csv", index=False, encoding="utf-8-sig")
    quintiles.to_csv(args.out_dir / "price_behavior_state_score_quintiles.csv", index=False, encoding="utf-8-sig")
    ic.to_csv(args.out_dir / "price_behavior_state_ic.csv", index=False, encoding="utf-8-sig")

    write_report(
        args.out_dir / "price_behavior_state_backtest_report.md",
        eval_df,
        baseline,
        event_summary,
        quintiles,
        ic,
        args.start_date,
    )

    print(f"stocks={eval_df['ts_code'].nunique()} rows={len(eval_df)}")
    print(f"wrote {args.out_dir / 'price_behavior_state_backtest_report.md'}")


if __name__ == "__main__":
    main()
