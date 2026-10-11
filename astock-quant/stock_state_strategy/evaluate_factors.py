"""Univariate, year-by-year ownership factor audit with frozen time splits."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy.stats import spearmanr
from statsmodels.stats.multitest import multipletests

from ownership_pipeline import COMPOSITION_FACTORS, HOLDER_FACTORS, ROOT, load_config, resolve


def newey_west_mean_test(values: pd.Series, max_lag: int = 3) -> tuple[float, float, float]:
    clean = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
    if len(clean) < 3:
        return np.nan, np.nan, np.nan
    model = sm.OLS(clean, np.ones((len(clean), 1))).fit(
        cov_type="HAC", cov_kwds={"maxlags": min(max_lag, len(clean) - 1)}
    )
    return float(model.params[0]), float(model.tvalues[0]), float(model.pvalues[0])


def split_name(dates: pd.Series, config: dict) -> pd.Series:
    discovery_end = pd.Timestamp(config["discovery_end"])
    validation_start = pd.Timestamp(config["validation_start"])
    validation_end = pd.Timestamp(config["validation_end"])
    holdout_start = pd.Timestamp(config["holdout_start"])
    return pd.Series(
        np.select(
            [
                dates <= discovery_end,
                (dates >= validation_start) & (dates <= validation_end),
                dates >= holdout_start,
            ],
            ["discovery", "validation", "holdout"],
            default="unused",
        ),
        index=dates.index,
    )


def source_mask(frame: pd.DataFrame, factor: str) -> pd.Series:
    if factor in COMPOSITION_FACTORS:
        return (
            frame["new_composition_disclosure"].fillna(False).astype(bool)
            & frame["composition_quality_valid"].fillna(False).astype(bool)
        )
    return frame["new_holder_disclosure"].fillna(False).astype(bool)


def eligible_mask(frame: pd.DataFrame, config: dict) -> pd.Series:
    return (
        (frame["listing_sessions"] >= int(config["minimum_listing_sessions"]))
        & (
            frame["average_turnover_cny_20"]
            >= float(config["minimum_average_turnover_cny"])
        )
        & frame["market_cap_cny"].gt(0)
    )


def neutralize_by_month(frame: pd.DataFrame, target: str) -> pd.Series:
    residual = pd.Series(np.nan, index=frame.index, dtype=float)
    controls = ["market_cap_cny", "ret_20", "ret_60", "volatility_20"]
    for _, group in frame.groupby("announcement_month", sort=False):
        columns = [target, *controls, "industry_vendor"]
        work = group[columns].replace([np.inf, -np.inf], np.nan).dropna()
        if len(work) < 30:
            continue
        continuous = work[controls].copy()
        continuous["market_cap_cny"] = np.log(continuous["market_cap_cny"])
        for column in controls:
            low, high = continuous[column].quantile([0.01, 0.99])
            continuous[column] = continuous[column].clip(low, high)
            scale = continuous[column].std(ddof=0)
            continuous[column] = (
                (continuous[column] - continuous[column].mean()) / scale
                if scale and np.isfinite(scale)
                else 0.0
            )
        industry = pd.get_dummies(
            work["industry_vendor"].fillna("UNKNOWN"), drop_first=True, dtype=float
        )
        design = sm.add_constant(
            pd.concat([continuous, industry], axis=1), has_constant="add"
        )
        try:
            fitted = sm.OLS(work[target].astype(float), design.astype(float)).fit()
        except (ValueError, np.linalg.LinAlgError):
            continue
        residual.loc[work.index] = fitted.resid
    return residual


def monthly_factor_metrics(
    frame: pd.DataFrame,
    factor: str,
    target: str,
    minimum_cross_section: int,
) -> pd.DataFrame:
    rows: list[dict] = []
    for month, group in frame.groupby("announcement_month", sort=True):
        work = group[[factor, target]].replace([np.inf, -np.inf], np.nan).dropna()
        if len(work) < minimum_cross_section or work[factor].nunique() < 3:
            continue
        ic = spearmanr(work[factor], work[target]).statistic
        rank = work[factor].rank(method="average", pct=True)
        low = work.loc[rank <= 0.2, target]
        high = work.loc[rank > 0.8, target]
        rows.append(
            {
                "announcement_month": month,
                "sample_count": len(work),
                "rank_ic": float(ic),
                "q5_mean_return": float(high.mean()),
                "q1_mean_return": float(low.mean()),
                "q5_minus_q1": float(high.mean() - low.mean()),
            }
        )
    return pd.DataFrame(rows)


def evaluate(config: dict, events: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    events = events.copy()
    events["ann_date"] = pd.to_datetime(events["ann_date"])
    events["announcement_month"] = events["ann_date"].dt.to_period("M").astype(str)
    events["split"] = split_name(events["ann_date"], config)
    base_eligible = eligible_mask(events, config)
    factors = [factor for factor in (*COMPOSITION_FACTORS, *HOLDER_FACTORS) if factor in events]
    monthly_parts: list[pd.DataFrame] = []
    yearly_parts: list[pd.DataFrame] = []
    summaries: list[dict] = []

    for horizon in [int(value) for value in config["horizons"]]:
        raw_target = f"future_{horizon}d_net_return"
        residual_target = f"future_{horizon}d_neutral_return"
        eligible_events = events[base_eligible & events[raw_target].notna()].copy()
        eligible_events[residual_target] = neutralize_by_month(
            eligible_events, raw_target
        )
        for factor in factors:
            factor_events = eligible_events[source_mask(eligible_events, factor)].copy()
            for target_kind, target in (
                ("raw", raw_target),
                ("neutral", residual_target),
            ):
                for split in ("discovery", "validation", "holdout"):
                    sample = factor_events[factor_events["split"] == split]
                    monthly = monthly_factor_metrics(
                        sample,
                        factor,
                        target,
                        int(config["minimum_cross_section"]),
                    )
                    if monthly.empty:
                        continue
                    monthly["factor"] = factor
                    monthly["horizon"] = horizon
                    monthly["target_kind"] = target_kind
                    monthly["split"] = split
                    monthly_parts.append(monthly)
                    ic_mean, ic_t, ic_p = newey_west_mean_test(monthly["rank_ic"])
                    spread_mean, spread_t, spread_p = newey_west_mean_test(
                        monthly["q5_minus_q1"]
                    )
                    summaries.append(
                        {
                            "factor": factor,
                            "horizon": horizon,
                            "target_kind": target_kind,
                            "split": split,
                            "event_count": int(
                                sample[[factor, target]].dropna().shape[0]
                            ),
                            "month_count": int(len(monthly)),
                            "rank_ic_mean": ic_mean,
                            "rank_ic_t": ic_t,
                            "rank_ic_p": ic_p,
                            "q5_minus_q1_mean": spread_mean,
                            "q5_minus_q1_t": spread_t,
                            "q5_minus_q1_p": spread_p,
                            "positive_ic_month_rate": float(
                                (monthly["rank_ic"] > 0).mean()
                            ),
                        }
                    )

                yearly = (
                    factor_events[["ann_date", factor, target]]
                    .replace([np.inf, -np.inf], np.nan)
                    .dropna()
                    .assign(year=lambda value: value["ann_date"].dt.year)
                    .groupby("year", observed=True)
                    .apply(
                        lambda group: pd.Series(
                            {
                                "event_count": len(group),
                                "rank_ic": spearmanr(group[factor], group[target]).statistic
                                if group[factor].nunique() >= 3
                                else np.nan,
                                "top20_return": group.loc[
                                    group[factor].rank(pct=True) > 0.8, target
                                ].mean(),
                                "bottom20_return": group.loc[
                                    group[factor].rank(pct=True) <= 0.2, target
                                ].mean(),
                            }
                        ),
                        include_groups=False,
                    )
                    .reset_index()
                )
                yearly["factor"] = factor
                yearly["horizon"] = horizon
                yearly["target_kind"] = target_kind
                yearly_parts.append(yearly)

    summary = pd.DataFrame(summaries)
    if not summary.empty:
        summary["rank_ic_q"] = np.nan
        summary["spread_q"] = np.nan
        for _, index in summary.groupby(
            ["horizon", "target_kind", "split"], observed=True
        ).groups.items():
            index = list(index)
            valid_ic = summary.loc[index, "rank_ic_p"].notna()
            valid_spread = summary.loc[index, "q5_minus_q1_p"].notna()
            if valid_ic.any():
                valid_index = summary.loc[index].index[valid_ic]
                summary.loc[valid_index, "rank_ic_q"] = multipletests(
                    summary.loc[valid_index, "rank_ic_p"], method="fdr_bh"
                )[1]
            if valid_spread.any():
                valid_index = summary.loc[index].index[valid_spread]
                summary.loc[valid_index, "spread_q"] = multipletests(
                    summary.loc[valid_index, "q5_minus_q1_p"], method="fdr_bh"
                )[1]
    monthly_all = pd.concat(monthly_parts, ignore_index=True) if monthly_parts else pd.DataFrame()
    yearly_all = pd.concat(yearly_parts, ignore_index=True) if yearly_parts else pd.DataFrame()
    return summary, monthly_all, yearly_all


def render_report(summary: pd.DataFrame, events: pd.DataFrame) -> str:
    lines = [
        "# 持仓结构单因子审计",
        "",
        "本报告严格使用公告日后的下一可交易日开盘作为入场时点。机构占比仅覆盖已识别的前十大流通股东；分散流通盘是散户代理，不是真实散户持仓。",
        "",
        f"事件数：{len(events):,}；股票数：{events['vendor_symbol'].nunique():,}。",
        "",
        "## 20日行业/市值/动量中性结果",
        "",
    ]
    selected = summary[
        (summary["horizon"] == 20)
        & (summary["target_kind"] == "neutral")
        & (summary["split"].isin(["validation", "holdout"]))
    ].copy()
    if selected.empty:
        lines.append("没有足够样本形成20日中性结果。")
    else:
        pivot = selected.pivot_table(
            index="factor", columns="split", values=["rank_ic_mean", "q5_minus_q1_mean"]
        )
        pivot.columns = [f"{metric}_{split}" for metric, split in pivot.columns]
        pivot = pivot.reset_index()
        validation_column = "rank_ic_mean_validation"
        if validation_column in pivot:
            pivot = pivot.reindex(
                pivot[validation_column].abs().sort_values(ascending=False).index
            )
        lines.extend([pivot.head(15).to_markdown(index=False, floatfmt=".4f"), ""])
    lines.extend(
        [
            "## 判读约束",
            "",
            "- 只有验证期与留出期方向一致、留出期绝对Rank IC不低于0.02，并且多重检验后仍显著的因子，才允许进入组合模型。",
            "- 市值变化不作为主要买卖信号，持股比例和股数变化才是主变量。",
            "- 本阶段只证明预测关系，不证明机构持仓变化导致股价变化。",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", type=Path, default=ROOT / "configs" / "ownership_state.json"
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(args.config.resolve())
    processed_root = resolve(config["processed_data_dir"])
    output_root = resolve(config["output_dir"]) / "factor_audit"
    output_root.mkdir(parents=True, exist_ok=True)
    events = pd.read_parquet(processed_root / "ownership_events.parquet")
    summary, monthly, yearly = evaluate(config, events)
    summary.to_csv(output_root / "factor_summary.csv", index=False)
    monthly.to_parquet(output_root / "monthly_factor_metrics.parquet", index=False)
    yearly.to_csv(output_root / "yearly_factor_metrics.csv", index=False)
    (output_root / "report.md").write_text(
        render_report(summary, events), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "event_rows": len(events),
                "factor_tests": len(summary),
                "monthly_rows": len(monthly),
                "yearly_rows": len(yearly),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
