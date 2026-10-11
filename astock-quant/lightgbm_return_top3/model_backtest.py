from __future__ import annotations

import argparse
import hashlib
import pickle
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from TechnicalPatternScanner.utils import save_result_csv

from .feature_engine import (
    ALL_FEATURE_COLUMNS,
    DRAWDOWN_TARGET_COLUMN,
    ENTRY_EXECUTABLE_COLUMN,
    HOLD_DAYS,
    IC_MIN_OBS,
    INDUSTRY_CODE_COLUMN,
    SIGNAL_ELIGIBILITY_COLUMN,
    TARGET_COLUMN,
    FeatureBuildConfig,
    build_feature_panel,
    build_rebalance_dates,
    compute_ic_summary,
    get_feature_columns,
    prune_correlated_features,
    rank_feature_panel,
    select_candidate_features,
)

PACKAGE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = PACKAGE_DIR / "output"

TRAIN_START = "2020-01-01"
TRAIN_END = "2023-12-31"
VALIDATION_START = "2024-01-01"
VALIDATION_END = "2024-12-31"
OOS_START = "2025-01-01"

TEST_SAMPLE_SIZE = 120
MIN_TRAIN_ROWS = 1_000
TEMPLATE_TIE_TOL = 0.001
PURGE_TRAIN_TAIL_DAYS = HOLD_DAYS

# Lightweight params shared by both auxiliary models (prob_up + drawdown).
# Deliberately simpler than the main templates to keep wall-clock time acceptable.
AUX_MODEL_PARAMS: dict[str, float | int] = {
    "num_leaves": 31,
    "learning_rate": 0.05,
    "n_estimators": 200,
    "min_child_samples": 500,
    "feature_fraction": 0.7,
    "bagging_fraction": 0.8,
    "lambda_l2": 5.0,
    "subsample_freq": 1,
}

TEMPLATE_CONFIGS: dict[str, dict[str, float | int]] = {
    "small": {
        "num_leaves": 31,
        "learning_rate": 0.05,
        "n_estimators": 400,
        "min_child_samples": 1000,
        "feature_fraction": 0.7,
        "bagging_fraction": 0.8,
        "lambda_l2": 5.0,
    },
    "medium": {
        "num_leaves": 63,
        "learning_rate": 0.05,
        "n_estimators": 500,
        "min_child_samples": 500,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "lambda_l2": 2.0,
    },
    "large": {
        "num_leaves": 127,
        "learning_rate": 0.03,
        "n_estimators": 700,
        "min_child_samples": 300,
        "feature_fraction": 0.9,
        "bagging_fraction": 0.9,
        "lambda_l2": 1.0,
    },
}


@dataclass(frozen=True)
class ResearchConfig:
    fee_bps: float = 30.0
    workers: int = 8
    sample_size: int | None = None
    sample_seed: int = 42
    min_avg_amount: float = 20_000_000.0
    min_abs_ic: float = 0.005
    corr_threshold: float = 0.90
    min_feature_count: int = 30
    min_ic_obs: int = IC_MIN_OBS
    min_train_rows: int = MIN_TRAIN_ROWS
    template_tie_tol: float = TEMPLATE_TIE_TOL
    top_k: int = 3
    include_turnover_features: bool = True
    include_auxiliary_features: bool = True
    include_state_features: bool = False
    output_prefix: str = ""
    test: bool = False


def require_lightgbm():
    try:
        import lightgbm as lgb
    except ImportError as exc:  # pragma: no cover - import guard
        raise RuntimeError(
            "lightgbm 未安装。请先运行: python -m pip install lightgbm"
        ) from exc
    return lgb


def portfolio_turnover(old_weights: dict[str, float], new_weights: dict[str, float]) -> float:
    all_codes = set(old_weights) | set(new_weights)
    return float(sum(abs(new_weights.get(code, 0.0) - old_weights.get(code, 0.0)) for code in all_codes))


def max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return np.nan
    running_max = equity.cummax()
    drawdown = equity / running_max - 1.0
    return float(drawdown.min())


def prepare_research_context(
    config: ResearchConfig,
    verbose: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DatetimeIndex]:
    build_config = FeatureBuildConfig(
        sample_size=config.sample_size,
        sample_seed=config.sample_seed,
        workers=config.workers,
        min_avg_amount=config.min_avg_amount,
        include_auxiliary_features=config.include_auxiliary_features,
        test=config.test,
        verbose=verbose,
    )
    return build_feature_panel(build_config)


def _build_training_mask(
    panel: pd.DataFrame,
    train_end: str | pd.Timestamp,
    train_start: str | pd.Timestamp = TRAIN_START,
    purge_tail_days: int = PURGE_TRAIN_TAIL_DAYS,
) -> pd.Series:
    date_index = panel.index.get_level_values("Date")
    train_end_ts = pd.Timestamp(train_end)
    if purge_tail_days > 0:
        available_dates = pd.DatetimeIndex(date_index.unique()).sort_values()
        train_dates = available_dates[available_dates <= train_end_ts]
        if len(train_dates) <= purge_tail_days:
            effective_train_end = pd.Timestamp.min
        else:
            effective_train_end = train_dates[-(purge_tail_days + 1)]
    else:
        effective_train_end = train_end_ts
    return (
        (date_index >= pd.Timestamp(train_start))
        & (date_index <= effective_train_end)
        & panel[SIGNAL_ELIGIBILITY_COLUMN].fillna(False)
        & panel[TARGET_COLUMN].notna()
    )


def select_model_features(raw_panel: pd.DataFrame, config: ResearchConfig) -> tuple[pd.DataFrame, list[str]]:
    train_mask = _build_training_mask(raw_panel, TRAIN_END)
    train_panel = raw_panel.loc[train_mask].copy()
    if train_panel.empty:
        raise RuntimeError("训练面板为空，无法进行特征筛选。")

    feature_universe = get_feature_columns(
        include_turnover=config.include_turnover_features,
        include_auxiliary=config.include_auxiliary_features,
        include_state=config.include_state_features,
    )
    ic_summary = compute_ic_summary(
        train_panel,
        feature_columns=feature_universe,
        target=TARGET_COLUMN,
        min_obs=config.min_ic_obs,
    )
    candidates = select_candidate_features(
        ic_summary=ic_summary,
        min_abs_ic=config.min_abs_ic,
        min_keep=config.min_feature_count,
    )
    selected_features = prune_correlated_features(
        panel=train_panel,
        feature_columns=candidates,
        ic_summary=ic_summary[ic_summary["feature"].isin(candidates)].copy(),
        corr_threshold=config.corr_threshold,
        min_keep=config.min_feature_count,
    )
    if not selected_features:
        raise RuntimeError("特征筛选后为空，无法继续训练。")
    return ic_summary, selected_features


def prepare_ranked_panel(raw_panel: pd.DataFrame, selected_features: list[str]) -> pd.DataFrame:
    return rank_feature_panel(raw_panel, selected_features)


def _prepare_design_matrix(frame: pd.DataFrame, feature_columns: list[str]) -> pd.DataFrame:
    x = frame[feature_columns + [INDUSTRY_CODE_COLUMN]].copy()
    x[INDUSTRY_CODE_COLUMN] = x[INDUSTRY_CODE_COLUMN].astype("int32").astype("category")
    return x


def fit_model(
    train_frame: pd.DataFrame,
    feature_columns: list[str],
    template_name: str,
):
    lgb = require_lightgbm()
    params = TEMPLATE_CONFIGS[template_name].copy()
    model = lgb.LGBMRegressor(
        objective="regression",
        random_state=42,
        n_jobs=-1,
        verbosity=-1,
        subsample_freq=1,
        **params,
    )
    x_train = _prepare_design_matrix(train_frame, feature_columns)
    y_train = train_frame[TARGET_COLUMN].astype(np.float32)
    model.fit(x_train, y_train, categorical_feature=[INDUSTRY_CODE_COLUMN])
    return model


def extract_feature_importance(model, fit_month: str, template_name: str, period_label: str) -> pd.DataFrame:
    booster = model.booster_
    return pd.DataFrame(
        {
            "feature": booster.feature_name(),
            "gain_importance": booster.feature_importance(importance_type="gain"),
            "split_importance": booster.feature_importance(importance_type="split"),
            "fit_month": fit_month,
            "template": template_name,
            "period_label": period_label,
        }
    )


def _predict_signal_frame(
    panel: pd.DataFrame,
    signal_date: pd.Timestamp,
    feature_columns: list[str],
    model,
    template_name: str,
    fit_month: str,
    train_end: pd.Timestamp,
    period_label: str,
    live_mode: bool = False,
) -> pd.DataFrame:
    latest = panel.xs(signal_date, level="Date").copy()
    eligibility_col = "live_eligible" if live_mode else SIGNAL_ELIGIBILITY_COLUMN
    latest = latest[latest[eligibility_col].fillna(False)].copy()
    if latest.empty:
        return pd.DataFrame()

    min_feature_obs = int(len(feature_columns) * 0.7)
    coverage = latest[feature_columns].notna().sum(axis=1)
    latest = latest[coverage >= min_feature_obs].copy()
    if latest.empty:
        return pd.DataFrame()

    latest["prediction"] = model.predict(_prepare_design_matrix(latest, feature_columns))
    latest["signal_date"] = signal_date
    latest["fit_month"] = fit_month
    latest["train_end"] = train_end
    latest["template"] = template_name
    latest["period_label"] = period_label
    latest = latest.reset_index()
    return latest


def run_monthly_model_predictions(
    ranked_panel: pd.DataFrame,
    signal_dates: pd.DatetimeIndex,
    feature_columns: list[str],
    template_name: str,
    config: ResearchConfig,
    period_label: str,
    verbose: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if len(signal_dates) == 0:
        return pd.DataFrame(), pd.DataFrame()

    month_map: dict[pd.Period, list[pd.Timestamp]] = {}
    for signal_date in signal_dates:
        month_map.setdefault(signal_date.to_period("M"), []).append(signal_date)

    prediction_frames: list[pd.DataFrame] = []
    importance_frames: list[pd.DataFrame] = []

    for month, month_dates in sorted(month_map.items()):
        month_start = month.to_timestamp()
        train_end = month_start - pd.Timedelta(days=1)
        train_mask = _build_training_mask(ranked_panel, train_end)
        train_frame = ranked_panel.loc[train_mask].copy()
        if len(train_frame) < config.min_train_rows:
            continue

        model = fit_model(train_frame, feature_columns, template_name)
        fit_month = str(month)
        importance_frames.append(extract_feature_importance(model, fit_month, template_name, period_label))

        for signal_date in month_dates:
            predicted = _predict_signal_frame(
                panel=ranked_panel,
                signal_date=signal_date,
                feature_columns=feature_columns,
                model=model,
                template_name=template_name,
                fit_month=fit_month,
                train_end=train_end,
                period_label=period_label,
            )
            if not predicted.empty:
                prediction_frames.append(predicted)

        if verbose:
            print(
                f"[model_backtest] {period_label} {template_name} 已完成月份 {fit_month} "
                f"(训练样本 {len(train_frame):,})",
                flush=True,
            )

    predictions = pd.concat(prediction_frames, ignore_index=True) if prediction_frames else pd.DataFrame()
    importances = pd.concat(importance_frames, ignore_index=True) if importance_frames else pd.DataFrame()
    return predictions, importances


def compute_rank_ic_by_date(predictions: pd.DataFrame) -> pd.DataFrame:
    if predictions.empty:
        return pd.DataFrame(columns=["signal_date", "rank_ic", "n_obs"])

    rows: list[dict[str, object]] = []
    for signal_date, frame in predictions.groupby("signal_date", sort=True):
        sub = frame[["prediction", TARGET_COLUMN]].dropna()
        if len(sub) < 30 or sub["prediction"].nunique() < 5 or sub[TARGET_COLUMN].nunique() < 5:
            rows.append({"signal_date": signal_date, "rank_ic": np.nan, "n_obs": len(sub)})
            continue
        pred_rank = sub["prediction"].rank(method="average")
        target_rank = sub[TARGET_COLUMN].rank(method="average")
        rows.append(
            {
                "signal_date": pd.Timestamp(signal_date),
                "rank_ic": float(pred_rank.corr(target_rank)),
                "n_obs": int(len(sub)),
            }
        )
    return pd.DataFrame(rows).sort_values("signal_date").reset_index(drop=True)


def build_period_trades(
    predictions: pd.DataFrame,
    top_k: int,
    fee_bps: float,
) -> pd.DataFrame:
    if predictions.empty:
        return pd.DataFrame()

    rows: list[dict[str, object]] = []
    old_weights: dict[str, float] = {}
    slot_weight = 1.0 / top_k if top_k > 0 else 0.0

    for signal_date, frame in predictions.groupby("signal_date", sort=True):
        scored = frame.dropna(subset=["prediction"]).copy()
        if scored.empty:
            continue

        benchmark_pool = scored.dropna(subset=[TARGET_COLUMN]).copy()
        ranked = scored.sort_values("prediction", ascending=False)
        top = ranked.head(top_k).copy()
        if top.empty:
            continue

        executable_mask = top[ENTRY_EXECUTABLE_COLUMN].fillna(False) & top[TARGET_COLUMN].notna()
        executed = top.loc[executable_mask].copy()
        skipped = top.loc[~executable_mask].copy()

        codes = top["ts_code"].astype(str).tolist()
        executed_codes = executed["ts_code"].astype(str).tolist()
        new_weights = {code: slot_weight for code in executed_codes}
        turnover = portfolio_turnover(old_weights, new_weights)
        # Each executed slot contributes slot_weight; un-executed slots earn 0 (cash drag)
        invested_weight = float(len(executed) * slot_weight)
        gross_return = float(executed[TARGET_COLUMN].mean() * invested_weight) if not executed.empty else 0.0
        benchmark_return = float(benchmark_pool[TARGET_COLUMN].mean()) if not benchmark_pool.empty else np.nan
        net_return = gross_return - turnover * (fee_bps / 10_000.0)
        gross_alpha = gross_return - benchmark_return if pd.notna(benchmark_return) else np.nan
        net_alpha = net_return - benchmark_return if pd.notna(benchmark_return) else np.nan

        rows.append(
            {
                "signal_date": pd.Timestamp(signal_date),
                "entry_date": pd.to_datetime(top["entry_date"]).dropna().min(),
                "exit_date": pd.to_datetime(top["exit_date"]).dropna().max(),
                "template": str(top["template"].iloc[0]),
                "period_label": str(top["period_label"].iloc[0]),
                "requested_holdings": int(len(top)),
                "holdings": int(len(executed)),
                "skipped_holdings": int(len(skipped)),
                "execution_rate": float(len(executed) / len(top)),
                "invested_weight": invested_weight,
                "n_candidates": int(len(scored)),
                "n_benchmark": int(len(benchmark_pool)),
                "gross_return": gross_return,
                "net_return": net_return,
                "benchmark_return": benchmark_return,
                "gross_alpha": gross_alpha,
                "net_alpha": net_alpha,
                "turnover": turnover,
                "fee_bps": fee_bps,
                "avg_prediction": float(top["prediction"].mean()),
                "best_prediction": float(top["prediction"].max()),
                "top_codes": "|".join(codes),
                "top_names": "|".join(top["name"].astype(str).tolist()),
                "top_predictions": "|".join(f"{value:.6f}" for value in top["prediction"].tolist()),
                "executed_codes": "|".join(executed_codes),
                "executed_names": "|".join(executed["name"].astype(str).tolist()),
                "skipped_codes": "|".join(skipped["ts_code"].astype(str).tolist()),
                "skipped_names": "|".join(skipped["name"].astype(str).tolist()),
            }
        )
        old_weights = new_weights

    return pd.DataFrame(rows).sort_values("signal_date").reset_index(drop=True)


def summarize_periods(
    periods: pd.DataFrame,
    rank_ic_frame: pd.DataFrame,
    scope: str,
    template_name: str,
    selected_features: list[str],
) -> dict[str, object]:
    if periods.empty:
        return {
            "scope": scope,
            "template": template_name,
            "n_periods": 0,
            "mean_rank_ic": np.nan,
            "rank_ic_ir": np.nan,
            "win_rate_gross": np.nan,
            "win_rate_net": np.nan,
            "topk_hit_rate": np.nan,
            "topk_alpha_hit_rate": np.nan,
            "avg_gross_return": np.nan,
            "avg_net_return": np.nan,
            "avg_benchmark_return": np.nan,
            "avg_gross_alpha": np.nan,
            "avg_net_alpha": np.nan,
            "compound_gross": np.nan,
            "compound_net": np.nan,
            "compound_benchmark": np.nan,
            "max_drawdown_net": np.nan,
            "sharpe_net": np.nan,
            "n_features": len(selected_features),
            "selected_features": "|".join(selected_features),
        }

    relevant_ic = rank_ic_frame[rank_ic_frame["signal_date"].isin(periods["signal_date"])]
    ic_mean = float(relevant_ic["rank_ic"].mean()) if not relevant_ic.empty else np.nan
    ic_std = float(relevant_ic["rank_ic"].std(ddof=1)) if len(relevant_ic) > 1 else np.nan

    gross_equity = (1.0 + periods["gross_return"].fillna(0.0)).cumprod()
    net_equity = (1.0 + periods["net_return"].fillna(0.0)).cumprod()
    benchmark_equity = (1.0 + periods["benchmark_return"].fillna(0.0)).cumprod()

    net_std = periods["net_return"].std(ddof=1)
    periods_per_year = 250.0 / HOLD_DAYS
    sharpe = (
        float(periods["net_return"].mean() / net_std * np.sqrt(periods_per_year))
        if pd.notna(net_std) and net_std > 0
        else np.nan
    )

    return {
        "scope": scope,
        "template": template_name,
        "n_periods": int(len(periods)),
        "mean_rank_ic": ic_mean,
        "rank_ic_ir": ic_mean / ic_std if pd.notna(ic_std) and ic_std > 0 else np.nan,
        "win_rate_gross": float((periods["gross_return"] > 0).mean()),
        "win_rate_net": float((periods["net_return"] > 0).mean()),
        "topk_hit_rate": float((periods["gross_return"] > 0).mean()),
        "topk_alpha_hit_rate": float((periods["gross_alpha"] > 0).mean()),
        "avg_gross_return": float(periods["gross_return"].mean()),
        "avg_net_return": float(periods["net_return"].mean()),
        "avg_benchmark_return": float(periods["benchmark_return"].mean()),
        "avg_gross_alpha": float(periods["gross_alpha"].mean()),
        "avg_net_alpha": float(periods["net_alpha"].mean()),
        "compound_gross": float(gross_equity.iloc[-1] - 1.0),
        "compound_net": float(net_equity.iloc[-1] - 1.0),
        "compound_benchmark": float(benchmark_equity.iloc[-1] - 1.0),
        "max_drawdown_net": max_drawdown(net_equity),
        "sharpe_net": sharpe,
        "n_features": len(selected_features),
        "selected_features": "|".join(selected_features),
    }


def build_monthly_summary(predictions: pd.DataFrame, periods: pd.DataFrame) -> pd.DataFrame:
    if predictions.empty or periods.empty:
        return pd.DataFrame()

    rank_ic = compute_rank_ic_by_date(predictions)
    rows: list[dict[str, object]] = []
    period_month = periods["signal_date"].dt.to_period("M")

    for month, month_periods in periods.groupby(period_month, sort=True):
        month_ic = rank_ic[rank_ic["signal_date"].dt.to_period("M") == month]
        rows.append(
            {
                "template": str(month_periods["template"].iloc[0]),
                "period_label": str(month_periods["period_label"].iloc[0]),
                "month": str(month),
                "n_periods": int(len(month_periods)),
                "mean_rank_ic": float(month_ic["rank_ic"].mean()) if not month_ic.empty else np.nan,
                "avg_gross_return": float(month_periods["gross_return"].mean()),
                "avg_net_return": float(month_periods["net_return"].mean()),
                "avg_benchmark_return": float(month_periods["benchmark_return"].mean()),
                "avg_net_alpha": float(month_periods["net_alpha"].mean()),
                "compound_net": float((1.0 + month_periods["net_return"]).prod() - 1.0),
            }
        )
    return pd.DataFrame(rows)


def select_best_template(validation_metrics: pd.DataFrame, config: ResearchConfig) -> str:
    if validation_metrics.empty:
        raise RuntimeError("validation_metrics 为空，无法选择模板。")

    metric = validation_metrics["mean_rank_ic"].astype(float)
    metric = metric.fillna(-np.inf)
    best_ic = float(metric.max())
    candidates = validation_metrics.loc[metric >= best_ic - config.template_tie_tol].copy()
    candidates["avg_net_alpha"] = candidates["avg_net_alpha"].fillna(-np.inf)
    candidates["avg_net_return"] = candidates["avg_net_return"].fillna(-np.inf)
    # On ties, prefer simpler template: "small" > "medium" > "large" (ascending=False → s before l)
    selected = candidates.sort_values(
        ["avg_net_alpha", "avg_net_return", "template"],
        ascending=[False, False, False],
    ).iloc[0]
    return str(selected["template"])


def aggregate_feature_importance(importance_frame: pd.DataFrame) -> pd.DataFrame:
    if importance_frame.empty:
        return pd.DataFrame()

    aggregated = (
        importance_frame.groupby("feature", as_index=False)
        .agg(
            fit_count=("fit_month", "nunique"),
            mean_gain_importance=("gain_importance", "mean"),
            sum_gain_importance=("gain_importance", "sum"),
            mean_split_importance=("split_importance", "mean"),
            sum_split_importance=("split_importance", "sum"),
        )
        .sort_values(["mean_gain_importance", "mean_split_importance"], ascending=False)
    )

    latest_month = importance_frame["fit_month"].max()
    latest = (
        importance_frame[importance_frame["fit_month"] == latest_month]
        .groupby("feature", as_index=False)
        .agg(
            latest_gain_importance=("gain_importance", "mean"),
            latest_split_importance=("split_importance", "mean"),
        )
    )
    merged = aggregated.merge(latest, on="feature", how="left")
    return merged.sort_values("mean_gain_importance", ascending=False).reset_index(drop=True)


def build_backtest_summary(
    validation_periods: pd.DataFrame,
    validation_rank_ic: pd.DataFrame,
    oos_periods: pd.DataFrame,
    oos_rank_ic: pd.DataFrame,
    selected_template: str,
    selected_features: list[str],
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    rows.append(
        summarize_periods(
            validation_periods,
            validation_rank_ic,
            scope=f"validation_{pd.Timestamp(VALIDATION_START).year}",
            template_name=selected_template,
            selected_features=selected_features,
        )
    )
    rows.append(
        summarize_periods(
            oos_periods,
            oos_rank_ic,
            scope=f"oos_{pd.Timestamp(OOS_START).year}_plus",
            template_name=selected_template,
            selected_features=selected_features,
        )
    )

    all_periods = pd.concat([validation_periods, oos_periods], ignore_index=True)
    all_rank_ic = pd.concat([validation_rank_ic, oos_rank_ic], ignore_index=True)
    rows.append(
        summarize_periods(
            all_periods,
            all_rank_ic,
            scope="all_eval",
            template_name=selected_template,
            selected_features=selected_features,
        )
    )

    if not all_periods.empty:
        for year, group in all_periods.groupby(all_periods["signal_date"].dt.year, sort=True):
            year_ic = all_rank_ic[all_rank_ic["signal_date"].dt.year == year]
            rows.append(
                summarize_periods(
                    group.copy(),
                    year_ic.copy(),
                    scope=f"year_{year}",
                    template_name=selected_template,
                    selected_features=selected_features,
                )
            )

    return pd.DataFrame(rows)


def run_validation_search(
    ranked_panel: pd.DataFrame,
    trading_dates: pd.DatetimeIndex,
    selected_features: list[str],
    config: ResearchConfig,
    verbose: bool = True,
) -> dict[str, object]:
    validation_dates = build_rebalance_dates(
        trading_dates=trading_dates,
        start_date=VALIDATION_START,
        end_date=VALIDATION_END,
        step=HOLD_DAYS,
    )
    if len(validation_dates) == 0:
        raise RuntimeError("2024 验证期没有可用的调仓日期。")

    metrics_rows: list[dict[str, object]] = []
    monthly_frames: list[pd.DataFrame] = []
    predictions_by_template: dict[str, pd.DataFrame] = {}
    periods_by_template: dict[str, pd.DataFrame] = {}
    importance_by_template: dict[str, pd.DataFrame] = {}

    for template_name in TEMPLATE_CONFIGS:
        predictions, importance = run_monthly_model_predictions(
            ranked_panel=ranked_panel,
            signal_dates=validation_dates,
            feature_columns=selected_features,
            template_name=template_name,
            config=config,
            period_label="validation",
            verbose=verbose,
        )
        rank_ic = compute_rank_ic_by_date(predictions)
        periods = build_period_trades(predictions, top_k=config.top_k, fee_bps=config.fee_bps)

        row = summarize_periods(
            periods=periods,
            rank_ic_frame=rank_ic,
            scope=f"validation_{pd.Timestamp(VALIDATION_START).year}",
            template_name=template_name,
            selected_features=selected_features,
        )
        row["selected_features"] = "|".join(selected_features)
        metrics_rows.append(row)
        monthly_frames.append(build_monthly_summary(predictions, periods))
        predictions_by_template[template_name] = predictions
        periods_by_template[template_name] = periods
        importance_by_template[template_name] = importance

    validation_metrics = pd.DataFrame(metrics_rows)
    selected_template = select_best_template(validation_metrics, config)
    validation_metrics["is_selected"] = validation_metrics["template"].eq(selected_template)

    validation_monthly = (
        pd.concat([frame for frame in monthly_frames if not frame.empty], ignore_index=True)
        if monthly_frames
        else pd.DataFrame()
    )

    return {
        "validation_dates": validation_dates,
        "validation_metrics": validation_metrics,
        "validation_monthly": validation_monthly,
        "selected_template": selected_template,
        "selected_predictions": predictions_by_template.get(selected_template, pd.DataFrame()),
        "selected_periods": periods_by_template.get(selected_template, pd.DataFrame()),
        "selected_importance": importance_by_template.get(selected_template, pd.DataFrame()),
    }


def read_saved_validation_selection(output_dir: Path = OUTPUT_DIR) -> tuple[str, list[str]] | None:
    metrics_path = output_dir / "validation_metrics.csv"
    if not metrics_path.exists():
        return None

    metrics = pd.read_csv(metrics_path, encoding="utf-8-sig")
    if "is_selected" not in metrics.columns:
        return None

    selected_col = metrics["is_selected"]
    if selected_col.dtype == bool:
        selected = metrics[selected_col]
    else:
        normalized = selected_col.astype(str).str.strip().str.lower()
        selected = metrics[normalized.isin(["true", "1", "yes"])]
    if selected.empty:
        return None

    template_name = str(selected.iloc[0]["template"])
    feature_text = str(selected.iloc[0].get("selected_features", "")).strip()
    features = [f for f in feature_text.split("|") if f and f in ALL_FEATURE_COLUMNS]
    if not features:
        return None
    return template_name, features


def _model_cache_path(template_name: str, train_end: pd.Timestamp, feature_columns: list[str]) -> Path:
    feat_hash = hashlib.md5("|".join(sorted(feature_columns)).encode()).hexdigest()[:8]
    month_str = str(train_end.to_period("M"))
    return OUTPUT_DIR / f".model_cache_{template_name}_{month_str}_purge{PURGE_TRAIN_TAIL_DAYS}_{feat_hash}.pkl"


def run_live_prediction(
    ranked_panel: pd.DataFrame,
    signal_date: pd.Timestamp,
    feature_columns: list[str],
    template_name: str,
    config: ResearchConfig,
):
    month = signal_date.to_period("M")
    train_end = month.to_timestamp() - pd.Timedelta(days=1)
    train_mask = _build_training_mask(ranked_panel, train_end)
    train_frame = ranked_panel.loc[train_mask].copy()
    if len(train_frame) < config.min_train_rows:
        raise RuntimeError(f"{signal_date.date()} 之前的训练样本不足，无法生成日报。")

    cache_path = _model_cache_path(template_name, train_end, feature_columns)
    model = None
    if cache_path.exists():
        try:
            with open(cache_path, "rb") as fh:
                model = pickle.load(fh)
            print(f"[run_live_prediction] 从缓存加载模型 {cache_path.name}", flush=True)
        except Exception:
            model = None
    if model is None:
        model = fit_model(train_frame, feature_columns, template_name)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "wb") as fh:
            pickle.dump(model, fh)

    prediction = _predict_signal_frame(
        panel=ranked_panel,
        signal_date=signal_date,
        feature_columns=feature_columns,
        model=model,
        template_name=template_name,
        fit_month=str(month),
        train_end=train_end,
        period_label="latest",
        live_mode=True,
    )
    importance = extract_feature_importance(model, str(month), template_name, "latest")
    return prediction, importance


def run_aux_live_predictions(
    ranked_panel: pd.DataFrame,
    signal_date: pd.Timestamp,
    feature_columns: list[str],
    template_name: str,
    config: ResearchConfig,
) -> pd.DataFrame:
    """训练二分类（上涨概率）和最大回撤回归两个辅助模型，返回信号截面预测。

    返回列：ts_code, prob_up, pred_drawdown
    """
    lgb = require_lightgbm()
    month = signal_date.to_period("M")
    train_end = month.to_timestamp() - pd.Timedelta(days=1)
    train_mask = _build_training_mask(ranked_panel, train_end)
    train_frame = ranked_panel.loc[train_mask].copy()
    if len(train_frame) < config.min_train_rows:
        print("[aux_predictions] 训练样本不足，跳过辅助预测。", flush=True)
        return pd.DataFrame(columns=["ts_code", "prob_up", "pred_drawdown"])

    x_train = _prepare_design_matrix(train_frame, feature_columns)
    cat_feat = [INDUSTRY_CODE_COLUMN]

    # ── 上涨概率：binary classifier ──────────────────────────────────────────
    feat_hash = hashlib.md5("|".join(sorted(feature_columns)).encode()).hexdigest()[:8]
    month_str = str(train_end.to_period("M"))
    cache_prob = OUTPUT_DIR / f".aux_prob_{template_name}_{month_str}_purge{PURGE_TRAIN_TAIL_DAYS}_{feat_hash}.pkl"
    cache_dd = OUTPUT_DIR / f".aux_dd_{template_name}_{month_str}_purge{PURGE_TRAIN_TAIL_DAYS}_{feat_hash}.pkl"

    model_prob = None
    if cache_prob.exists():
        try:
            with open(cache_prob, "rb") as fh:
                model_prob = pickle.load(fh)
            print(f"[aux_predictions] 从缓存加载上涨概率模型 {cache_prob.name}", flush=True)
        except Exception:
            model_prob = None
    if model_prob is None:
        y_binary = (train_frame[TARGET_COLUMN] > 0).astype(int)
        model_prob = lgb.LGBMClassifier(
            objective="binary",
            random_state=42,
            n_jobs=-1,
            verbosity=-1,
            **AUX_MODEL_PARAMS,
        )
        model_prob.fit(x_train, y_binary, categorical_feature=cat_feat)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(cache_prob, "wb") as fh:
            pickle.dump(model_prob, fh)

    # ── 最大回撤：regression on fwd_drawdown_20d ─────────────────────────────
    model_dd = None
    if cache_dd.exists():
        try:
            with open(cache_dd, "rb") as fh:
                model_dd = pickle.load(fh)
            print(f"[aux_predictions] 从缓存加载回撤模型 {cache_dd.name}", flush=True)
        except Exception:
            model_dd = None
    if model_dd is None:
        dd_mask = train_frame[DRAWDOWN_TARGET_COLUMN].notna()
        x_dd = _prepare_design_matrix(train_frame[dd_mask], feature_columns)
        y_dd = train_frame.loc[dd_mask, DRAWDOWN_TARGET_COLUMN].astype(np.float32)
        if len(x_dd) >= config.min_train_rows:
            model_dd = lgb.LGBMRegressor(
                objective="regression",
                random_state=42,
                n_jobs=-1,
                verbosity=-1,
                **AUX_MODEL_PARAMS,
            )
            model_dd.fit(x_dd, y_dd, categorical_feature=cat_feat)
            with open(cache_dd, "wb") as fh:
                pickle.dump(model_dd, fh)

    # ── 截面预测 ──────────────────────────────────────────────────────────────
    latest = ranked_panel.xs(signal_date, level="Date").copy()
    latest = latest[latest["live_eligible"].fillna(False)].copy()
    if latest.empty:
        return pd.DataFrame(columns=["ts_code", "prob_up", "pred_drawdown"])

    min_obs = int(len(feature_columns) * 0.7)
    coverage = latest[feature_columns].notna().sum(axis=1)
    latest = latest[coverage >= min_obs].copy()
    if latest.empty:
        return pd.DataFrame(columns=["ts_code", "prob_up", "pred_drawdown"])

    x_signal = _prepare_design_matrix(latest, feature_columns)
    result = latest.reset_index()[["ts_code"]].copy()
    result["prob_up"] = model_prob.predict_proba(x_signal)[:, 1]
    result["pred_drawdown"] = model_dd.predict(x_signal) if model_dd is not None else np.nan
    return result


def _prefixed_output_name(key: str, output_prefix: str = "") -> str:
    prefix = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in output_prefix.strip())
    return f"{prefix}_{key}.csv" if prefix else f"{key}.csv"


def save_pipeline_outputs(result: dict[str, object], output_prefix: str = "") -> dict[str, str]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}

    for key in [
        "backtest_summary",
        "backtest_trades",
        "validation_metrics",
        "validation_monthly",
        "feature_importance",
    ]:
        df = result.get(key)
        if isinstance(df, pd.DataFrame):
            paths[key] = save_result_csv(df, str(OUTPUT_DIR / _prefixed_output_name(key, output_prefix)))

    return paths


def run_backtest_pipeline(
    config: ResearchConfig,
    verbose: bool = True,
) -> dict[str, object]:
    raw_panel, metadata, trading_dates = prepare_research_context(config, verbose=verbose)
    ic_summary, selected_features = select_model_features(raw_panel, config)
    ranked_panel = prepare_ranked_panel(raw_panel, selected_features)

    validation_result = run_validation_search(
        ranked_panel=ranked_panel,
        trading_dates=trading_dates,
        selected_features=selected_features,
        config=config,
        verbose=verbose,
    )
    selected_template = str(validation_result["selected_template"])

    oos_dates = build_rebalance_dates(
        trading_dates=trading_dates,
        start_date=OOS_START,
        end_date=trading_dates.max(),
        step=HOLD_DAYS,
    )
    oos_predictions, oos_importance = run_monthly_model_predictions(
        ranked_panel=ranked_panel,
        signal_dates=oos_dates,
        feature_columns=selected_features,
        template_name=selected_template,
        config=config,
        period_label="oos",
        verbose=verbose,
    )
    oos_rank_ic = compute_rank_ic_by_date(oos_predictions)
    oos_periods = build_period_trades(oos_predictions, top_k=config.top_k, fee_bps=config.fee_bps)

    validation_predictions = validation_result["selected_predictions"]
    validation_periods = validation_result["selected_periods"]
    validation_rank_ic = compute_rank_ic_by_date(validation_predictions)

    backtest_trades = pd.concat([validation_periods, oos_periods], ignore_index=True)
    feature_importance = aggregate_feature_importance(
        pd.concat(
            [validation_result["selected_importance"], oos_importance],
            ignore_index=True,
        )
    )
    backtest_summary = build_backtest_summary(
        validation_periods=validation_periods,
        validation_rank_ic=validation_rank_ic,
        oos_periods=oos_periods,
        oos_rank_ic=oos_rank_ic,
        selected_template=selected_template,
        selected_features=selected_features,
    )

    result = {
        "raw_panel": raw_panel,
        "metadata": metadata,
        "trading_dates": trading_dates,
        "ic_summary": ic_summary,
        "selected_features": selected_features,
        "ranked_panel": ranked_panel,
        "selected_template": selected_template,
        "validation_metrics": validation_result["validation_metrics"],
        "validation_monthly": validation_result["validation_monthly"],
        "backtest_trades": backtest_trades,
        "backtest_summary": backtest_summary,
        "feature_importance": feature_importance,
        "oos_predictions": oos_predictions,
    }
    result["output_paths"] = save_pipeline_outputs(result, output_prefix=config.output_prefix)
    return result


def build_arg_parser(include_ab_args: bool = True) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LightGBM 20日收益 Top3 回测")
    parser.add_argument("--test", action="store_true", help="使用小样本快速验证")
    parser.add_argument("--sample-size", type=int, default=None, help="随机抽样股票数")
    parser.add_argument("--workers", type=int, default=8, help="load_all 并发线程数")
    parser.add_argument("--fee-bps", type=float, default=30.0, help="单边成本，单位 bp")
    parser.add_argument("--min-avg-amount", type=float, default=20_000_000.0, help="20 日日均成交额下限")
    if include_ab_args:
        parser.add_argument("--output-prefix", type=str, default="", help="输出文件名前缀，用于保留 A/B 对照结果")
        parser.add_argument(
            "--no-turnover-features",
            action="store_false",
            dest="include_turnover_features",
            help="关闭 TurnoverRate 换手率因子，用于 baseline 对照回测",
        )
        parser.add_argument(
            "--no-aux-data-features",
            action="store_false",
            dest="include_auxiliary_features",
            help="关闭 market_meta/event_data 辅助因子，用于 baseline 对照回测",
        )
        parser.add_argument(
            "--with-state-features",
            action="store_true",
            dest="include_state_features",
            help="开启价格行为状态/心理风险特征，用于 A/B 对照研究",
        )
    return parser


def parse_args() -> ResearchConfig:
    args = build_arg_parser().parse_args()
    sample_size = args.sample_size
    if args.test and sample_size is None:
        sample_size = TEST_SAMPLE_SIZE
    return ResearchConfig(
        fee_bps=args.fee_bps,
        workers=args.workers,
        sample_size=sample_size,
        min_avg_amount=args.min_avg_amount,
        include_turnover_features=args.include_turnover_features,
        include_auxiliary_features=args.include_auxiliary_features,
        include_state_features=args.include_state_features,
        output_prefix=args.output_prefix,
        test=args.test,
    )


def main() -> None:
    config = parse_args()
    result = run_backtest_pipeline(config=config, verbose=True)
    print(
        "[model_backtest] 完成："
        f"selected_template={result['selected_template']}, "
        f"selected_features={len(result['selected_features'])}",
        flush=True,
    )
    for key, path in result.get("output_paths", {}).items():
        print(f"[model_backtest] {key}: {path}", flush=True)


if __name__ == "__main__":
    main()
