"""Transparent audit of the only stable retail-concentration proxy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ml_incremental_test import prediction_metrics
from ownership_pipeline import ROOT, load_config, resolve


def eligible_events(events: pd.DataFrame, config: dict) -> pd.DataFrame:
    frame = events.copy()
    frame["ann_date"] = pd.to_datetime(frame["ann_date"])
    frame = frame[
        frame["new_holder_disclosure"].fillna(False).astype(bool)
        & frame["holder_num_change_rate"].notna()
        & frame["future_20d_net_return"].notna()
        & (frame["listing_sessions"] >= int(config["minimum_listing_sessions"]))
        & (
            frame["average_turnover_cny_20"]
            >= float(config["minimum_average_turnover_cny"])
        )
    ].copy()
    frame["concentration_score"] = -frame["holder_num_change_rate"]
    return frame


def cohort_extremes(frame: pd.DataFrame, minimum_cross_section: int) -> pd.DataFrame:
    parts = []
    for date, group in frame.groupby("ann_date", sort=True):
        work = group[
            ["ann_date", "vendor_symbol", "concentration_score", "future_20d_net_return"]
        ].dropna()
        if len(work) < minimum_cross_section:
            continue
        count = max(1, int(np.ceil(len(work) * 0.2)))
        high = work.nlargest(count, "concentration_score").copy()
        low = work.nsmallest(count, "concentration_score").copy()
        high["bucket"] = "top_concentration"
        low["bucket"] = "bottom_concentration"
        parts.extend([high, low])
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def evaluate(events: pd.DataFrame, config: dict) -> tuple[dict, pd.DataFrame]:
    frame = eligible_events(events, config)
    masks = {
        "validation": (frame["ann_date"] >= pd.Timestamp(config["validation_start"]))
        & (frame["ann_date"] <= pd.Timestamp(config["validation_end"])),
        "holdout": frame["ann_date"] >= pd.Timestamp(config["holdout_start"]),
    }
    summary: dict[str, dict] = {}
    yearly_parts = []
    for split, mask in masks.items():
        sample = frame[mask].copy()
        summary[split] = prediction_metrics(
            sample,
            "concentration_score",
            "future_20d_net_return",
            int(config["minimum_cross_section"]),
            int(config["random_seed"]),
        )
        extremes = cohort_extremes(sample, int(config["minimum_cross_section"]))
        if not extremes.empty:
            yearly = (
                extremes.assign(year=extremes["ann_date"].dt.year)
                .pivot_table(
                    index="year",
                    columns="bucket",
                    values="future_20d_net_return",
                    aggfunc=["mean", "count"],
                )
                .reset_index()
            )
            yearly.columns = [
                "year"
                if column[0] == "year"
                else f"{column[0]}_{column[1]}"
                for column in yearly.columns
            ]
            yearly["top_minus_bottom"] = (
                yearly["mean_top_concentration"]
                - yearly["mean_bottom_concentration"]
            )
            yearly["split"] = split
            yearly_parts.append(yearly)
    summary["decision"] = {
        "passes_standalone_gate": bool(
            summary["validation"]["random_percentile"] >= 0.95
            and summary["holdout"]["random_percentile"] >= 0.95
            and summary["validation"]["selected_mean_net_return"]
            > summary["validation"]["all_event_mean_net_return"]
            and summary["holdout"]["selected_mean_net_return"]
            > summary["holdout"]["all_event_mean_net_return"]
        ),
        "interpretation": (
            "Standalone event strategy"
            if summary["validation"]["random_percentile"] >= 0.95
            and summary["holdout"]["random_percentile"] >= 0.95
            else "Risk/selection overlay only"
        ),
    }
    yearly_all = pd.concat(yearly_parts, ignore_index=True) if yearly_parts else pd.DataFrame()
    return summary, yearly_all


def render_report(summary: dict, yearly: pd.DataFrame) -> str:
    rows = []
    for split in ("validation", "holdout"):
        values = summary[split]
        rows.append(
            {
                "split": split,
                "events": values["sample_count"],
                "daily_rank_ic": values["mean_daily_rank_ic"],
                "top20_net_return": values["selected_mean_net_return"],
                "all_event_return": values["all_event_mean_net_return"],
                "random_median": values["random_mean_median"],
                "random_percentile": values["random_percentile"],
            }
        )
    return "\n".join(
        [
            "# 股东户数集中规则审计",
            "",
            "规则只使用 `concentration_score = -股东户数变化率`。同一公告日横截面选择集中度最高20%，公告后下一可交易日开盘买入，20个交易日后按可执行规则卖出，收益已经扣除双边成本。",
            "",
            pd.DataFrame(rows).to_markdown(index=False, floatfmt=".4f"),
            "",
            "## 逐年同日分组",
            "",
            yearly.to_markdown(index=False, floatfmt=".4f"),
            "",
            "## 结论",
            "",
            f"独立策略门槛：{'通过' if summary['decision']['passes_standalone_gate'] else '未通过'}。",
            f"建议用途：{summary['decision']['interpretation']}。",
            "",
            "该规则可作为弱筛选或风险覆盖信息；未通过配对随机95%门槛时，不应单独形成实盘策略。",
            "",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", type=Path, default=ROOT / "configs" / "ownership_state.json"
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(args.config.resolve())
    events = pd.read_parquet(
        resolve(config["processed_data_dir"]) / "ownership_events.parquet"
    )
    summary, yearly = evaluate(events, config)
    output = resolve(config["output_dir"]) / "concentration_rule"
    output.mkdir(parents=True, exist_ok=True)
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    yearly.to_csv(output / "yearly_metrics.csv", index=False)
    (output / "report.md").write_text(
        render_report(summary, yearly), encoding="utf-8"
    )
    print(json.dumps(summary["decision"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
