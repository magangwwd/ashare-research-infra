from __future__ import annotations

import argparse
import gc
import json
import math
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backtest_mfe_mae import (
    load_config,
    load_panel,
    read_year_partitions,
    resolve_from,
)
from ml_cprr_v2 import add_daily_features
from ml_cprr_v3 import FEATURES
from v5_executable_tail import add_point_in_time_states


AUDIT_YEARS = (2022, 2023, 2024, 2025, 2026)
DISCOVERY_YEAR = 2022
VALIDATION_YEARS = (2023, 2024)
HOLDOUT_YEARS = (2025, 2026)
MIN_CROSS_SECTION = 20
MIN_LISTING_OBSERVATIONS = 250
MIN_AVERAGE_TURNOVER_CNY = 100_000_000.0
MIN_RECENT_OBSERVATIONS = 18
MIN_VALIDATION_DIRECTED_IC = 0.01
MAX_VALIDATION_FDR = 0.10
HAC_LAG = 20

TARGETS = {
    "net_r": {
        "column": "v5_net_r",
        "score_sign": 1.0,
        "description": "费用后可执行净R（越高越好）",
    },
    "tail_loss_r": {
        "column": "v5_tail_loss_r",
        "score_sign": -1.0,
        "description": "实际可卖退出路径尾部损失R（越低越好）",
    },
}


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


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(json_value(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def writable_output_dir(preferred: Path) -> Path:
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        probe = preferred / ".codex_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return preferred
    except OSError as exc:
        fallback = Path(tempfile.gettempdir()) / "demo01_v5_feature_audit_latest"
        fallback.mkdir(parents=True, exist_ok=True)
        print(f"Output directory is not writable ({exc}); using {fallback}", flush=True)
        return fallback


def newey_west_mean_test(
    values: np.ndarray | pd.Series,
    lag: int = HAC_LAG,
) -> tuple[float, float, float]:
    array = np.asarray(values, dtype=float)
    array = array[np.isfinite(array)]
    count = len(array)
    if count < 2:
        return math.nan, math.nan, math.nan
    mean = float(array.mean())
    centered = array - mean
    gamma_zero = float(np.dot(centered, centered) / count)
    long_run_variance = gamma_zero
    maximum_lag = min(int(lag), count - 1)
    for current_lag in range(1, maximum_lag + 1):
        covariance = float(
            np.dot(centered[current_lag:], centered[:-current_lag]) / count
        )
        weight = 1.0 - current_lag / (maximum_lag + 1.0)
        long_run_variance += 2.0 * weight * covariance
    long_run_variance = max(long_run_variance, 0.0)
    standard_error = math.sqrt(long_run_variance / count)
    if standard_error <= 0:
        statistic = math.inf if mean > 0 else (-math.inf if mean < 0 else 0.0)
    else:
        statistic = mean / standard_error
    p_value = math.erfc(abs(statistic) / math.sqrt(2.0))
    return mean, float(statistic), float(p_value)


def benjamini_hochberg(p_values: pd.Series) -> pd.Series:
    values = pd.to_numeric(p_values, errors="coerce").to_numpy(dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
    finite_positions = np.flatnonzero(np.isfinite(values))
    if not len(finite_positions):
        return pd.Series(result, index=p_values.index, dtype=float)
    order = finite_positions[np.argsort(values[finite_positions])]
    count = len(order)
    adjusted = values[order] * count / np.arange(1, count + 1)
    adjusted = np.minimum.accumulate(adjusted[::-1])[::-1]
    result[order] = np.clip(adjusted, 0.0, 1.0)
    return pd.Series(result, index=p_values.index, dtype=float)


def recent_observation_counts(panel: pd.DataFrame, window: int = 20) -> np.ndarray:
    counts = np.zeros(len(panel), dtype="int16")
    for positions in panel.groupby("instrument_id", sort=False).indices.values():
        pos = np.asarray(positions, dtype=np.int64)
        trade_indices = panel.loc[pos, "trade_idx"].to_numpy(dtype="int32")
        left = np.searchsorted(trade_indices, trade_indices - (window - 1), side="left")
        counts[pos] = (np.arange(len(pos)) - left + 1).astype("int16")
    return counts


def load_samples(
    data_root: Path,
    feature_start: pd.Timestamp,
    label_path: Path,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    panel, _, calendar, panel_audit = load_panel(data_root, feature_start)
    start_year = feature_start.year
    end_year = int(panel["trade_date"].dt.year.max())
    turnover = read_year_partitions(
        data_root,
        "daily_metrics",
        start_year,
        end_year,
        ["instrument_id", "trade_date", "turnover_rate_pct"],
    )
    turnover["trade_date"] = pd.to_datetime(turnover["trade_date"])
    duplicate_turnover = int(
        turnover.duplicated(["instrument_id", "trade_date"]).sum()
    )
    if duplicate_turnover:
        raise ValueError(f"turnover metric has {duplicate_turnover} duplicate keys")
    panel = panel.merge(
        turnover,
        on=["instrument_id", "trade_date"],
        how="left",
        validate="one_to_one",
    )
    missing_turnover = int(panel["turnover_rate_pct"].isna().sum())

    panel, point_state_audit = add_point_in_time_states(panel, data_root)
    panel = panel.sort_values(["instrument_id", "trade_idx"]).reset_index(drop=True)
    group = panel.groupby("instrument_id", sort=False)
    panel["listing_observations"] = (group.cumcount() + 1).astype("int32")
    panel["average_turnover_cny_20"] = group["turnover_cny"].transform(
        lambda values: values.rolling(20, min_periods=20).mean()
    )
    panel["recent_observations_20"] = recent_observation_counts(panel)
    panel = add_daily_features(panel)

    rank_eligible = panel["historical_state_known"] & ~panel["historical_is_st"]
    panel[["rs20_rank", "rs60_rank"]] = np.nan
    panel.loc[rank_eligible, "rs20_rank"] = panel.loc[rank_eligible].groupby(
        "trade_date", sort=False
    )["ret_20"].rank(pct=True)
    panel.loc[rank_eligible, "rs60_rank"] = panel.loc[rank_eligible].groupby(
        "trade_date", sort=False
    )["ret_60"].rank(pct=True)
    panel[FEATURES] = panel[FEATURES].astype("float32")

    feature_columns = [
        "instrument_id",
        "trade_date",
        "trade_idx",
        "listing_observations",
        "average_turnover_cny_20",
        "recent_observations_20",
        "historical_is_st",
        *FEATURES,
    ]
    features = panel[feature_columns].copy()
    del panel
    gc.collect()

    labels = pd.read_parquet(
        label_path,
        columns=[
            "instrument_id",
            "trade_date",
            "v5_net_r",
            "v5_tail_loss_r",
            "v5_delay_sessions",
            "v5_missing_held_sessions",
        ],
    )
    labels["trade_date"] = pd.to_datetime(labels["trade_date"])
    duplicate_labels = int(labels.duplicated(["instrument_id", "trade_date"]).sum())
    if duplicate_labels:
        raise ValueError(f"strict V5 labels have {duplicate_labels} duplicate keys")
    samples = features.merge(
        labels,
        on=["instrument_id", "trade_date"],
        how="inner",
        validate="one_to_one",
    )
    finite_features = np.isfinite(samples[FEATURES].to_numpy(dtype="float32")).all(axis=1)
    after_join = len(samples)
    date_eligible = samples["trade_date"].dt.year.isin(AUDIT_YEARS)
    listing_eligible = samples["listing_observations"] >= MIN_LISTING_OBSERVATIONS
    liquidity_eligible = (
        samples["average_turnover_cny_20"] >= MIN_AVERAGE_TURNOVER_CNY
    )
    recent_eligible = samples["recent_observations_20"] >= MIN_RECENT_OBSERVATIONS
    non_st = ~samples["historical_is_st"]
    finite_targets = np.isfinite(
        samples[["v5_net_r", "v5_tail_loss_r"]].to_numpy(dtype="float32")
    ).all(axis=1)
    eligible = (
        date_eligible
        & listing_eligible
        & liquidity_eligible
        & recent_eligible
        & non_st
        & finite_features
        & finite_targets
    )
    filter_counts = {
        "joined_strict_label_rows": int(after_join),
        "audit_year_rows": int(date_eligible.sum()),
        "listing_rows": int((date_eligible & listing_eligible).sum()),
        "liquidity_rows": int((date_eligible & listing_eligible & liquidity_eligible).sum()),
        "recent_observation_rows": int(
            (date_eligible & listing_eligible & liquidity_eligible & recent_eligible).sum()
        ),
        "finite_feature_and_target_rows": int(eligible.sum()),
    }
    samples = samples.loc[eligible].copy()
    samples = samples.rename(columns={"trade_date": "signal_date"})
    samples = samples.sort_values(["signal_date", "instrument_id"]).reset_index(drop=True)
    audit = {
        "feature_start": str(feature_start.date()),
        "label_file": str(label_path),
        "label_rows": int(len(labels)),
        "duplicate_label_keys": duplicate_labels,
        "missing_turnover_metric_rows": missing_turnover,
        "eligible_rows": int(len(samples)),
        "eligible_instruments": int(samples["instrument_id"].nunique()),
        "eligible_signal_dates": int(samples["signal_date"].nunique()),
        "rows_by_year": {
            str(year): int((samples["signal_date"].dt.year == year).sum())
            for year in AUDIT_YEARS
        },
        "signal_dates_by_year": {
            str(year): int(
                samples.loc[samples["signal_date"].dt.year == year, "signal_date"].nunique()
            )
            for year in AUDIT_YEARS
        },
        "filters": filter_counts,
        "panel": panel_audit,
        "point_in_time_states": point_state_audit,
        "calendar_end": str(calendar["calendar_date"].max().date()),
    }
    return samples, audit


def audit_daily_cross_sections(
    samples: pd.DataFrame,
    minimum_size: int = MIN_CROSS_SECTION,
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    total_dates = samples["signal_date"].nunique()
    for date_number, (signal_date, group) in enumerate(
        samples.groupby("signal_date", sort=True), start=1
    ):
        if len(group) < minimum_size:
            continue
        feature_ranks = group[FEATURES].rank(pct=True, method="average")
        feature_values = feature_ranks.to_numpy(dtype=float)
        feature_centered = feature_values - feature_values.mean(axis=0)
        feature_sums = np.square(feature_centered).sum(axis=0)
        for target_name, target_spec in TARGETS.items():
            raw_target = group[target_spec["column"]].to_numpy(dtype=float)
            score = raw_target * float(target_spec["score_sign"])
            target_rank = pd.Series(score).rank(pct=True, method="average").to_numpy()
            target_centered = target_rank - target_rank.mean()
            denominator = np.sqrt(feature_sums * np.square(target_centered).sum())
            numerator = (feature_centered * target_centered[:, None]).sum(axis=0)
            correlations = np.divide(
                numerator,
                denominator,
                out=np.full(len(FEATURES), np.nan, dtype=float),
                where=denominator > 0,
            )
            universe_score = float(np.mean(score))
            universe_median = float(np.median(score))
            for column, feature in enumerate(FEATURES):
                ranks = feature_values[:, column]
                raw_feature = group[feature].to_numpy(dtype=float)
                unique_values = np.unique(raw_feature)
                if len(unique_values) <= 10:
                    high = raw_feature == unique_values[-1]
                    low = raw_feature == unique_values[0]
                else:
                    high = ranks >= 0.90
                    low = ranks <= 0.10
                records.append(
                    {
                        "signal_date": signal_date,
                        "year": int(signal_date.year),
                        "target": target_name,
                        "feature": feature,
                        "rows": int(len(group)),
                        "rank_ic": float(correlations[column]),
                        "universe_score_mean": universe_score,
                        "universe_score_median": universe_median,
                        "high_decile_score_mean": float(np.mean(score[high])),
                        "high_decile_score_median": float(np.median(score[high])),
                        "high_decile_rows": int(high.sum()),
                        "low_decile_score_mean": float(np.mean(score[low])),
                        "low_decile_score_median": float(np.median(score[low])),
                        "low_decile_rows": int(low.sum()),
                    }
                )
        if date_number % 200 == 0 or date_number == total_dates:
            log(f"Cross-section audit {date_number}/{total_dates} dates")
    daily = pd.DataFrame(records)
    daily["high_decile_score_lift"] = (
        daily["high_decile_score_mean"] - daily["universe_score_mean"]
    )
    daily["low_decile_score_lift"] = (
        daily["low_decile_score_mean"] - daily["universe_score_mean"]
    )
    daily["high_decile_median_lift"] = (
        daily["high_decile_score_median"] - daily["universe_score_median"]
    )
    daily["low_decile_median_lift"] = (
        daily["low_decile_score_median"] - daily["universe_score_median"]
    )
    return daily


def aggregate_yearly(daily: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for (target, feature, year), group in daily.groupby(
        ["target", "feature", "year"], sort=False
    ):
        mean_ic, statistic, p_value = newey_west_mean_test(group["rank_ic"])
        records.append(
            {
                "target": target,
                "feature": feature,
                "year": int(year),
                "signal_dates": int(group["signal_date"].nunique()),
                "average_cross_section_rows": float(group["rows"].mean()),
                "ic_mean": mean_ic,
                "ic_median": float(group["rank_ic"].median()),
                "ic_positive_rate": float((group["rank_ic"] > 0).mean()),
                "ic_hac_t": statistic,
                "ic_hac_p": p_value,
                "high_decile_score_lift_mean": float(
                    group["high_decile_score_lift"].mean()
                ),
                "high_decile_score_lift_median": float(
                    group["high_decile_median_lift"].mean()
                ),
                "low_decile_score_lift_mean": float(
                    group["low_decile_score_lift"].mean()
                ),
                "low_decile_score_lift_median": float(
                    group["low_decile_median_lift"].mean()
                ),
            }
        )
    return pd.DataFrame(records).sort_values(["target", "feature", "year"])


def build_stability(
    daily: pd.DataFrame,
    yearly: pd.DataFrame,
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for (target, feature), group in yearly.groupby(["target", "feature"], sort=False):
        by_year = group.set_index("year")
        if any(year not in by_year.index for year in AUDIT_YEARS):
            continue
        discovery_ic = float(by_year.loc[DISCOVERY_YEAR, "ic_mean"])
        direction_sign = 1.0 if discovery_ic >= 0 else -1.0
        direction = "HIGH" if direction_sign > 0 else "LOW"
        daily_pair = daily.loc[
            (daily["target"] == target) & (daily["feature"] == feature)
        ].copy()
        validation_daily = daily_pair.loc[
            daily_pair["year"].isin(VALIDATION_YEARS), "rank_ic"
        ].to_numpy(dtype=float) * direction_sign
        validation_mean, validation_t, validation_p = newey_west_mean_test(
            validation_daily
        )
        row: dict[str, Any] = {
            "target": target,
            "feature": feature,
            "direction": direction,
            "direction_source": "2022 rank IC sign",
            "validation_directed_ic_mean": validation_mean,
            "validation_hac_t": validation_t,
            "validation_hac_p": validation_p,
        }
        validation_lifts: list[float] = []
        validation_median_lifts: list[float] = []
        for year in AUDIT_YEARS:
            annual = by_year.loc[year]
            directed_ic = direction_sign * float(annual["ic_mean"])
            if direction_sign > 0:
                lift = float(annual["high_decile_score_lift_mean"])
                median_lift = float(annual["high_decile_score_lift_median"])
            else:
                lift = float(annual["low_decile_score_lift_mean"])
                median_lift = float(annual["low_decile_score_lift_median"])
            row[f"directed_ic_{year}"] = directed_ic
            row[f"favorable_decile_lift_{year}"] = lift
            row[f"favorable_decile_median_lift_{year}"] = median_lift
            if year in VALIDATION_YEARS:
                validation_lifts.append(lift)
                validation_median_lifts.append(median_lift)
        row["validation_favorable_lift_mean"] = float(np.mean(validation_lifts))
        row["validation_favorable_median_lift_mean"] = float(
            np.mean(validation_median_lifts)
        )
        row["validation_ic_positive_years"] = int(
            sum(row[f"directed_ic_{year}"] > 0 for year in VALIDATION_YEARS)
        )
        row["validation_lift_positive_years"] = int(
            sum(row[f"favorable_decile_lift_{year}"] > 0 for year in VALIDATION_YEARS)
        )
        row["holdout_ic_positive_years"] = int(
            sum(row[f"directed_ic_{year}"] > 0 for year in HOLDOUT_YEARS)
        )
        row["holdout_lift_positive_years"] = int(
            sum(row[f"favorable_decile_lift_{year}"] > 0 for year in HOLDOUT_YEARS)
        )
        records.append(row)
    result = pd.DataFrame(records)
    result["validation_fdr_q"] = benjamini_hochberg(result["validation_hac_p"])
    result["validation_pass"] = (
        (result["validation_ic_positive_years"] == len(VALIDATION_YEARS))
        & (result["validation_directed_ic_mean"] >= MIN_VALIDATION_DIRECTED_IC)
        & (result["validation_fdr_q"] <= MAX_VALIDATION_FDR)
        & (result["validation_lift_positive_years"] == len(VALIDATION_YEARS))
        & (result["validation_favorable_lift_mean"] > 0)
    )
    result["holdout_consistent"] = (
        (result["holdout_ic_positive_years"] == len(HOLDOUT_YEARS))
        & (result["holdout_lift_positive_years"] == len(HOLDOUT_YEARS))
    )
    return result.sort_values(
        ["validation_pass", "validation_fdr_q", "validation_directed_ic_mean"],
        ascending=[False, True, False],
    ).reset_index(drop=True)


def compact_row(row: pd.Series) -> dict[str, Any]:
    columns = [
        "target",
        "feature",
        "direction",
        "directed_ic_2022",
        "directed_ic_2023",
        "directed_ic_2024",
        "validation_directed_ic_mean",
        "validation_hac_p",
        "validation_fdr_q",
        "favorable_decile_lift_2023",
        "favorable_decile_lift_2024",
        "directed_ic_2025",
        "directed_ic_2026",
        "favorable_decile_lift_2025",
        "favorable_decile_lift_2026",
        "validation_pass",
        "holdout_consistent",
    ]
    return {column: row[column] for column in columns}


def write_report(path: Path, summary: dict[str, Any]) -> None:
    rows = [
        "# 严格V5逐特征年度信息审计",
        "",
        "## 结论",
        "",
        f"**{summary['decision']}**",
        "",
        summary["decision_detail"],
        "",
        "## 审计口径",
        "",
        "- 目标一：费用后可执行净R，越高越好。",
        "- 目标二：实际可卖退出路径尾部损失R，转换为负损失后审计，越高越好。",
        "- 2022年只确定每个特征方向；2023—2024验证；2025和2026截至数据水位只作旁证。",
        "- 日截面使用Spearman Rank IC；均值显著性使用20阶Newey-West标准误。",
        "- 对34个“目标×特征”验证假设统一做Benjamini-Hochberg校正。",
        "- 不训练模型、不组合特征、不使用2025+结果改变方向或门槛。",
        "",
        "## 样本",
        "",
        f"- 最终候选行：{summary['sample_audit']['eligible_rows']:,}。",
        f"- 证券数：{summary['sample_audit']['eligible_instruments']:,}；信号日：{summary['sample_audit']['eligible_signal_dates']:,}。",
        f"- 逐日截面记录：{summary['daily_metric_rows']:,}。",
        "",
        "## 排名前列",
        "",
        "| 目标 | 特征 | 方向 | IC22 | IC23 | IC24 | 验证IC | q值 | 分位增益23 | 分位增益24 | IC25 | IC26 | 通过 | 留出一致 |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for item in summary["top_rows"]:
        rows.append(
            f"| {item['target']} | {item['feature']} | {item['direction']} | "
            f"{item['directed_ic_2022']:.4f} | {item['directed_ic_2023']:.4f} | "
            f"{item['directed_ic_2024']:.4f} | {item['validation_directed_ic_mean']:.4f} | "
            f"{item['validation_fdr_q']:.4f} | {item['favorable_decile_lift_2023']:.4f} | "
            f"{item['favorable_decile_lift_2024']:.4f} | {item['directed_ic_2025']:.4f} | "
            f"{item['directed_ic_2026']:.4f} | "
            f"{'是' if item['validation_pass'] else '否'} | "
            f"{'是' if item['holdout_consistent'] else '否'} |"
        )
    rows.extend(
        [
            "",
            "## 正式验证门槛",
            "",
            "同时满足：2023和2024定向IC均为正；验证期合并定向IC均值不低于0.01；34项BH校正q值不高于0.10；两年优势分位平均目标增益均为正。2025与2026不参与通过判定。",
            "",
            "## 下一步",
            "",
            summary["next_step"],
        ]
    )
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Strict V5 feature information audit")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--label-file", default=None)
    parser.add_argument("--state-audit", default=None)
    parser.add_argument("--label-summary", default=None)
    parser.add_argument("--output-dir", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    script_dir = Path(__file__).resolve().parent
    config = load_config(resolve_from(script_dir, args.config))
    data_root = resolve_from(script_dir, str(config["data_root"]))
    output_root = resolve_from(script_dir, str(config["output_dir"]))
    output_dir = writable_output_dir(
        Path(args.output_dir).resolve()
        if args.output_dir
        else output_root / "v5_feature_audit"
    )
    label_path = (
        Path(args.label_file).resolve()
        if args.label_file
        else output_root / "v5_tail_label_audit" / "v5_tail_labels.parquet"
    )
    state_audit_path = (
        Path(args.state_audit).resolve()
        if args.state_audit
        else output_root / "data_state_audit" / "summary.json"
    )
    label_summary_path = (
        Path(args.label_summary).resolve()
        if args.label_summary
        else output_root / "v5_tail_label_audit" / "summary.json"
    )
    state_audit = json.loads(state_audit_path.read_text(encoding="utf-8"))
    label_summary = json.loads(label_summary_path.read_text(encoding="utf-8"))
    if not state_audit.get("ready_for_v5_labels"):
        raise RuntimeError("Historical state audit is not READY")
    if label_summary.get("decision") != "READY_FOR_FEATURE_AUDIT":
        raise RuntimeError("Strict V5 label audit is not READY_FOR_FEATURE_AUDIT")
    if not label_path.exists():
        raise FileNotFoundError(label_path)

    samples, sample_audit = load_samples(
        data_root,
        pd.Timestamp(str(config["feature_start"])),
        label_path,
    )
    log(f"Eligible strict V5 feature rows: {len(samples):,}")
    daily = audit_daily_cross_sections(samples)
    yearly = aggregate_yearly(daily)
    stability = build_stability(daily, yearly)
    passed = stability.loc[stability["validation_pass"]].copy()
    if passed.empty:
        decision = "STOP_EXISTING_17_FEATURES"
        decision_detail = (
            "现有17项价格量特征没有任何一项在严格V5目标上通过预先冻结的验证门槛；"
            "不得进入模型训练或继续围绕这些特征调参。"
        )
        next_step = (
            "停止现有17项特征路线。若继续研究，必须先提出独立的新信息源（例如行业中性、"
            "估值/盈利质量、公告事件或更细粒度成交结构），并对新增特征重新执行同一审计。"
        )
    else:
        decision = "LIMITED_FEATURES_PASS_VALIDATION"
        decision_detail = (
            f"共有{len(passed)}个目标×特征组合通过2023—2024验证门槛；"
            "这只是进入简单、冻结方向组合验证的最低条件，不是模型或交易结论。"
        )
        next_step = (
            "只允许对通过项做冻结方向的简单等权/线性组合和配对随机检验；"
            "不得使用2025+重新筛选特征，也不直接训练LightGBM。"
        )
    top_rows: list[dict[str, Any]] = []
    for target in TARGETS:
        target_rows = stability.loc[stability["target"] == target].head(8)
        top_rows.extend(compact_row(row) for _, row in target_rows.iterrows())
    summary = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "decision": decision,
        "decision_detail": decision_detail,
        "next_step": next_step,
        "features": FEATURES,
        "targets": TARGETS,
        "hypotheses": len(FEATURES) * len(TARGETS),
        "discovery_year": DISCOVERY_YEAR,
        "validation_years": list(VALIDATION_YEARS),
        "holdout_years": list(HOLDOUT_YEARS),
        "validation_rule": {
            "directed_ic_positive_each_validation_year": True,
            "minimum_validation_directed_ic_mean": MIN_VALIDATION_DIRECTED_IC,
            "maximum_bh_fdr_q": MAX_VALIDATION_FDR,
            "favorable_decile_lift_positive_each_validation_year": True,
            "hac_lag": HAC_LAG,
        },
        "qualified_count": int(len(passed)),
        "qualified": [compact_row(row) for _, row in passed.iterrows()],
        "top_rows": top_rows,
        "sample_audit": sample_audit,
        "daily_metric_rows": int(len(daily)),
        "state_audit_decision": state_audit.get("decision"),
        "label_audit_decision": label_summary.get("decision"),
        "no_model_trained": True,
        "holdout_not_used_for_selection": True,
    }
    daily.to_parquet(
        output_dir / "daily_rank_ic.parquet", index=False, compression="zstd"
    )
    yearly.to_csv(
        output_dir / "yearly_feature_metrics.csv", index=False, encoding="utf-8-sig"
    )
    stability.to_csv(
        output_dir / "feature_stability.csv", index=False, encoding="utf-8-sig"
    )
    write_json(output_dir / "summary.json", summary)
    write_report(output_dir / "report.md", summary)
    log(f"Decision: {decision}")
    log(f"Outputs: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
