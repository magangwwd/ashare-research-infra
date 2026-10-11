"""Decile-shape audit for the frozen discovery-selected control proxy."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import ttest_1samp

from ownership_pipeline import ROOT, load_config, resolve


def main() -> None:
    config = load_config(ROOT / "configs" / "ownership_state.json")
    source = resolve(config["output_dir"]) / "control_state" / "scored_events.parquet"
    frame = pd.read_parquet(source)
    frame["ann_date"] = pd.to_datetime(frame["ann_date"])
    score = "discovery_selected_behavior_score"
    target = "future_20d_net_return"
    frame = frame.dropna(subset=[score, target]).copy()
    frame["within_date_percentile"] = frame.groupby("ann_date", sort=False)[score].rank(
        pct=True, method="average"
    )
    frame["decile"] = np.ceil(frame["within_date_percentile"] * 10).clip(1, 10).astype(int)
    frame["split"] = np.select(
        [
            frame["ann_date"] <= pd.Timestamp(config["discovery_end"]),
            frame["ann_date"] <= pd.Timestamp(config["validation_end"]),
        ],
        ["discovery", "validation"],
        default="holdout",
    )

    event_metrics = frame.groupby(["split", "decile"], observed=True).agg(
        events=(target, "size"),
        mean_net_return=(target, "mean"),
        median_net_return=(target, "median"),
        positive_return_rate=(target, lambda x: float((x > 0).mean())),
        mean_future_lock_behavior=("future_lock_behavior", "mean"),
    ).reset_index()
    daily = frame.groupby(["split", "decile", "ann_date"], observed=True)[target].mean().reset_index()
    daily_rows = []
    for (split, decile), group in daily.groupby(["split", "decile"], observed=True):
        test = ttest_1samp(group[target], 0.0, nan_policy="omit")
        daily_rows.append(
            {
                "split": split,
                "decile": int(decile),
                "daily_cohorts": int(len(group)),
                "equal_date_mean_return": float(group[target].mean()),
                "return_t": float(test.statistic),
            }
        )
    result = event_metrics.merge(
        pd.DataFrame(daily_rows), on=["split", "decile"], how="left"
    ).sort_values(["split", "decile"])
    output = resolve(config["output_dir"]) / "control_state"
    result.to_csv(output / "decile_metrics.csv", index=False)
    print(result.to_string(index=False))


if __name__ == "__main__":
    main()
