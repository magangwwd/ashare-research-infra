"""Frozen two-stage audit: return alpha first, control-state filter second."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
from scipy.stats import ttest_rel

from ownership_pipeline import ROOT, load_config, resolve


def select_cohorts(frame: pd.DataFrame, minimum: int) -> pd.DataFrame:
    parts = []
    for date, group in frame.groupby("ann_date", sort=True):
        work = group.dropna(
            subset=[
                "prediction_controls_only",
                "discovery_selected_behavior_score",
                "future_20d_net_return",
            ]
        ).copy()
        if len(work) < minimum:
            continue
        count = max(1, int(np.ceil(len(work) * 0.2)))
        baseline = work.nlargest(count, "prediction_controls_only").copy()
        baseline["strategy"] = "alpha_top20"

        control_cutoff = work["discovery_selected_behavior_score"].quantile(0.50)
        controlled = work[
            work["discovery_selected_behavior_score"] >= control_cutoff
        ]
        overlay = controlled.nlargest(count, "prediction_controls_only").copy()
        if len(overlay) < count:
            continue
        overlay["strategy"] = "alpha_top20_with_control_top50"
        parts.extend([baseline, overlay])
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def paired_random(
    frame: pd.DataFrame, selected: pd.DataFrame, runs: int, seed: int
) -> np.ndarray:
    chosen_counts = selected.groupby("ann_date").size().to_dict()
    groups = []
    for date, count in chosen_counts.items():
        outcomes = frame.loc[
            frame["ann_date"] == date, "future_20d_net_return"
        ].dropna().to_numpy(dtype=float)
        if len(outcomes) >= count:
            groups.append((outcomes, count))
    rng = np.random.default_rng(seed)
    output = np.full(runs, np.nan)
    for run in range(runs):
        values = []
        for outcomes, count in groups:
            values.extend(rng.choice(outcomes, size=count, replace=False))
        if values:
            output[run] = float(np.mean(values))
    return output


def evaluate_split(
    frame: pd.DataFrame, split: str, minimum: int, seed: int
) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    selected = select_cohorts(frame, minimum)
    if selected.empty:
        raise RuntimeError(f"No eligible cohorts in {split}")
    rows = []
    yearly_rows = []
    daily = selected.groupby(["ann_date", "strategy"], observed=True)[
        "future_20d_net_return"
    ].mean().unstack("strategy")
    for strategy, sample in selected.groupby("strategy", observed=True):
        random_values = paired_random(frame, sample, 500, seed)
        random_values = random_values[np.isfinite(random_values)]
        mean_return = float(sample["future_20d_net_return"].mean())
        rows.append(
            {
                "split": split,
                "strategy": strategy,
                "events": int(len(sample)),
                "mean_net_return": mean_return,
                "median_net_return": float(sample["future_20d_net_return"].median()),
                "positive_return_rate": float(
                    (sample["future_20d_net_return"] > 0).mean()
                ),
                "equal_date_mean_return": float(daily[strategy].mean()),
                "random_median": float(np.median(random_values)),
                "random_percentile": float(np.mean(random_values < mean_return)),
            }
        )
        for year, year_sample in sample.groupby(sample["ann_date"].dt.year):
            yearly_rows.append(
                {
                    "split": split,
                    "year": int(year),
                    "strategy": strategy,
                    "events": int(len(year_sample)),
                    "mean_net_return": float(
                        year_sample["future_20d_net_return"].mean()
                    ),
                }
            )
    common = daily.dropna()
    difference = (
        common["alpha_top20_with_control_top50"] - common["alpha_top20"]
    )
    paired_test = ttest_rel(
        common["alpha_top20_with_control_top50"],
        common["alpha_top20"],
        nan_policy="omit",
    )
    comparison = {
        "daily_cohorts": int(len(common)),
        "overlay_minus_alpha_daily_return": float(difference.mean()),
        "overlay_better_day_rate": float((difference > 0).mean()),
        "paired_t": float(paired_test.statistic),
        "paired_p": float(paired_test.pvalue),
    }
    return comparison, pd.DataFrame(rows), pd.DataFrame(yearly_rows)


def main() -> None:
    config = load_config(ROOT / "configs" / "ownership_state.json")
    output_root = resolve(config["output_dir"])
    scored = pd.read_parquet(output_root / "control_state" / "scored_events.parquet")
    scored["ann_date"] = pd.to_datetime(scored["ann_date"])
    all_metrics = []
    all_yearly = []
    summary = {}
    for index, split in enumerate(("validation", "holdout")):
        predictions = pd.read_parquet(
            output_root / "ml_incremental" / f"{split}_predictions.parquet"
        )
        predictions["ann_date"] = pd.to_datetime(predictions["ann_date"])
        prediction_columns = [
            "ann_date",
            "vendor_symbol",
            "prediction_controls_only",
        ]
        merged = scored.merge(
            predictions[prediction_columns],
            on=["ann_date", "vendor_symbol"],
            how="inner",
            validate="one_to_one",
        )
        comparison, metrics, yearly = evaluate_split(
            merged,
            split,
            int(config["minimum_cross_section"]),
            int(config["random_seed"]) + index,
        )
        summary[split] = comparison
        all_metrics.append(metrics)
        all_yearly.append(yearly)
    metrics = pd.concat(all_metrics, ignore_index=True)
    yearly = pd.concat(all_yearly, ignore_index=True)
    validation = summary["validation"]
    holdout = summary["holdout"]
    summary["decision"] = {
        "passes_overlay_gate": bool(
            validation["overlay_minus_alpha_daily_return"] > 0
            and holdout["overlay_minus_alpha_daily_return"] > 0
            and validation["paired_p"] < 0.05
            and holdout["paired_p"] < 0.05
        ),
        "use": "return_strategy" if (
            validation["overlay_minus_alpha_daily_return"] > 0
            and holdout["overlay_minus_alpha_daily_return"] > 0
            and validation["paired_p"] < 0.05
            and holdout["paired_p"] < 0.05
        ) else "diagnostic_filter_only",
    }
    output = output_root / "control_state"
    metrics.to_csv(output / "alpha_overlay_metrics.csv", index=False)
    yearly.to_csv(output / "alpha_overlay_yearly.csv", index=False)
    (output / "alpha_overlay_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(metrics.to_string(index=False))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
