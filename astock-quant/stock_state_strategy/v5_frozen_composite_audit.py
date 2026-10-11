from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backtest_mfe_mae import load_config, resolve_from
from v5_feature_information_audit import (
    AUDIT_YEARS,
    HOLDOUT_YEARS,
    TARGETS,
    benjamini_hochberg,
    json_value,
    load_samples,
    log,
    newey_west_mean_test,
    writable_output_dir,
    write_json,
)


RANDOM_SEED = 20260817
RANDOM_RUNS = 200
TOP_FRACTION = 0.10
MIN_HOLDOUT_DIRECTED_IC = 0.01
MAX_HOLDOUT_FDR = 0.10
MAX_ANNUAL_RANDOM_P = 0.05

# Frozen after the 2022 direction / 2023-2024 validation audit.  Every validated
# item is retained; no 2025+ result is allowed to add, remove, or reweight one.
FROZEN_COMPOSITES: dict[str, tuple[tuple[str, str], ...]] = {
    "net_r": (
        ("turnover_ratio_20", "LOW"),
        ("volume_ratio_20", "LOW"),
        ("amount_ratio_20", "LOW"),
        ("vol_ratio_5_20", "LOW"),
        ("close_ma20_ratio", "LOW"),
    ),
    "tail_loss_r": (
        ("turnover_ratio_20", "LOW"),
        ("volume_ratio_20", "LOW"),
        ("amount_ratio_20", "LOW"),
        ("dist_high20", "LOW"),
        ("vol_ratio_5_20", "LOW"),
        ("ret_5", "LOW"),
        ("near_high20_flag", "LOW"),
        ("close_ma20_ratio", "LOW"),
    ),
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def frozen_spec_records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for target, items in FROZEN_COMPOSITES.items():
        weight = 1.0 / len(items)
        for feature, direction in items:
            records.append(
                {
                    "target": target,
                    "feature": feature,
                    "direction": direction,
                    "weight": weight,
                    "selection_source": "2022 direction and 2023-2024 validation",
                }
            )
    return records


def validate_frozen_specs(stability: pd.DataFrame) -> None:
    required = {"target", "feature", "direction", "validation_pass"}
    missing = required.difference(stability.columns)
    if missing:
        raise ValueError(f"feature stability is missing columns: {sorted(missing)}")
    passed = stability.loc[
        stability["validation_pass"].astype(str).str.lower().eq("true")
    ]
    observed = {
        (str(row.target), str(row.feature), str(row.direction))
        for row in passed.itertuples(index=False)
    }
    frozen = {
        (target, feature, direction)
        for target, items in FROZEN_COMPOSITES.items()
        for feature, direction in items
    }
    if observed != frozen:
        missing_frozen = sorted(observed.difference(frozen))
        unexpected_frozen = sorted(frozen.difference(observed))
        raise RuntimeError(
            "frozen composite does not match validation-passed feature set; "
            f"missing_from_code={missing_frozen}, unexpected_in_code={unexpected_frozen}"
        )


def directed_composite_score(
    group: pd.DataFrame,
    items: tuple[tuple[str, str], ...],
) -> np.ndarray:
    features = [feature for feature, _ in items]
    ranks = group[features].rank(pct=True, method="average").to_numpy(
        dtype=float, copy=True
    )
    for column, (_, direction) in enumerate(items):
        if direction == "LOW":
            ranks[:, column] = 1.0 - ranks[:, column]
        elif direction != "HIGH":
            raise ValueError(f"unknown direction: {direction}")
    return ranks.mean(axis=1)


def deterministic_top_indices(
    scores: np.ndarray,
    instrument_ids: np.ndarray,
    fraction: float = TOP_FRACTION,
) -> np.ndarray:
    if not 0 < fraction <= 1:
        raise ValueError("fraction must be in (0, 1]")
    count = max(1, int(math.ceil(len(scores) * fraction)))
    order = np.lexsort((instrument_ids.astype(str), -np.asarray(scores, dtype=float)))
    return order[:count]


def audit_daily_composites(
    samples: pd.DataFrame,
    random_runs: int = RANDOM_RUNS,
    seed: int = RANDOM_SEED,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if random_runs < 1:
        raise ValueError("random_runs must be positive")
    rng = np.random.default_rng(seed)
    daily_records: list[dict[str, Any]] = []
    random_sums: dict[tuple[str, int], np.ndarray] = {}
    random_date_counts: dict[tuple[str, int], int] = {}
    total_dates = samples["signal_date"].nunique()
    for date_number, (signal_date, group) in enumerate(
        samples.groupby("signal_date", sort=True), start=1
    ):
        year = int(signal_date.year)
        rows = len(group)
        if rows < 2:
            continue
        ids = group["instrument_id"].astype(str).to_numpy()
        selected_count = max(1, int(math.ceil(rows * TOP_FRACTION)))
        target_scores = {
            target: group[spec["column"]].to_numpy(dtype=float)
            * float(spec["score_sign"])
            for target, spec in TARGETS.items()
        }
        universe_means = {
            target: float(values.mean()) for target, values in target_scores.items()
        }

        random_lifts = {
            target: np.empty(random_runs, dtype=float) for target in TARGETS
        }
        for run in range(random_runs):
            random_positions = rng.choice(rows, size=selected_count, replace=False)
            for target, values in target_scores.items():
                random_lifts[target][run] = (
                    float(values[random_positions].mean()) - universe_means[target]
                )
        for target in TARGETS:
            key = (target, year)
            if key not in random_sums:
                random_sums[key] = np.zeros(random_runs, dtype=float)
                random_date_counts[key] = 0
            random_sums[key] += random_lifts[target]
            random_date_counts[key] += 1

        for target, items in FROZEN_COMPOSITES.items():
            composite = directed_composite_score(group, items)
            selected = deterministic_top_indices(composite, ids)
            realized_score = target_scores[target]
            composite_rank = pd.Series(composite).rank(
                pct=True, method="average"
            ).to_numpy(dtype=float)
            realized_rank = pd.Series(realized_score).rank(
                pct=True, method="average"
            ).to_numpy(dtype=float)
            rank_ic = float(np.corrcoef(composite_rank, realized_rank)[0, 1])
            universe_median = float(np.median(realized_score))
            selected_mean = float(realized_score[selected].mean())
            selected_median = float(np.median(realized_score[selected]))
            daily_records.append(
                {
                    "signal_date": signal_date,
                    "year": year,
                    "target": target,
                    "rows": rows,
                    "selected_rows": int(len(selected)),
                    "feature_count": len(items),
                    "rank_ic": rank_ic,
                    "universe_score_mean": universe_means[target],
                    "universe_score_median": universe_median,
                    "selected_score_mean": selected_mean,
                    "selected_score_median": selected_median,
                    "selected_score_lift": selected_mean - universe_means[target],
                    "selected_median_lift": selected_median - universe_median,
                }
            )
        if date_number % 200 == 0 or date_number == total_dates:
            log(f"Frozen composite audit {date_number}/{total_dates} dates")

    random_records: list[dict[str, Any]] = []
    for (target, year), sums in sorted(random_sums.items()):
        count = random_date_counts[(target, year)]
        for run, value in enumerate(sums / count):
            random_records.append(
                {
                    "target": target,
                    "year": year,
                    "run": run,
                    "annual_daily_mean_lift": float(value),
                    "signal_dates": count,
                    "seed": seed,
                }
            )
    return pd.DataFrame(daily_records), pd.DataFrame(random_records)


def aggregate_yearly(
    daily: pd.DataFrame,
    random_baseline: pd.DataFrame,
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for (target, year), group in daily.groupby(["target", "year"], sort=False):
        ic_mean, ic_t, ic_p = newey_west_mean_test(group["rank_ic"])
        lift_mean, lift_t, lift_p = newey_west_mean_test(
            group["selected_score_lift"]
        )
        random_values = random_baseline.loc[
            (random_baseline["target"] == target)
            & (random_baseline["year"] == year),
            "annual_daily_mean_lift",
        ].to_numpy(dtype=float)
        empirical_p = float(
            (1 + np.count_nonzero(random_values >= lift_mean))
            / (len(random_values) + 1)
        )
        records.append(
            {
                "target": target,
                "year": int(year),
                "signal_dates": int(group["signal_date"].nunique()),
                "average_cross_section_rows": float(group["rows"].mean()),
                "average_selected_rows": float(group["selected_rows"].mean()),
                "rank_ic_mean": ic_mean,
                "rank_ic_hac_t": ic_t,
                "rank_ic_hac_p": ic_p,
                "selected_score_lift_mean": lift_mean,
                "selected_score_lift_hac_t": lift_t,
                "selected_score_lift_hac_p": lift_p,
                "selected_median_lift_mean": float(
                    group["selected_median_lift"].mean()
                ),
                "random_lift_p05": float(np.quantile(random_values, 0.05)),
                "random_lift_median": float(np.median(random_values)),
                "random_lift_p95": float(np.quantile(random_values, 0.95)),
                "random_percentile": float((random_values < lift_mean).mean()),
                "random_empirical_p": empirical_p,
            }
        )
    return pd.DataFrame(records).sort_values(["target", "year"]).reset_index(drop=True)


def build_holdout_decision(
    daily: pd.DataFrame,
    yearly: pd.DataFrame,
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for target, group in daily.groupby("target", sort=False):
        annual = yearly.loc[yearly["target"] == target].set_index("year")
        if any(year not in annual.index for year in AUDIT_YEARS):
            raise ValueError(f"{target} is missing an audit year")
        holdout_daily = group.loc[group["year"].isin(HOLDOUT_YEARS)]
        pooled_ic, pooled_t, pooled_p = newey_west_mean_test(
            holdout_daily["rank_ic"]
        )
        row: dict[str, Any] = {
            "target": target,
            "feature_count": len(FROZEN_COMPOSITES[target]),
            "holdout_directed_ic_mean": pooled_ic,
            "holdout_ic_hac_t": pooled_t,
            "holdout_ic_hac_p": pooled_p,
        }
        for year in AUDIT_YEARS:
            row[f"rank_ic_{year}"] = float(annual.loc[year, "rank_ic_mean"])
            row[f"selected_lift_{year}"] = float(
                annual.loc[year, "selected_score_lift_mean"]
            )
            row[f"random_percentile_{year}"] = float(
                annual.loc[year, "random_percentile"]
            )
            row[f"random_empirical_p_{year}"] = float(
                annual.loc[year, "random_empirical_p"]
            )
        row["holdout_ic_positive_years"] = int(
            sum(row[f"rank_ic_{year}"] > 0 for year in HOLDOUT_YEARS)
        )
        row["holdout_lift_positive_years"] = int(
            sum(row[f"selected_lift_{year}"] > 0 for year in HOLDOUT_YEARS)
        )
        row["holdout_random_pass_years"] = int(
            sum(
                row[f"random_empirical_p_{year}"] <= MAX_ANNUAL_RANDOM_P
                for year in HOLDOUT_YEARS
            )
        )
        records.append(row)
    result = pd.DataFrame(records)
    result["holdout_ic_fdr_q"] = benjamini_hochberg(result["holdout_ic_hac_p"])
    result["holdout_pass"] = (
        (result["holdout_ic_positive_years"] == len(HOLDOUT_YEARS))
        & (result["holdout_lift_positive_years"] == len(HOLDOUT_YEARS))
        & (result["holdout_random_pass_years"] == len(HOLDOUT_YEARS))
        & (result["holdout_directed_ic_mean"] >= MIN_HOLDOUT_DIRECTED_IC)
        & (result["holdout_ic_fdr_q"] <= MAX_HOLDOUT_FDR)
    )
    return result.sort_values("target").reset_index(drop=True)


def write_report(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# 严格V5冻结等权组合审计",
        "",
        "## 结论",
        "",
        f"**{summary['decision']}**",
        "",
        summary["decision_detail"],
        "",
        "## 冻结规则",
        "",
        "- 组合项只来自2022定方向、2023—2024验证通过的单特征；所有通过项等权保留。",
        "- 2025与2026 YTD是本轮正式留出评价；没有据此删除特征、改变方向或调整权重。",
        f"- 每日选组合分数最高的{TOP_FRACTION:.0%}，与同日、同数量随机组做{summary['random_runs']}次配对比较。",
        "- 目标分数为费用后净R，或尾部损失R的相反数；本轮不训练模型、不模拟资金曲线。",
        "",
        "## 留出结果",
        "",
        "| 目标 | 特征数 | IC25 | IC26 | 增益25 | 增益26 | 随机百分位25 | 随机百分位26 | 合并IC | q值 | 通过 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in summary["holdout_results"]:
        lines.append(
            f"| {row['target']} | {row['feature_count']} | {row['rank_ic_2025']:.4f} | "
            f"{row['rank_ic_2026']:.4f} | {row['selected_lift_2025']:.4f} | "
            f"{row['selected_lift_2026']:.4f} | {row['random_percentile_2025']:.1%} | "
            f"{row['random_percentile_2026']:.1%} | {row['holdout_directed_ic_mean']:.4f} | "
            f"{row['holdout_ic_fdr_q']:.4g} | {'是' if row['holdout_pass'] else '否'} |"
        )
    lines.extend(
        [
            "",
            "## 通过门槛",
            "",
            "2025和2026两个切片的IC及Top10%日均目标增益都必须为正，两个年度相对配对随机的单侧经验p值均不高于0.05，合并留出IC不低于0.01，且两个组合的BH校正q值不高于0.10。",
            "",
            "## 下一步",
            "",
            summary["next_step"],
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Strict V5 frozen composite audit")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--label-file", default=None)
    parser.add_argument("--feature-audit-dir", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--random-runs", type=int, default=RANDOM_RUNS)
    parser.add_argument("--seed", type=int, default=RANDOM_SEED)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    script_dir = Path(__file__).resolve().parent
    config = load_config(resolve_from(script_dir, args.config))
    data_root = resolve_from(script_dir, str(config["data_root"]))
    output_root = resolve_from(script_dir, str(config["output_dir"]))
    feature_audit_dir = (
        Path(args.feature_audit_dir).resolve()
        if args.feature_audit_dir
        else output_root / "v5_feature_audit"
    )
    output_dir = writable_output_dir(
        Path(args.output_dir).resolve()
        if args.output_dir
        else output_root / "v5_frozen_composite_audit"
    )
    label_path = (
        Path(args.label_file).resolve()
        if args.label_file
        else output_root / "v5_tail_label_audit" / "v5_tail_labels.parquet"
    )
    feature_summary_path = feature_audit_dir / "summary.json"
    stability_path = feature_audit_dir / "feature_stability.csv"
    feature_summary = json.loads(feature_summary_path.read_text(encoding="utf-8"))
    if feature_summary.get("decision") != "LIMITED_FEATURES_PASS_VALIDATION":
        raise RuntimeError("feature audit did not authorize a frozen combination audit")
    if not feature_summary.get("holdout_not_used_for_selection"):
        raise RuntimeError("feature audit does not certify holdout isolation")
    stability = pd.read_csv(stability_path)
    validate_frozen_specs(stability)

    samples, sample_audit = load_samples(
        data_root,
        pd.Timestamp(str(config["feature_start"])),
        label_path,
    )
    log(f"Eligible strict V5 composite rows: {len(samples):,}")
    daily, random_baseline = audit_daily_composites(
        samples,
        random_runs=int(args.random_runs),
        seed=int(args.seed),
    )
    yearly = aggregate_yearly(daily, random_baseline)
    holdout = build_holdout_decision(daily, yearly)
    passed_targets = set(holdout.loc[holdout["holdout_pass"], "target"])
    if passed_targets == set(FROZEN_COMPOSITES):
        decision = "BOTH_COMPOSITES_PASS_HOLDOUT"
        decision_detail = (
            "净收益与尾部风险两个冻结等权组合都通过2025—2026留出门槛；"
            "这只授权下一轮预先冻结的规则级审计，不代表可交易。"
        )
        next_step = (
            "可另立一次受控规则审计，预先固定如何同时使用两个分数；"
            "在该规则冻结前不得查看更多留出变体，也不得直接训练LightGBM。"
        )
    elif passed_targets == {"tail_loss_r"}:
        decision = "TAIL_RISK_COMPOSITE_ONLY"
        decision_detail = (
            "只有尾部风险组合通过留出门槛；净收益组合未通过，现有17项特征不能形成"
            "可靠的收益排序器。风险组合只能保留为候选风险控制信息。"
        )
        next_step = (
            "停止现有17项特征的收益建模与LightGBM路线。若继续，只能为尾部风险组合"
            "预先定义单一风险过滤规则，并等待新的前向数据验证；不得用本轮留出期调阈值。"
        )
    elif passed_targets == {"net_r"}:
        decision = "NET_RETURN_COMPOSITE_ONLY"
        decision_detail = (
            "只有净收益组合通过留出门槛，尾部风险组合未通过；当前仍缺乏可接受的"
            "可执行尾部风险控制，不能进入交易回测。"
        )
        next_step = "停止交易化推进；先引入独立尾部风险信息源并重新审计。"
    else:
        decision = "STOP_FROZEN_COMPOSITES"
        decision_detail = (
            "两个冻结等权组合均未通过2025—2026留出门槛；现有17项特征路线停止。"
        )
        next_step = "不得训练模型；如继续研究，必须提出独立的新信息源并重新执行特征审计。"

    summary = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "decision": decision,
        "decision_detail": decision_detail,
        "next_step": next_step,
        "frozen_composites": frozen_spec_records(),
        "top_fraction": TOP_FRACTION,
        "random_runs": int(args.random_runs),
        "random_seed": int(args.seed),
        "holdout_years": list(HOLDOUT_YEARS),
        "holdout_rule": {
            "directed_ic_positive_each_holdout_year": True,
            "selected_lift_positive_each_holdout_year": True,
            "maximum_annual_random_empirical_p": MAX_ANNUAL_RANDOM_P,
            "minimum_pooled_holdout_directed_ic": MIN_HOLDOUT_DIRECTED_IC,
            "maximum_bh_fdr_q_across_two_composites": MAX_HOLDOUT_FDR,
        },
        "holdout_results": [
            {column: json_value(value) for column, value in row.items()}
            for row in holdout.to_dict("records")
        ],
        "sample_audit": sample_audit,
        "daily_metric_rows": int(len(daily)),
        "random_baseline_rows": int(len(random_baseline)),
        "feature_audit_summary_sha256": file_sha256(feature_summary_path),
        "feature_stability_sha256": file_sha256(stability_path),
        "no_model_trained": True,
        "no_portfolio_simulated": True,
        "holdout_not_used_to_change_features_directions_or_weights": True,
    }
    daily.to_parquet(
        output_dir / "daily_composite_metrics.parquet", index=False, compression="zstd"
    )
    yearly.to_csv(
        output_dir / "yearly_composite_metrics.csv", index=False, encoding="utf-8-sig"
    )
    random_baseline.to_parquet(
        output_dir / "paired_random_baseline.parquet", index=False, compression="zstd"
    )
    holdout.to_csv(
        output_dir / "holdout_decision.csv", index=False, encoding="utf-8-sig"
    )
    pd.DataFrame(frozen_spec_records()).to_csv(
        output_dir / "frozen_composite_specs.csv", index=False, encoding="utf-8-sig"
    )
    write_json(output_dir / "summary.json", summary)
    write_report(output_dir / "report.md", summary)
    log(f"Decision: {decision}")
    log(f"Outputs: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
