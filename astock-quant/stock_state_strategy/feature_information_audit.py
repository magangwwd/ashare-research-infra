from __future__ import annotations

import argparse
import gc
import json
import math
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backtest_mfe_mae import build_stock_views, load_config, resolve_from
from ml_cprr_v2 import add_daily_features, load_daily_panel
from ml_cprr_v3 import DEFAULT_CAPITAL, FEATURES, simulate_single_position
from ml_net_r_v4 import HORIZONS, add_executable_net_r_labels, build_samples


PERIODS = ("2022", "2023", "2024", "2025_plus")
SEED = 20260816


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(json_value(value), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def period_frame(samples: pd.DataFrame, period: str) -> pd.DataFrame:
    year = samples["signal_date"].dt.year
    if period == "2025_plus":
        return samples.loc[year >= 2025]
    return samples.loc[year == int(period)]


def audit_cross_sections(
    frame: pd.DataFrame,
    horizon: int,
    period: str,
) -> list[dict[str, Any]]:
    ic_values = {feature: [] for feature in FEATURES}
    high_sum = {feature: 0.0 for feature in FEATURES}
    high_positive = {feature: 0 for feature in FEATURES}
    high_count = {feature: 0 for feature in FEATURES}
    low_sum = {feature: 0.0 for feature in FEATURES}
    low_positive = {feature: 0 for feature in FEATURES}
    low_count = {feature: 0 for feature in FEATURES}

    for _, group in frame.groupby("signal_date", sort=False):
        if len(group) < 20:
            continue
        x_rank = group[FEATURES].rank(pct=True, method="average").to_numpy(
            dtype=float
        )
        y_rank = group["actual_net_r"].rank(
            pct=True, method="average"
        ).to_numpy(dtype=float)
        x_centered = x_rank - x_rank.mean(axis=0)
        y_centered = y_rank - y_rank.mean()
        denominator = np.sqrt(
            np.square(x_centered).sum(axis=0)
            * np.square(y_centered).sum()
        )
        numerator = (x_centered * y_centered[:, None]).sum(axis=0)
        correlations = np.divide(
            numerator,
            denominator,
            out=np.full(len(FEATURES), np.nan, dtype=float),
            where=denominator > 0,
        )
        actual = group["actual_net_r"].to_numpy(dtype=float)
        profitable = group["profitable"].to_numpy(dtype="int8")
        for column, feature in enumerate(FEATURES):
            if np.isfinite(correlations[column]):
                ic_values[feature].append(float(correlations[column]))
            high = x_rank[:, column] >= 0.90
            low = x_rank[:, column] <= 0.10
            high_sum[feature] += float(actual[high].sum())
            high_positive[feature] += int(profitable[high].sum())
            high_count[feature] += int(high.sum())
            low_sum[feature] += float(actual[low].sum())
            low_positive[feature] += int(profitable[low].sum())
            low_count[feature] += int(low.sum())

    universe_r = float(frame["actual_net_r"].mean())
    universe_profit = float(frame["profitable"].mean())
    rows = []
    for feature in FEATURES:
        values = np.asarray(ic_values[feature], dtype=float)
        high_r = high_sum[feature] / max(high_count[feature], 1)
        low_r = low_sum[feature] / max(low_count[feature], 1)
        high_profit = high_positive[feature] / max(high_count[feature], 1)
        low_profit = low_positive[feature] / max(low_count[feature], 1)
        mean_ic = float(values.mean()) if len(values) else math.nan
        std_ic = float(values.std(ddof=1)) if len(values) > 1 else math.nan
        rows.append(
            {
                "horizon": horizon,
                "period": period,
                "feature": feature,
                "rows": int(len(frame)),
                "signal_dates": int(frame["signal_date"].nunique()),
                "ic_dates": int(len(values)),
                "ic_mean": mean_ic,
                "ic_median": float(np.median(values)) if len(values) else math.nan,
                "ic_positive_rate": float((values > 0).mean()) if len(values) else math.nan,
                "ic_t_stat": (
                    mean_ic / (std_ic / math.sqrt(len(values)))
                    if len(values) > 1 and std_ic > 0
                    else math.nan
                ),
                "universe_net_r_mean": universe_r,
                "universe_profitable_rate": universe_profit,
                "high_decile_net_r_mean": high_r,
                "high_decile_net_r_lift": high_r - universe_r,
                "high_decile_profitable_rate": high_profit,
                "high_decile_profit_rate_lift": high_profit - universe_profit,
                "low_decile_net_r_mean": low_r,
                "low_decile_net_r_lift": low_r - universe_r,
                "low_decile_profitable_rate": low_profit,
                "low_decile_profit_rate_lift": low_profit - universe_profit,
            }
        )
    return rows


def build_stability(raw_metrics: pd.DataFrame) -> pd.DataFrame:
    records = []
    for (horizon, feature), group in raw_metrics.groupby(
        ["horizon", "feature"], sort=False
    ):
        by_period = group.set_index("period")
        base_ic = float(by_period.loc["2022", "ic_mean"])
        direction = 1 if base_ic >= 0 else -1
        directed = {}
        lifts = {}
        profit_lifts = {}
        for period in PERIODS:
            row = by_period.loc[period]
            directed[period] = direction * float(row["ic_mean"])
            if direction > 0:
                lifts[period] = float(row["high_decile_net_r_lift"])
                profit_lifts[period] = float(
                    row["high_decile_profit_rate_lift"]
                )
            else:
                lifts[period] = float(row["low_decile_net_r_lift"])
                profit_lifts[period] = float(
                    row["low_decile_profit_rate_lift"]
                )
        dev_periods = ("2022", "2023", "2024")
        dev_ic = [directed[period] for period in dev_periods]
        dev_lift = [lifts[period] for period in dev_periods]
        dev_profit_lift = [profit_lifts[period] for period in dev_periods]
        positive_ic_years = int(sum(value > 0 for value in dev_ic))
        positive_lift_years = int(sum(value > 0 for value in dev_lift))
        dev_ic_mean = float(np.mean(dev_ic))
        dev_lift_mean = float(np.mean(dev_lift))
        screen_pass = (
            positive_ic_years == 3
            and dev_ic_mean >= 0.01
            and positive_lift_years >= 2
            and dev_lift_mean > 0
        )
        strong_pass = (
            positive_ic_years == 3
            and dev_ic_mean >= 0.02
            and positive_lift_years == 3
            and dev_lift_mean > 0
        )
        records.append(
            {
                "horizon": int(horizon),
                "feature": feature,
                "direction": "HIGH" if direction > 0 else "LOW",
                "direction_source": "2022 IC sign",
                "directed_ic_2022": directed["2022"],
                "directed_ic_2023": directed["2023"],
                "directed_ic_2024": directed["2024"],
                "directed_ic_2025_plus": directed["2025_plus"],
                "dev_directed_ic_mean": dev_ic_mean,
                "positive_ic_years": positive_ic_years,
                "favorable_decile_lift_2022": lifts["2022"],
                "favorable_decile_lift_2023": lifts["2023"],
                "favorable_decile_lift_2024": lifts["2024"],
                "favorable_decile_lift_2025_plus": lifts["2025_plus"],
                "dev_favorable_decile_lift_mean": dev_lift_mean,
                "positive_lift_years": positive_lift_years,
                "dev_profit_rate_lift_mean": float(np.mean(dev_profit_lift)),
                "screen_pass": screen_pass,
                "strong_pass": strong_pass,
            }
        )
    return pd.DataFrame(records)


def feature_top1(
    samples: pd.DataFrame,
    feature: str,
    direction: str,
) -> pd.DataFrame:
    grouped = samples.groupby("signal_date", sort=False)[feature]
    indices = grouped.idxmax() if direction == "HIGH" else grouped.idxmin()
    return samples.loc[indices.to_numpy()].sort_values("signal_date").copy()


def run_single_feature_top1(
    samples: pd.DataFrame,
    stability: pd.DataFrame,
    price_panel: pd.DataFrame,
    calendar: pd.DataFrame,
    horizon: int,
    config: dict[str, Any],
    initial_capital: float,
) -> pd.DataFrame:
    selected_frames: dict[str, pd.DataFrame] = {}
    needed_ids: set[str] = set()
    horizon_stability = stability.loc[stability["horizon"] == horizon]
    for row in horizon_stability.itertuples(index=False):
        top1 = feature_top1(samples, row.feature, row.direction)
        selected_frames[row.feature] = top1
        needed_ids.update(str(value) for value in top1["instrument_id"].unique())
    views = build_stock_views(price_panel, needed_ids)
    cache: dict[tuple[str, int, int], tuple[dict[str, Any] | None, str]] = {}
    records = []
    for row in horizon_stability.itertuples(index=False):
        top1 = selected_frames[row.feature]
        period_definitions = {
            "development_2022_2024": top1.loc[
                top1["signal_date"].between(
                    pd.Timestamp("2022-01-01"), pd.Timestamp("2024-12-31")
                )
            ],
            "2022": top1.loc[top1["signal_date"].dt.year == 2022],
            "2023": top1.loc[top1["signal_date"].dt.year == 2023],
            "2024": top1.loc[top1["signal_date"].dt.year == 2024],
            "2025_plus": top1.loc[top1["signal_date"].dt.year >= 2025],
        }
        for period, signals in period_definitions.items():
            _, metrics, _ = simulate_single_position(
                signals,
                views,
                calendar,
                horizon,
                config,
                initial_capital,
                cache,
            )
            records.append(
                {
                    "horizon": horizon,
                    "feature": row.feature,
                    "direction": row.direction,
                    "period": period,
                    **metrics,
                }
            )
    return pd.DataFrame(records)


def combine_decision(
    stability: pd.DataFrame,
    top1_results: pd.DataFrame,
) -> pd.DataFrame:
    yearly = top1_results.loc[
        top1_results["period"].isin(["2022", "2023", "2024"])
    ].copy()
    yearly["positive_return"] = yearly["total_return"].astype(float) > 0
    positive_years = (
        yearly.groupby(["horizon", "feature"])["positive_return"]
        .sum()
        .rename("positive_top1_years")
    )
    development = top1_results.loc[
        top1_results["period"] == "development_2022_2024",
        [
            "horizon",
            "feature",
            "trades",
            "total_return",
            "average_r",
            "profit_factor",
            "max_drawdown",
        ],
    ].rename(
        columns={
            "trades": "dev_top1_trades",
            "total_return": "dev_top1_return",
            "average_r": "dev_top1_average_r",
            "profit_factor": "dev_top1_profit_factor",
            "max_drawdown": "dev_top1_max_drawdown",
        }
    )
    test = top1_results.loc[
        top1_results["period"] == "2025_plus",
        [
            "horizon",
            "feature",
            "trades",
            "total_return",
            "average_r",
            "profit_factor",
            "max_drawdown",
        ],
    ].rename(
        columns={
            "trades": "test_top1_trades",
            "total_return": "test_top1_return",
            "average_r": "test_top1_average_r",
            "profit_factor": "test_top1_profit_factor",
            "max_drawdown": "test_top1_max_drawdown",
        }
    )
    result = stability.merge(
        positive_years.reset_index(), on=["horizon", "feature"], how="left"
    ).merge(development, on=["horizon", "feature"], how="left").merge(
        test, on=["horizon", "feature"], how="left"
    )
    result["qualified_for_v5"] = (
        result["screen_pass"].astype(bool)
        & (result["positive_top1_years"] >= 2)
        & (result["dev_top1_return"] > 0)
        & (result["dev_top1_profit_factor"] > 1.0)
    )
    return result.sort_values(
        ["qualified_for_v5", "dev_directed_ic_mean", "dev_top1_return"],
        ascending=[False, False, False],
    ).reset_index(drop=True)


def pct(value: Any) -> str:
    if value is None or not np.isfinite(float(value)):
        return "NA"
    return f"{float(value):.2%}"


def num(value: Any, digits: int = 3) -> str:
    if value is None or not np.isfinite(float(value)):
        return "NA"
    return f"{float(value):.{digits}f}"


def write_report(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# 可执行净R特征信息审计",
        "",
        "## 结论",
        "",
        f"**{summary['decision']}**",
        "",
        f"共检查{summary['features']}个特征×{summary['horizons']}个周期，共{summary['comparisons']}组。方向只由2022年IC确定；2023、2024用于稳定性验证，2025+仅作旁证。",
        "",
        "## 各周期开发期排名前三",
        "",
        "| H | 特征 | 方向 | 三年IC | IC均值 | 优势分位净R增益 | Top1正收益年份 | 开发Top1收益 | 2025+ IC | 2025+ Top1收益 | V5候选 |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in summary["top_rows"]:
        lines.append(
            f"| {row['horizon']} | {row['feature']} | {row['direction']} | "
            f"{row['positive_ic_years']}/3 | {num(row['dev_directed_ic_mean'])} | "
            f"{num(row['dev_favorable_decile_lift_mean'])} | "
            f"{int(row['positive_top1_years'])}/3 | {pct(row['dev_top1_return'])} | "
            f"{num(row['directed_ic_2025_plus'])} | {pct(row['test_top1_return'])} | "
            f"{'是' if row['qualified_for_v5'] else '否'} |"
        )
    lines.extend(
        [
            "",
            "## 判定标准",
            "",
            "V5候选必须同时满足：2022—2024定向IC全部为正且均值≥0.01；优势Top10%净R增益至少两年为正且三年均值>0；单特征Top1至少两年正收益；开发期Top1总收益>0且盈利因子>1。该筛选仍未校正68组多重比较，因此只是进入下一轮研究的最低门槛，不是可交易结论。",
            "",
            "## 交易规则",
            "",
            "单特征Top1回测继续执行单股票、一次买入、一次全部卖出、不加仓、不减仓；持仓期间跳过新信号。标签使用与V4一致的6%保护止损、浮盈8%后5%移动退出和H日到期退出。",
            "",
            "## 下一步",
            "",
            summary["next_step"],
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Feature information audit")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--start-year", type=int, default=2020)
    parser.add_argument("--capital", type=float, default=DEFAULT_CAPITAL)
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    config = load_config(resolve_from(script_dir, args.config))
    data_root = resolve_from(script_dir, str(config["data_root"]))
    output_dir = (
        Path(args.output_dir).resolve()
        if args.output_dir
        else resolve_from(script_dir, str(config["output_dir"]))
        / "feature_audit"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    calendar_file = pd.read_parquet(data_root / "trading_calendar.parquet")
    end_year = int(pd.to_datetime(calendar_file["calendar_date"]).dt.year.max())
    del calendar_file

    panel, calendar, panel_audit = load_daily_panel(
        data_root, args.start_year, end_year
    )
    panel = add_daily_features(panel)
    price_columns = [
        "instrument_id",
        "trade_idx",
        "trade_date",
        "open",
        "adj_open",
        "adj_high",
        "adj_low",
        "adj_close",
        "limit_status",
    ]
    price_panel = panel.loc[
        panel["trade_date"] >= pd.Timestamp("2021-01-01"), price_columns
    ].copy()

    raw_rows: list[dict[str, Any]] = []
    all_top1_results = []
    sample_audits: dict[int, Any] = {}
    label_audits: dict[int, Any] = {}
    stability_parts = []
    for horizon in HORIZONS:
        panel, label_audit = add_executable_net_r_labels(
            panel,
            horizon,
            float(config["buy_cost_rate"]),
            float(config["sell_cost_rate"]),
        )
        samples, sample_audit = build_samples(panel, calendar)
        samples = samples.loc[
            samples["signal_date"] >= pd.Timestamp("2022-01-01")
        ].copy()
        label_audits[horizon] = label_audit
        sample_audits[horizon] = sample_audit
        log(f"Auditing H={horizon}: {len(samples):,} rows")
        horizon_rows = []
        for period in PERIODS:
            frame = period_frame(samples, period)
            rows = audit_cross_sections(frame, horizon, period)
            horizon_rows.extend(rows)
            log(f"H={horizon} {period} cross-sectional audit complete")
        raw_rows.extend(horizon_rows)
        horizon_raw = pd.DataFrame(horizon_rows)
        horizon_stability = build_stability(horizon_raw)
        stability_parts.append(horizon_stability)
        top1_result = run_single_feature_top1(
            samples,
            horizon_stability,
            price_panel,
            calendar,
            horizon,
            config,
            float(args.capital),
        )
        all_top1_results.append(top1_result)
        del samples, horizon_raw, horizon_stability, top1_result
        gc.collect()

    raw_metrics = pd.DataFrame(raw_rows)
    stability = pd.concat(stability_parts, ignore_index=True)
    top1_results = pd.concat(all_top1_results, ignore_index=True)
    decision_table = combine_decision(stability, top1_results)
    qualified = decision_table.loc[decision_table["qualified_for_v5"]]
    if qualified.empty:
        decision = "现有17个价格量特征没有通过V5最低信息门槛"
        next_step = (
            "停止用现有特征继续训练LightGBM。若要继续，需要先加入新的信息源，"
            "优先考虑行业中性、估值/盈利质量、公告事件或更细粒度成交特征；"
            "新增特征仍须重复同一审计，不能直接进入模型。"
        )
    else:
        decision = (
            f"有{len(qualified)}个特征通过最低筛选，可进行受限V5组合验证"
        )
        next_step = (
            "V5只允许使用通过筛选的特征，并冻结方向；先做简单线性/等权组合和"
            "配对随机检验，不立即使用复杂模型。"
        )

    top_rows = []
    for horizon in HORIZONS:
        rows = decision_table.loc[decision_table["horizon"] == horizon].head(3)
        top_rows.extend(rows.to_dict("records"))
    summary = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "decision": decision,
        "next_step": next_step,
        "features": len(FEATURES),
        "horizons": len(HORIZONS),
        "comparisons": len(FEATURES) * len(HORIZONS),
        "qualified_count": int(len(qualified)),
        "qualified_features": qualified[
            [
                "horizon",
                "feature",
                "direction",
                "dev_directed_ic_mean",
                "dev_top1_return",
                "test_top1_return",
            ]
        ].to_dict("records"),
        "top_rows": top_rows,
        "panel_audit": panel_audit,
        "label_audits": label_audits,
        "sample_audits": sample_audits,
        "selection_rule": (
            "direction from 2022; positive directed IC in 2022/23/24 with mean>=0.01; "
            "positive favorable-decile lift in >=2 years and mean>0; positive Top1 "
            "return in >=2 years; development Top1 return>0 and profit factor>1"
        ),
        "multiple_testing_warning": "68 exploratory feature/horizon comparisons; no family-wise correction",
    }
    raw_metrics.to_csv(
        output_dir / "raw_feature_metrics.csv", index=False, encoding="utf-8-sig"
    )
    stability.to_csv(
        output_dir / "feature_stability.csv", index=False, encoding="utf-8-sig"
    )
    top1_results.to_csv(
        output_dir / "single_feature_top1.csv", index=False, encoding="utf-8-sig"
    )
    decision_table.to_csv(
        output_dir / "decision_table.csv", index=False, encoding="utf-8-sig"
    )
    write_json(output_dir / "summary.json", summary)
    write_report(output_dir / "report.md", summary)
    log(f"Decision: {decision}")
    log(f"Feature audit outputs: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
