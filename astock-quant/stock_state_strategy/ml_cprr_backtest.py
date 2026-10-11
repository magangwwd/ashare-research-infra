from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import brier_score_loss, roc_auc_score

from backtest_mfe_mae import (
    build_stock_views,
    load_config,
    load_panel,
    resolve_from,
    simulate_portfolio,
)


SEEDS = [20260816, 20260817, 20260818, 20260819, 20260820]
ROUND_TRIP_COST = 0.0035
MFE_FLOOR = 0.06
MAE_CEILING = 0.08
CPRR_ENTRY_FLOOR = 2.0
PWIN_MARGIN = 0.05
ACTUAL_CPRR_CAP = 10.0

FEATURES = [
    "ret60",
    "drawdown20",
    "volume_ratio_5_20",
    "atr20_pct",
    "log_amount20",
    "listing_sessions",
    "rs60_rank",
    "z_rs60_rank",
    "z_atr20_pct",
    "z_drawdown20",
    "z_volume_ratio_5_20",
    "close_ma60",
    "ma60_ma120",
]


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
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    return value


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(json_value(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def date_weights(frame: pd.DataFrame) -> np.ndarray:
    counts = frame.groupby("signal_date")["instrument_id"].transform("size")
    date_count = max(int(frame["signal_date"].nunique()), 1)
    return (len(frame) / date_count / counts).to_numpy(dtype=float)


def load_samples(output_dir: Path) -> pd.DataFrame:
    candidates_path = output_dir / "eligible_candidates.csv"
    outcomes_path = output_dir / "candidate_outcomes_8_4.csv"
    if not candidates_path.exists() or not outcomes_path.exists():
        raise FileNotFoundError(
            "Run backtest_mfe_mae.py first; eligible candidates and 8/4 outcomes "
            "are required."
        )

    candidates = pd.read_csv(candidates_path, parse_dates=["signal_date"])
    outcomes = pd.read_csv(
        outcomes_path,
        parse_dates=["signal_date", "entry_date", "exit_date"],
    )
    if candidates.duplicated(["instrument_id", "signal_date"]).any():
        raise ValueError("Candidate key is not unique")
    if outcomes.duplicated(["instrument_id", "signal_date"]).any():
        raise ValueError("Outcome key is not unique")

    candidate_columns = [
        "instrument_id",
        "signal_date",
        "adj_close",
        "ma60",
        "ma120",
        "ret60",
        "drawdown20",
        "volume_ratio_5_20",
        "atr20_pct",
        "amount20",
        "listing_sessions",
        "rs60_rank",
        "z_rs60_rank",
        "z_atr20_pct",
        "z_drawdown20",
        "z_volume_ratio_5_20",
        "score",
    ]
    renamed = candidates[candidate_columns].rename(columns={"score": "hand_score"})
    samples = outcomes.merge(
        renamed,
        on=["instrument_id", "signal_date"],
        how="inner",
        validate="one_to_one",
        suffixes=("", "_candidate"),
    )
    if len(samples) != len(outcomes):
        raise ValueError(
            f"Only {len(samples):,}/{len(outcomes):,} outcome rows matched features"
        )
    if "atr20_pct_candidate" in samples:
        mismatch = ~np.isclose(
            samples["atr20_pct"],
            samples["atr20_pct_candidate"],
            rtol=1e-10,
            atol=1e-12,
            equal_nan=True,
        )
        if mismatch.any():
            raise ValueError("ATR feature differs between candidate and outcome files")
        samples = samples.drop(columns="atr20_pct_candidate")

    samples["close_ma60"] = samples["adj_close"] / samples["ma60"] - 1.0
    samples["ma60_ma120"] = samples["ma60"] / samples["ma120"] - 1.0
    samples["log_amount20"] = np.log1p(samples["amount20"].clip(lower=0.0))
    samples["p_win"] = samples["exit_reason"].isin(
        ["INTRADAY_TARGET", "GAP_TARGET"]
    ).astype("int8")
    actual_reward = (samples["mfe20"] - ROUND_TRIP_COST).clip(lower=0.0)
    actual_risk = np.maximum.reduce(
        [
            (samples["mae_pre"] + ROUND_TRIP_COST).to_numpy(dtype=float),
            (0.5 * samples["atr20_pct"]).to_numpy(dtype=float),
            np.full(len(samples), 0.01),
        ]
    )
    samples["actual_cprr20"] = np.minimum(
        actual_reward.to_numpy(dtype=float) / actual_risk,
        ACTUAL_CPRR_CAP,
    )
    invalid_actual = (samples["mfe20"] < MFE_FLOOR) | (
        samples["mae_pre"] > MAE_CEILING
    )
    samples.loc[invalid_actual, "actual_cprr20"] = 0.0
    samples["good_path"] = (
        (samples["mfe20"] >= 0.08) & (samples["mae_pre"] <= 0.04)
    ).astype("int8")

    numeric = samples[FEATURES + ["mfe20", "mae_pre"]].replace(
        [np.inf, -np.inf], np.nan
    )
    if numeric.isna().any().any():
        columns = numeric.columns[numeric.isna().any()].tolist()
        raise ValueError(f"Missing or infinite model values: {columns}")
    return samples.sort_values(
        ["signal_date", "instrument_id"]
    ).reset_index(drop=True)


def split_samples(samples: pd.DataFrame) -> dict[str, pd.DataFrame]:
    y2023 = pd.Timestamp("2023-01-01")
    y2024 = pd.Timestamp("2024-01-01")
    y2025 = pd.Timestamp("2025-01-01")
    splits = {
        "initial_train": samples.loc[
            (samples["signal_date"] < y2023) & (samples["exit_date"] < y2023)
        ].copy(),
        "early_stop": samples.loc[
            (samples["signal_date"] >= y2023)
            & (samples["signal_date"] < y2024)
            & (samples["exit_date"] < y2024)
        ].copy(),
        "final_train": samples.loc[
            (samples["signal_date"] < y2024) & (samples["exit_date"] < y2024)
        ].copy(),
        "calibration": samples.loc[
            (samples["signal_date"] >= y2024)
            & (samples["signal_date"] < y2025)
            & (samples["exit_date"] < y2025)
        ].copy(),
        "test": samples.loc[samples["signal_date"] >= y2025].copy(),
    }
    for name, frame in splits.items():
        if frame.empty:
            raise ValueError(f"Empty time split: {name}")
    return splits


def common_params(seed: int, estimators: int) -> dict[str, Any]:
    return {
        "n_estimators": int(estimators),
        "learning_rate": 0.03,
        "num_leaves": 15,
        "max_depth": 5,
        "min_child_samples": 80,
        "subsample": 0.80,
        "subsample_freq": 1,
        "colsample_bytree": 0.80,
        "reg_alpha": 0.10,
        "reg_lambda": 1.0,
        "random_state": seed,
        "n_jobs": -1,
        "verbosity": -1,
    }


def tuned_iterations(
    train: pd.DataFrame,
    valid: pd.DataFrame,
    seed: int,
) -> dict[str, int]:
    x_train = train[FEATURES]
    x_valid = valid[FEATURES]
    w_train = date_weights(train)
    w_valid = date_weights(valid)
    callbacks = [lgb.early_stopping(100, verbose=False)]
    models: dict[str, Any] = {
        "mfe": lgb.LGBMRegressor(
            objective="quantile", alpha=0.30, **common_params(seed, 1500)
        ),
        "mae": lgb.LGBMRegressor(
            objective="quantile", alpha=0.70, **common_params(seed, 1500)
        ),
        "pwin": lgb.LGBMClassifier(
            objective="binary", **common_params(seed, 1500)
        ),
    }
    targets = {"mfe": "mfe20", "mae": "mae_pre", "pwin": "p_win"}
    metrics = {"mfe": "quantile", "mae": "quantile", "pwin": "binary_logloss"}
    result: dict[str, int] = {}
    for name, model in models.items():
        model.fit(
            x_train,
            train[targets[name]],
            sample_weight=w_train,
            eval_set=[(x_valid, valid[targets[name]])],
            eval_sample_weight=[w_valid],
            eval_metric=metrics[name],
            callbacks=callbacks,
        )
        best = int(model.best_iteration_ or model.n_estimators)
        result[name] = max(best, 10)
    return result


@dataclass
class SeedModels:
    seed: int
    iterations: dict[str, int]
    mfe: lgb.LGBMRegressor
    mae: lgb.LGBMRegressor
    pwin: lgb.LGBMClassifier


def fit_seed_models(
    train: pd.DataFrame,
    seed: int,
    iterations: dict[str, int],
) -> SeedModels:
    weights = date_weights(train)
    x_train = train[FEATURES]
    mfe = lgb.LGBMRegressor(
        objective="quantile",
        alpha=0.30,
        **common_params(seed, iterations["mfe"]),
    )
    mae = lgb.LGBMRegressor(
        objective="quantile",
        alpha=0.70,
        **common_params(seed, iterations["mae"]),
    )
    pwin = lgb.LGBMClassifier(
        objective="binary", **common_params(seed, iterations["pwin"])
    )
    mfe.fit(x_train, train["mfe20"], sample_weight=weights)
    mae.fit(x_train, train["mae_pre"], sample_weight=weights)
    pwin.fit(x_train, train["p_win"], sample_weight=weights)
    return SeedModels(seed, iterations, mfe, mae, pwin)


def raw_predictions(frame: pd.DataFrame, models: list[SeedModels]) -> pd.DataFrame:
    x_data = frame[FEATURES]
    mfe = np.column_stack([model.mfe.predict(x_data) for model in models])
    mae = np.column_stack([model.mae.predict(x_data) for model in models])
    pwin = np.column_stack(
        [model.pwin.predict_proba(x_data)[:, 1] for model in models]
    )
    atr = frame["atr20_pct"].to_numpy(dtype=float)[:, None]
    reward = np.maximum(mfe - ROUND_TRIP_COST, 0.0)
    risk = np.maximum.reduce(
        [mae + ROUND_TRIP_COST, 0.5 * np.broadcast_to(atr, mae.shape), np.full(mae.shape, 0.01)]
    )
    ungated_raw = reward / risk
    ungated_q25 = np.quantile(ungated_raw, 0.25, axis=1)
    ungated_q75 = np.quantile(ungated_raw, 0.75, axis=1)
    raw = ungated_raw.copy()
    raw[(mfe < MFE_FLOOR) | (mae > MAE_CEILING)] = 0.0
    q25 = np.quantile(raw, 0.25, axis=1)
    q75 = np.quantile(raw, 0.75, axis=1)
    prediction = pd.DataFrame(index=frame.index)
    prediction["pred_mfe_q30"] = np.median(mfe, axis=1)
    prediction["pred_mae_q70"] = np.median(mae, axis=1)
    prediction["pred_p_win_raw"] = np.median(pwin, axis=1)
    prediction["raw_cprr_median"] = np.median(raw, axis=1)
    prediction["raw_cprr_iqr"] = q75 - q25
    prediction["cprr_lcb"] = np.maximum(
        prediction["raw_cprr_median"] - 0.5 * prediction["raw_cprr_iqr"],
        0.0,
    )
    prediction["ungated_cprr_median"] = np.median(ungated_raw, axis=1)
    prediction["ungated_cprr_lcb"] = np.maximum(
        prediction["ungated_cprr_median"]
        - 0.5 * (ungated_q75 - ungated_q25),
        0.0,
    )
    return prediction


def fit_isotonic(
    x_values: pd.Series,
    y_values: pd.Series,
    y_min: float,
    y_max: float,
) -> IsotonicRegression:
    if x_values.nunique() < 2:
        constant = float(np.clip(y_values.median(), y_min, y_max))
        log(
            "Degenerate isotonic input; using a constant calibration value "
            f"of {constant:.6f}"
        )
        calibrator = IsotonicRegression(
            y_min=y_min,
            y_max=y_max,
            increasing=True,
            out_of_bounds="clip",
        )
        calibrator.fit([0.0, 1.0], [constant, constant])
        return calibrator
    calibrator = IsotonicRegression(
        y_min=y_min,
        y_max=y_max,
        increasing=True,
        out_of_bounds="clip",
    )
    calibrator.fit(x_values.to_numpy(dtype=float), y_values.to_numpy(dtype=float))
    return calibrator


def apply_calibration(
    frame: pd.DataFrame,
    prediction: pd.DataFrame,
    cprr_calibrator: IsotonicRegression,
    pwin_calibrator: IsotonicRegression,
) -> pd.DataFrame:
    result = frame.copy()
    for column in prediction:
        result[column] = prediction[column].to_numpy()
    result["final_cprr20"] = cprr_calibrator.predict(result["cprr_lcb"])
    result["p_win_calibrated"] = pwin_calibrator.predict(
        result["pred_p_win_raw"]
    )
    forecast_valid = (
        (result["cprr_lcb"] > 0.0)
        & (result["pred_mfe_q30"] >= MFE_FLOOR)
        & (result["pred_mae_q70"] <= MAE_CEILING)
    )
    result.loc[~forecast_valid, "final_cprr20"] = 0.0
    result["p_win_required"] = (
        1.0 / (1.0 + result["final_cprr20"]) + PWIN_MARGIN
    )
    result["entry_signal"] = (
        forecast_valid
        & (result["final_cprr20"] >= CPRR_ENTRY_FLOOR)
        & (result["p_win_calibrated"] >= result["p_win_required"])
    )
    return result


def safe_spearman(x_values: pd.Series, y_values: pd.Series) -> float:
    if len(x_values) < 3 or x_values.nunique() < 2 or y_values.nunique() < 2:
        return math.nan
    return float(x_values.corr(y_values, method="spearman"))


def ranking_diagnostics(test: pd.DataFrame) -> dict[str, Any]:
    daily_ics: list[float] = []
    ungated_daily_ics: list[float] = []
    for _, group in test.groupby("signal_date"):
        value = safe_spearman(group["cprr_lcb"], group["actual_cprr20"])
        if np.isfinite(value):
            daily_ics.append(value)
        ungated_value = safe_spearman(
            group["ungated_cprr_lcb"], group["actual_cprr20"]
        )
        if np.isfinite(ungated_value):
            ungated_daily_ics.append(ungated_value)
    selected = test.loc[test["entry_signal"]]
    universe_good = float(test["good_path"].mean())
    selected_good = float(selected["good_path"].mean()) if len(selected) else math.nan
    try:
        auc = float(roc_auc_score(test["p_win"], test["pred_p_win_raw"]))
    except ValueError:
        auc = math.nan
    return {
        "test_rows": int(len(test)),
        "test_signal_dates": int(test["signal_date"].nunique()),
        "entry_rows_before_portfolio_constraints": int(len(selected)),
        "entry_signal_dates": int(selected["signal_date"].nunique()),
        "overall_spearman_lcb_actual_cprr": safe_spearman(
            test["cprr_lcb"], test["actual_cprr20"]
        ),
        "ungated_overall_spearman_actual_cprr": safe_spearman(
            test["ungated_cprr_lcb"], test["actual_cprr20"]
        ),
        "daily_rank_ic_mean": float(np.mean(daily_ics)) if daily_ics else math.nan,
        "daily_rank_ic_median": float(np.median(daily_ics)) if daily_ics else math.nan,
        "daily_rank_ic_dates": int(len(daily_ics)),
        "ungated_daily_rank_ic_mean": (
            float(np.mean(ungated_daily_ics)) if ungated_daily_ics else math.nan
        ),
        "ungated_daily_rank_ic_median": (
            float(np.median(ungated_daily_ics)) if ungated_daily_ics else math.nan
        ),
        "ungated_daily_rank_ic_dates": int(len(ungated_daily_ics)),
        "pwin_auc": auc,
        "pwin_brier_calibrated": float(
            brier_score_loss(test["p_win"], test["p_win_calibrated"])
        ),
        "universe_good_path_rate": universe_good,
        "selected_good_path_rate": selected_good,
        "good_path_lift_pp": (
            float((selected_good - universe_good) * 100.0)
            if np.isfinite(selected_good)
            else math.nan
        ),
        "selected_actual_cprr_mean": (
            float(selected["actual_cprr20"].mean()) if len(selected) else math.nan
        ),
        "selected_actual_cprr_median": (
            float(selected["actual_cprr20"].median()) if len(selected) else math.nan
        ),
    }


def save_prediction(frame: pd.DataFrame, path: Path) -> None:
    columns = [
        "instrument_id",
        "signal_date",
        "entry_date",
        "exit_date",
        "hand_score",
        "mfe20",
        "mae_pre",
        "p_win",
        "actual_cprr20",
        "good_path",
        "pred_mfe_q30",
        "pred_mae_q70",
        "pred_p_win_raw",
        "p_win_calibrated",
        "raw_cprr_median",
        "raw_cprr_iqr",
        "cprr_lcb",
        "ungated_cprr_median",
        "ungated_cprr_lcb",
        "final_cprr20",
        "p_win_required",
        "entry_signal",
    ]
    frame[columns].to_csv(path, index=False, encoding="utf-8-sig")


def outcome_view(frame: pd.DataFrame, score_column: str) -> pd.DataFrame:
    result = frame.copy()
    result["score"] = result[score_column].astype(float)
    return result


def run_random_baselines(
    test: pd.DataFrame,
    ml_selected: pd.DataFrame,
    stock_views: dict[str, pd.DataFrame],
    calendar: pd.DataFrame,
    config: dict[str, Any],
    runs: int,
    seed: int,
) -> pd.DataFrame:
    attempted = (
        ml_selected.groupby("signal_date").size().clip(upper=5).astype(int).to_dict()
    )
    pools = {date: group for date, group in test.groupby("signal_date")}
    records: list[dict[str, Any]] = []
    for run in range(runs):
        rng = np.random.default_rng(seed + run)
        pieces: list[pd.DataFrame] = []
        for date, count in attempted.items():
            pool = pools[date]
            take = min(int(count), len(pool))
            indices = rng.choice(pool.index.to_numpy(), size=take, replace=False)
            piece = pool.loc[indices].copy()
            piece["random_score"] = rng.random(take)
            pieces.append(piece)
        chosen = pd.concat(pieces, ignore_index=True) if pieces else test.iloc[:0].copy()
        chosen = outcome_view(chosen, "random_score") if len(chosen) else chosen
        _, _, metrics = simulate_portfolio(
            chosen,
            stock_views,
            calendar,
            config,
            random_mode=False,
        )
        records.append({"run": run, "seed": seed + run, **metrics})
        if (run + 1) % 20 == 0:
            log(f"Matched random baselines: {run + 1}/{runs}")
    return pd.DataFrame(records)


def importance_table(models: list[SeedModels]) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for model_set in models:
        for target in ["mfe", "mae", "pwin"]:
            model = getattr(model_set, target)
            gains = model.booster_.feature_importance(importance_type="gain")
            total = float(gains.sum())
            for feature, gain in zip(FEATURES, gains):
                records.append(
                    {
                        "seed": model_set.seed,
                        "target": target,
                        "feature": feature,
                        "gain": float(gain),
                        "gain_share": float(gain / total) if total > 0 else 0.0,
                    }
                )
    detail = pd.DataFrame(records)
    summary = (
        detail.groupby(["target", "feature"], as_index=False)["gain_share"]
        .mean()
        .sort_values(["target", "gain_share"], ascending=[True, False])
    )
    return summary


def quantiles(frame: pd.DataFrame, column: str) -> dict[str, float]:
    values = frame[column].dropna()
    return {
        "p10": float(values.quantile(0.10)),
        "p25": float(values.quantile(0.25)),
        "p50": float(values.quantile(0.50)),
        "p75": float(values.quantile(0.75)),
        "p90": float(values.quantile(0.90)),
    }


def acceptance_assessment(
    diagnostics: dict[str, Any],
    ml_metrics: dict[str, Any],
    hand_metrics: dict[str, Any],
    random_runs: pd.DataFrame,
) -> dict[str, Any]:
    random_return_median = (
        float(random_runs["total_return"].median()) if len(random_runs) else math.nan
    )
    checks = {
        "portfolio_trades_at_least_100": int(ml_metrics.get("trades", 0)) >= 100,
        "average_r_above_0_15": float(ml_metrics.get("average_r", math.nan)) > 0.15,
        "profit_factor_above_1_20": float(
            ml_metrics.get("profit_factor", math.nan)
        ) > 1.20,
        "max_drawdown_better_than_minus_15pct": float(
            ml_metrics.get("max_drawdown", math.nan)
        ) > -0.15,
        "return_beats_hand_score": float(ml_metrics.get("total_return", math.nan))
        > float(hand_metrics.get("total_return", math.nan)),
        "return_beats_matched_random_median": float(
            ml_metrics.get("total_return", math.nan)
        ) > random_return_median,
        "daily_rank_ic_above_0_03": float(
            diagnostics.get("daily_rank_ic_mean", math.nan)
        ) > 0.03,
        "good_path_lift_at_least_3pp": float(
            diagnostics.get("good_path_lift_pp", math.nan)
        ) >= 3.0,
    }
    passed = int(sum(bool(value) for value in checks.values()))
    trades = int(ml_metrics.get("trades", 0))
    if trades < 50:
        decision = "样本不足，当前证据不支持继续扩大投入"
    elif passed >= 6:
        decision = "可继续，但仅进入滚动样本外和前向模拟阶段"
    else:
        decision = "本版未通过，不建议直接进入模拟盘；先改进特征/标签"
    return {
        "checks": checks,
        "passed": passed,
        "total_checks": len(checks),
        "decision": decision,
        "matched_random_total_return_median": random_return_median,
        "matched_random_average_r_median": (
            float(random_runs["average_r"].median()) if len(random_runs) else math.nan
        ),
    }


def pct(value: Any) -> str:
    return "NA" if value is None or not np.isfinite(float(value)) else f"{float(value):.2%}"


def num(value: Any, digits: int = 3) -> str:
    return "NA" if value is None or not np.isfinite(float(value)) else f"{float(value):.{digits}f}"


def write_report(
    path: Path,
    split_sizes: dict[str, int],
    diagnostics: dict[str, Any],
    ml_metrics: dict[str, Any],
    hand_metrics: dict[str, Any],
    random_runs: pd.DataFrame,
    assessment: dict[str, Any],
    iterations: dict[int, dict[str, int]],
) -> None:
    random_return_q = random_runs["total_return"].quantile([0.05, 0.5, 0.95])
    checks = "\n".join(
        f"- [{'x' if passed else ' '}] `{name}`"
        for name, passed in assessment["checks"].items()
    )
    lines = [
        "# FinalCPRR20 LightGBM 快速回测",
        "",
        "## 结论",
        "",
        f"**{assessment['decision']}**",
        "",
        f"预设 8 项标准通过 {assessment['passed']}/{assessment['total_checks']} 项。",
        "",
        "## 时间切分",
        "",
        "| 样本 | 行数 |",
        "|---|---:|",
        *[f"| {name} | {size:,} |" for name, size in split_sizes.items()],
        "",
        "## 2025 年以后组合结果（100 万元重新起算）",
        "",
        "| 方法 | 交易数 | 总收益 | 平均R | 盈利因子 | 最大回撤 |",
        "|---|---:|---:|---:|---:|---:|",
        (
            f"| FinalCPRR20 | {int(ml_metrics.get('trades', 0))} | "
            f"{pct(ml_metrics.get('total_return'))} | {num(ml_metrics.get('average_r'))} | "
            f"{num(ml_metrics.get('profit_factor'))} | {pct(ml_metrics.get('max_drawdown'))} |"
        ),
        (
            f"| 原手工分数 | {int(hand_metrics.get('trades', 0))} | "
            f"{pct(hand_metrics.get('total_return'))} | {num(hand_metrics.get('average_r'))} | "
            f"{num(hand_metrics.get('profit_factor'))} | {pct(hand_metrics.get('max_drawdown'))} |"
        ),
        (
            f"| 匹配随机中位数 | {num(random_runs['trades'].median(), 0)} | "
            f"{pct(random_runs['total_return'].median())} | "
            f"{num(random_runs['average_r'].median())} | "
            f"{num(random_runs['profit_factor'].median())} | "
            f"{pct(random_runs['max_drawdown'].median())} |"
        ),
        "",
        (
            "匹配随机总收益 5%/50%/95% 分位："
            f"{pct(random_return_q.loc[0.05])} / {pct(random_return_q.loc[0.5])} / "
            f"{pct(random_return_q.loc[0.95])}。"
        ),
        "",
        "## 排序和阈值诊断",
        "",
        f"- 测试候选：{diagnostics['test_rows']:,} 条，{diagnostics['test_signal_dates']} 个信号日。",
        f"- 阈值通过：{diagnostics['entry_rows_before_portfolio_constraints']:,} 条，{diagnostics['entry_signal_dates']} 个信号日。",
        f"- 日截面 Rank IC 均值/中位数：{num(diagnostics['daily_rank_ic_mean'])} / {num(diagnostics['daily_rank_ic_median'])}。",
        f"- 全测试 LCB 与实际 CPRR 的 Spearman：{num(diagnostics['overall_spearman_lcb_actual_cprr'])}。",
        f"- 首触分类 AUC：{num(diagnostics['pwin_auc'])}；校准后 Brier：{num(diagnostics['pwin_brier_calibrated'])}。",
        f"- 好路径比例（全候选/阈值通过）：{pct(diagnostics['universe_good_path_rate'])} / {pct(diagnostics['selected_good_path_rate'])}，提升 {num(diagnostics['good_path_lift_pp'], 2)} 个百分点。",
        "",
        "## 预设验收项",
        "",
        checks,
        "",
        "## 模型轮数",
        "",
        "```json",
        json.dumps(iterations, ensure_ascii=False, indent=2),
        "```",
        "",
        "## 解释边界",
        "",
        "本轮只验证现有周频候选池中的 ML 排序和入场阈值。交易退出仍固定为 +8% 止盈、-4% 止损和 20 日到期，未使用预测 MFE/MAE 动态设置止盈止损。2025—2026 数据此前已被手工策略分析过，因此属于准样本外；真正前向验证仍需使用 2026-08-11 以后新增数据。",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="FinalCPRR20 LightGBM quick backtest")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--random-runs", type=int, default=None)
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    config_path = resolve_from(script_dir, args.config)
    config = load_config(config_path)
    output_dir = resolve_from(script_dir, str(config["output_dir"]))
    ml_dir = output_dir / "ml"
    ml_dir.mkdir(parents=True, exist_ok=True)

    log("Loading feature/outcome samples")
    samples = load_samples(output_dir)
    splits = split_samples(samples)
    split_sizes = {name: int(len(frame)) for name, frame in splits.items()}
    log(f"Time splits: {split_sizes}")

    model_sets: list[SeedModels] = []
    iterations: dict[int, dict[str, int]] = {}
    for number, seed in enumerate(SEEDS, start=1):
        log(f"Tuning seed {number}/{len(SEEDS)}: {seed}")
        best = tuned_iterations(splits["initial_train"], splits["early_stop"], seed)
        iterations[seed] = best
        log(f"Seed {seed} best rounds: {best}")
        model_sets.append(fit_seed_models(splits["final_train"], seed, best))

    log("Predicting calibration and final test periods")
    calibration_raw = raw_predictions(splits["calibration"], model_sets)
    test_raw = raw_predictions(splits["test"], model_sets)
    cprr_calibrator = fit_isotonic(
        calibration_raw["cprr_lcb"],
        splits["calibration"]["actual_cprr20"],
        0.0,
        ACTUAL_CPRR_CAP,
    )
    pwin_calibrator = fit_isotonic(
        calibration_raw["pred_p_win_raw"],
        splits["calibration"]["p_win"],
        0.0,
        1.0,
    )
    calibration = apply_calibration(
        splits["calibration"], calibration_raw, cprr_calibrator, pwin_calibrator
    )
    test = apply_calibration(
        splits["test"], test_raw, cprr_calibrator, pwin_calibrator
    )
    save_prediction(calibration, ml_dir / "predictions_calibration.csv")
    save_prediction(test, ml_dir / "predictions_test.csv")
    importance_table(model_sets).to_csv(
        ml_dir / "feature_importance.csv", index=False, encoding="utf-8-sig"
    )
    joblib.dump(
        {
            "features": FEATURES,
            "seeds": SEEDS,
            "models": [
                {
                    "seed": model.seed,
                    "iterations": model.iterations,
                    "mfe": model.mfe,
                    "mae": model.mae,
                    "pwin": model.pwin,
                }
                for model in model_sets
            ],
            "cprr_calibrator": cprr_calibrator,
            "pwin_calibrator": pwin_calibrator,
            "constants": {
                "round_trip_cost": ROUND_TRIP_COST,
                "mfe_floor": MFE_FLOOR,
                "mae_ceiling": MAE_CEILING,
                "cprr_entry_floor": CPRR_ENTRY_FLOOR,
                "pwin_margin": PWIN_MARGIN,
            },
        },
        ml_dir / "final_cprr20_bundle.joblib",
        compress=3,
    )

    diagnostics = ranking_diagnostics(test)
    log(f"Test ranking diagnostics: {json_value(diagnostics)}")

    log("Loading 2025+ price panel for identical portfolio accounting")
    data_root = resolve_from(script_dir, str(config["data_root"]))
    panel, _, calendar, panel_audit = load_panel(
        data_root, pd.Timestamp("2024-12-01")
    )
    stock_views = build_stock_views(panel, set(test["instrument_id"].unique()))

    ml_selected = test.loc[test["entry_signal"]].copy()
    ml_outcomes = outcome_view(ml_selected, "final_cprr20")
    hand_outcomes = outcome_view(test, "hand_score")
    log("Simulating FinalCPRR20 and hand-score portfolios")
    ml_trades, ml_curve, ml_metrics = simulate_portfolio(
        ml_outcomes, stock_views, calendar, config
    )
    hand_trades, hand_curve, hand_metrics = simulate_portfolio(
        hand_outcomes, stock_views, calendar, config
    )

    random_runs_count = int(
        args.random_runs
        if args.random_runs is not None
        else config.get("random_baseline_runs", 100)
    )
    log(f"Running {random_runs_count} matched random baselines")
    random_runs = run_random_baselines(
        test,
        ml_selected,
        stock_views,
        calendar,
        config,
        random_runs_count,
        int(config.get("random_seed", SEEDS[0])) + 1000,
    )

    ml_trades.to_csv(ml_dir / "ml_test_trades.csv", index=False, encoding="utf-8-sig")
    ml_curve.to_csv(ml_dir / "ml_test_equity.csv", index=False, encoding="utf-8-sig")
    hand_trades.to_csv(
        ml_dir / "hand_test_trades.csv", index=False, encoding="utf-8-sig"
    )
    hand_curve.to_csv(
        ml_dir / "hand_test_equity.csv", index=False, encoding="utf-8-sig"
    )
    random_runs.to_csv(
        ml_dir / "matched_random_runs.csv", index=False, encoding="utf-8-sig"
    )

    assessment = acceptance_assessment(
        diagnostics, ml_metrics, hand_metrics, random_runs
    )
    summary = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "split_sizes": split_sizes,
        "model_iterations": iterations,
        "diagnostics": diagnostics,
        "prediction_quantiles_test": {
            column: quantiles(test, column)
            for column in [
                "pred_mfe_q30",
                "pred_mae_q70",
                "pred_p_win_raw",
                "cprr_lcb",
                "final_cprr20",
            ]
        },
        "portfolio": {"final_cprr20": ml_metrics, "hand_score": hand_metrics},
        "random_baseline": {
            "runs": random_runs_count,
            "total_return_p05": float(random_runs["total_return"].quantile(0.05)),
            "total_return_median": float(random_runs["total_return"].median()),
            "total_return_p95": float(random_runs["total_return"].quantile(0.95)),
            "average_r_median": float(random_runs["average_r"].median()),
        },
        "assessment": assessment,
        "panel_audit": panel_audit,
    }
    write_json(ml_dir / "summary.json", summary)
    write_report(
        ml_dir / "report.md",
        split_sizes,
        diagnostics,
        ml_metrics,
        hand_metrics,
        random_runs,
        assessment,
        iterations,
    )
    log(f"Decision: {assessment['decision']}")
    log(f"Outputs written to {ml_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
