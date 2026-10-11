"""Frozen LightGBM test: do ownership features add value over market controls?"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from ownership_pipeline import COMPOSITION_FACTORS, HOLDER_FACTORS, ROOT, load_config, resolve


CONTROL_FEATURES = (
    "market_cap_cny",
    "float_market_cap_cny",
    "turnover_rate_pct",
    "free_float_turnover_rate_pct",
    "price_earnings_ttm",
    "price_book",
    "ret_20",
    "ret_60",
    "volatility_20",
    "average_turnover_cny_20",
    "listing_sessions",
)
OWNERSHIP_FEATURES = tuple(
    factor
    for factor in (*COMPOSITION_FACTORS, *HOLDER_FACTORS)
    if factor not in {"holder_num", "average_holding_shares"}
)


def prepare_samples(config: dict, events: pd.DataFrame, horizon: int) -> pd.DataFrame:
    target = f"future_{horizon}d_net_return"
    frame = events.copy()
    frame["ann_date"] = pd.to_datetime(frame["ann_date"])
    frame = frame[
        frame["new_composition_disclosure"].fillna(False).astype(bool)
        & frame["composition_quality_valid"].fillna(False).astype(bool)
        & frame[target].notna()
        & frame["holder_num"].notna()
        & (frame["listing_sessions"] >= int(config["minimum_listing_sessions"]))
        & (
            frame["average_turnover_cny_20"]
            >= float(config["minimum_average_turnover_cny"])
        )
    ].copy()
    frame["log_market_cap"] = np.log(frame["market_cap_cny"].clip(lower=1.0))
    frame["log_float_market_cap"] = np.log(
        frame["float_market_cap_cny"].clip(lower=1.0)
    )
    frame["log_average_holding_value"] = np.log(
        frame["average_holding_value_cny"].clip(lower=1.0)
    )
    frame = frame.replace([np.inf, -np.inf], np.nan)
    frame["industry_vendor"] = (
        frame["industry_vendor"].astype("object").where(frame["industry_vendor"].notna(), "UNKNOWN")
    )
    frame["industry_vendor"] = frame["industry_vendor"].map(str).astype("object")
    numeric_candidates = set(CONTROL_FEATURES) | set(OWNERSHIP_FEATURES) | {
        "log_market_cap",
        "log_float_market_cap",
        "log_average_holding_value",
        target,
    }
    for column in numeric_candidates.intersection(frame.columns):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").to_numpy(
            dtype=float, na_value=np.nan
        )
    return frame.sort_values(["ann_date", "vendor_symbol"], kind="stable").reset_index(
        drop=True
    )


def make_model(numeric_features: list[str], seed: int) -> Pipeline:
    transformer = ColumnTransformer(
        [
            (
                "numeric",
                SimpleImputer(strategy="median", add_indicator=True),
                numeric_features,
            ),
            (
                "industry",
                OneHotEncoder(handle_unknown="ignore", min_frequency=10),
                ["industry_vendor"],
            ),
        ],
        remainder="drop",
    )
    estimator = lgb.LGBMRegressor(
        objective="regression_l1",
        n_estimators=300,
        learning_rate=0.03,
        num_leaves=15,
        max_depth=4,
        min_child_samples=100,
        subsample=0.8,
        colsample_bytree=0.8,
        reg_alpha=0.2,
        reg_lambda=1.0,
        random_state=seed,
        n_jobs=-1,
        verbosity=-1,
    )
    return Pipeline([("features", transformer), ("model", estimator)])


def cohort_selection(
    sample: pd.DataFrame,
    prediction_column: str,
    target: str,
    minimum_cross_section: int,
) -> pd.DataFrame:
    selected: list[pd.DataFrame] = []
    for _, group in sample.groupby("ann_date", sort=True):
        work = group[["ann_date", "vendor_symbol", prediction_column, target]].dropna()
        if len(work) < minimum_cross_section:
            continue
        count = max(1, int(np.ceil(len(work) * 0.2)))
        chosen = work.nlargest(count, prediction_column).copy()
        chosen["cohort_size"] = len(work)
        selected.append(chosen)
    return pd.concat(selected, ignore_index=True) if selected else pd.DataFrame()


def paired_random_distribution(
    sample: pd.DataFrame,
    target: str,
    prediction_column: str,
    minimum_cross_section: int,
    runs: int,
    seed: int,
) -> np.ndarray:
    groups = []
    for _, group in sample.groupby("ann_date", sort=True):
        work = group[[prediction_column, target]].dropna()
        if len(work) < minimum_cross_section:
            continue
        groups.append((work[target].to_numpy(dtype=float), max(1, int(np.ceil(len(work) * 0.2)))))
    rng = np.random.default_rng(seed)
    values = np.full(runs, np.nan)
    for run in range(runs):
        chosen = []
        for outcomes, count in groups:
            chosen.extend(rng.choice(outcomes, size=count, replace=False).tolist())
        if chosen:
            values[run] = float(np.mean(chosen))
    return values


def prediction_metrics(
    sample: pd.DataFrame,
    prediction_column: str,
    target: str,
    minimum_cross_section: int,
    seed: int,
) -> dict:
    valid = sample[[prediction_column, target]].dropna()
    overall_ic = (
        float(spearmanr(valid[prediction_column], valid[target]).statistic)
        if len(valid) >= 3
        else np.nan
    )
    daily_ic = []
    for _, group in sample.groupby("ann_date", sort=True):
        work = group[[prediction_column, target]].dropna()
        if len(work) >= minimum_cross_section and work[prediction_column].nunique() >= 3:
            daily_ic.append(
                float(spearmanr(work[prediction_column], work[target]).statistic)
            )
    selected = cohort_selection(sample, prediction_column, target, minimum_cross_section)
    selected_mean = float(selected[target].mean()) if not selected.empty else np.nan
    random_values = paired_random_distribution(
        sample,
        target,
        prediction_column,
        minimum_cross_section,
        200,
        seed,
    )
    random_valid = random_values[np.isfinite(random_values)]
    percentile = (
        float(np.mean(random_valid < selected_mean)) if len(random_valid) else np.nan
    )
    return {
        "sample_count": int(len(valid)),
        "cohort_selected_count": int(len(selected)),
        "overall_rank_ic": overall_ic,
        "mean_daily_rank_ic": float(np.mean(daily_ic)) if daily_ic else np.nan,
        "positive_daily_ic_rate": float(np.mean(np.asarray(daily_ic) > 0))
        if daily_ic
        else np.nan,
        "selected_mean_net_return": selected_mean,
        "all_event_mean_net_return": float(valid[target].mean()) if len(valid) else np.nan,
        "random_mean_median": float(np.median(random_valid))
        if len(random_valid)
        else np.nan,
        "random_percentile": percentile,
    }


def fit_and_score(
    frame: pd.DataFrame,
    config: dict,
    horizon: int,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    target = f"future_{horizon}d_net_return"
    discovery_end = pd.Timestamp(config["discovery_end"])
    validation_start = pd.Timestamp(config["validation_start"])
    validation_end = pd.Timestamp(config["validation_end"])
    holdout_start = pd.Timestamp(config["holdout_start"])
    controls = [feature for feature in CONTROL_FEATURES if feature in frame.columns]
    controls = [
        "log_market_cap" if value == "market_cap_cny" else value
        for value in controls
    ]
    controls = [
        "log_float_market_cap" if value == "float_market_cap_cny" else value
        for value in controls
    ]
    ownership = [feature for feature in OWNERSHIP_FEATURES if feature in frame.columns]
    ownership.append("log_average_holding_value")
    ownership = list(dict.fromkeys(ownership))

    discovery = frame[frame["ann_date"] <= discovery_end]
    validation = frame[
        (frame["ann_date"] >= validation_start) & (frame["ann_date"] <= validation_end)
    ].copy()
    holdout = frame[frame["ann_date"] >= holdout_start].copy()
    if min(len(discovery), len(validation), len(holdout)) == 0:
        raise RuntimeError("One or more frozen time splits have no eligible samples")

    models = {
        "controls_only": controls,
        "ownership_plus_controls": list(dict.fromkeys([*controls, *ownership])),
    }
    validation_predictions = validation[
        ["ann_date", "vendor_symbol", "industry_vendor", target]
    ].copy()
    holdout_predictions = holdout[
        ["ann_date", "vendor_symbol", "industry_vendor", target]
    ].copy()
    metrics: dict[str, dict] = {"validation": {}, "holdout": {}}
    seed = int(config["random_seed"])

    for name, features in models.items():
        validation_model = make_model(features, seed)
        validation_model.fit(discovery, discovery[target])
        prediction_column = f"prediction_{name}"
        validation_predictions[prediction_column] = validation_model.predict(validation)
        validation_scored = validation.copy()
        validation_scored[prediction_column] = validation_predictions[prediction_column].to_numpy()
        metrics["validation"][name] = prediction_metrics(
            validation_scored,
            prediction_column,
            target,
            int(config["minimum_cross_section"]),
            seed,
        )

        final_model = make_model(features, seed)
        train_final = frame[frame["ann_date"] <= validation_end]
        final_model.fit(train_final, train_final[target])
        holdout_predictions[prediction_column] = final_model.predict(holdout)
        holdout_scored = holdout.copy()
        holdout_scored[prediction_column] = holdout_predictions[prediction_column].to_numpy()
        metrics["holdout"][name] = prediction_metrics(
            holdout_scored,
            prediction_column,
            target,
            int(config["minimum_cross_section"]),
            seed + 1,
        )

    full = metrics["holdout"]["ownership_plus_controls"]
    base = metrics["holdout"]["controls_only"]
    metrics["decision"] = {
        "ownership_ic_increment": full["mean_daily_rank_ic"]
        - base["mean_daily_rank_ic"],
        "ownership_selected_return_increment": full["selected_mean_net_return"]
        - base["selected_mean_net_return"],
        "passes_incremental_gate": bool(
            np.isfinite(full["mean_daily_rank_ic"])
            and full["mean_daily_rank_ic"] >= 0.02
            and full["mean_daily_rank_ic"] > base["mean_daily_rank_ic"]
            and full["selected_mean_net_return"]
            > base["selected_mean_net_return"]
            and full["random_percentile"] >= 0.95
        ),
    }
    return validation_predictions, holdout_predictions, metrics


def render_report(metrics: dict, horizon: int) -> str:
    rows = []
    for split in ("validation", "holdout"):
        for model, values in metrics[split].items():
            rows.append(
                {
                    "split": split,
                    "model": model,
                    "samples": values["sample_count"],
                    "daily_rank_ic": values["mean_daily_rank_ic"],
                    "selected_net_return": values["selected_mean_net_return"],
                    "random_median": values["random_mean_median"],
                    "random_percentile": values["random_percentile"],
                }
            )
    table = pd.DataFrame(rows)
    decision = metrics["decision"]
    return "\n".join(
        [
            "# 持仓结构机器学习增量检验",
            "",
            f"标签：公告后下一可交易日开盘买入、{horizon}个交易日后可执行卖出的费用后收益。模型参数在读取留出期结果前固定。",
            "",
            table.to_markdown(index=False, floatfmt=".4f"),
            "",
            "## 决策",
            "",
            f"- 持仓特征留出期日截面IC增量：{decision['ownership_ic_increment']:.4f}",
            f"- 持仓特征Top20%事件收益增量：{decision['ownership_selected_return_increment']:.4%}",
            f"- 是否通过增量门槛：{'是' if decision['passes_incremental_gate'] else '否'}",
            "",
            "该结果是事件队列检验，不是满仓资金曲线。只有通过增量门槛后，才授权下一阶段组合级资金回测。",
            "",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", type=Path, default=ROOT / "configs" / "ownership_state.json"
    )
    parser.add_argument("--horizon", type=int, default=20)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(args.config.resolve())
    processed_root = resolve(config["processed_data_dir"])
    output_root = resolve(config["output_dir"]) / "ml_incremental"
    output_root.mkdir(parents=True, exist_ok=True)
    events = pd.read_parquet(processed_root / "ownership_events.parquet")
    frame = prepare_samples(config, events, args.horizon)
    validation, holdout, metrics = fit_and_score(frame, config, args.horizon)
    validation.to_parquet(output_root / "validation_predictions.parquet", index=False)
    holdout.to_parquet(output_root / "holdout_predictions.parquet", index=False)
    (output_root / "summary.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_root / "report.md").write_text(
        render_report(metrics, args.horizon), encoding="utf-8"
    )
    print(json.dumps(metrics["decision"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
