from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lightgbm_return_top3.feature_engine import (
    HOLD_DAYS,
    SIGNAL_ELIGIBILITY_COLUMN,
    TARGET_COLUMN,
    build_rebalance_dates,
)
from lightgbm_return_top3.model_backtest import (
    OOS_START,
    OUTPUT_DIR,
    ResearchConfig,
    TEMPLATE_CONFIGS,
    VALIDATION_END,
    _build_training_mask,
    _predict_signal_frame,
    build_period_trades,
    compute_rank_ic_by_date,
    fit_model,
    prepare_ranked_panel,
    prepare_research_context,
    select_model_features,
    summarize_periods,
)


def run_variant(
    raw_panel: pd.DataFrame,
    trading_dates: pd.DatetimeIndex,
    base_config: ResearchConfig,
    include_state_features: bool,
    template: str,
    top_k: int,
) -> dict[str, object]:
    config = ResearchConfig(
        fee_bps=base_config.fee_bps,
        workers=base_config.workers,
        sample_size=base_config.sample_size,
        sample_seed=base_config.sample_seed,
        min_avg_amount=base_config.min_avg_amount,
        min_abs_ic=base_config.min_abs_ic,
        corr_threshold=base_config.corr_threshold,
        min_feature_count=base_config.min_feature_count,
        min_ic_obs=base_config.min_ic_obs,
        min_train_rows=base_config.min_train_rows,
        template_tie_tol=base_config.template_tie_tol,
        top_k=top_k,
        include_turnover_features=base_config.include_turnover_features,
        include_auxiliary_features=base_config.include_auxiliary_features,
        include_state_features=include_state_features,
        output_prefix=base_config.output_prefix,
        test=base_config.test,
    )

    _, selected_features = select_model_features(raw_panel, config)
    ranked_panel = prepare_ranked_panel(raw_panel, selected_features)

    train_mask = _build_training_mask(ranked_panel, VALIDATION_END)
    train_frame = ranked_panel.loc[train_mask].copy()
    if len(train_frame) < config.min_train_rows:
        raise RuntimeError(f"训练样本不足：{len(train_frame)} < {config.min_train_rows}")

    model = fit_model(train_frame, selected_features, template)
    signal_dates = build_rebalance_dates(
        trading_dates=trading_dates,
        start_date=OOS_START,
        end_date=trading_dates.max(),
        step=HOLD_DAYS,
    )

    frames = []
    train_end = pd.Timestamp(VALIDATION_END)
    for signal_date in signal_dates:
        predicted = _predict_signal_frame(
            panel=ranked_panel,
            signal_date=signal_date,
            feature_columns=selected_features,
            model=model,
            template_name=template,
            fit_month=f"fixed_train_to_{VALIDATION_END[:7]}",
            train_end=train_end,
            period_label="fixed_oos",
        )
        if not predicted.empty:
            frames.append(predicted)

    predictions = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    rank_ic = compute_rank_ic_by_date(predictions)
    periods = build_period_trades(predictions, top_k=top_k, fee_bps=config.fee_bps)
    summary = summarize_periods(
        periods=periods,
        rank_ic_frame=rank_ic,
        scope="fixed_oos_2025_plus",
        template_name=template,
        selected_features=selected_features,
    )
    summary["variant"] = "with_state_features" if include_state_features else "baseline_no_state"
    summary["state_features_selected"] = int(sum(feature.startswith("behavior_") for feature in selected_features))
    summary["state_features_selected_list"] = "|".join(
        feature for feature in selected_features if feature.startswith("behavior_")
    )
    return {
        "summary": summary,
        "predictions": predictions,
        "periods": periods,
        "rank_ic": rank_ic,
        "selected_features": selected_features,
    }


def fmt_pct(value: float) -> str:
    if pd.isna(value):
        return ""
    return f"{value:.2%}"


def write_report(path: Path, comparison: pd.DataFrame, args: argparse.Namespace) -> None:
    row_map = {row["variant"]: row for _, row in comparison.iterrows()}
    baseline = row_map.get("baseline_no_state")
    state = row_map.get("with_state_features")
    lines = [
        "# LightGBM 状态特征 OOS 对照",
        "",
        "生成日期：2026-06-05",
        "",
        "## 口径",
        "",
        f"- 模型模板：`{args.template}`",
        f"- 训练窗口：2020-01-01 至 {VALIDATION_END}",
        f"- OOS：{OOS_START} 之后，每 {HOLD_DAYS} 个交易日调仓",
        f"- Top-K：{args.top_k}",
        f"- 样本股票数：{'全量' if args.sample_size is None else args.sample_size}",
        "- 对照组：baseline 关闭 `behavior_*` 状态特征；实验组默认纳入状态特征。",
        "- 这是固定训练窗口 OOS 对照，用于快速评估特征增量；完整月度扩窗结果仍以 `model_backtest.py` 为准。",
        "",
        "## 结果",
        "",
        "| variant | n_periods | mean_rank_ic | topk_hit_rate | topk_alpha_hit_rate | avg_net_return | avg_net_alpha | max_drawdown_net | state_features_selected |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for _, row in comparison.iterrows():
        lines.append(
            f"| {row['variant']} | {int(row['n_periods'])} | {row['mean_rank_ic']:.4f} | "
            f"{fmt_pct(row['topk_hit_rate'])} | {fmt_pct(row['topk_alpha_hit_rate'])} | "
            f"{fmt_pct(row['avg_net_return'])} | {fmt_pct(row['avg_net_alpha'])} | "
            f"{fmt_pct(row['max_drawdown_net'])} | {int(row['state_features_selected'])} |"
        )

    if baseline is not None and state is not None:
        lines.extend(
            [
                "",
                "## 差异",
                "",
                f"- OOS IC 差异：{state['mean_rank_ic'] - baseline['mean_rank_ic']:+.4f}",
                f"- Top-K 正收益命中率差异：{(state['topk_hit_rate'] - baseline['topk_hit_rate']) * 100:+.2f} 个百分点",
                f"- Top-K 跑赢基准命中率差异：{(state['topk_alpha_hit_rate'] - baseline['topk_alpha_hit_rate']) * 100:+.2f} 个百分点",
                f"- 最大回撤差异：{(state['max_drawdown_net'] - baseline['max_drawdown_net']) * 100:+.2f} 个百分点",
                "",
                "## 状态特征入选",
                "",
                state.get("state_features_selected_list", "") or "_无状态特征入选_",
                "",
            ]
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare LightGBM OOS with/without price behavior state features.")
    parser.add_argument("--sample-size", type=int, default=None)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--fee-bps", type=float, default=30.0)
    parser.add_argument("--min-avg-amount", type=float, default=20_000_000.0)
    parser.add_argument("--template", choices=["small", "medium", "large"], default="small")
    parser.add_argument("--n-estimators", type=int, default=None, help="临时覆盖 LightGBM n_estimators，用于快速 A/B")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--output-prefix", default="state_feature_oos_compare")
    args = parser.parse_args()
    if args.n_estimators is not None:
        TEMPLATE_CONFIGS[args.template]["n_estimators"] = args.n_estimators

    config = ResearchConfig(
        fee_bps=args.fee_bps,
        workers=args.workers,
        sample_size=args.sample_size,
        min_avg_amount=args.min_avg_amount,
        include_state_features=True,
        output_prefix=args.output_prefix,
        test=False,
    )
    raw_panel, _, trading_dates = prepare_research_context(config, verbose=True)

    results = [
        run_variant(raw_panel, trading_dates, config, False, args.template, args.top_k),
        run_variant(raw_panel, trading_dates, config, True, args.template, args.top_k),
    ]
    comparison = pd.DataFrame([result["summary"] for result in results])

    out_dir = OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    comparison_path = out_dir / f"{args.output_prefix}_summary.csv"
    comparison.to_csv(comparison_path, index=False, encoding="utf-8-sig")

    report_path = out_dir / f"{args.output_prefix}_report.md"
    write_report(report_path, comparison, args)

    for result in results:
        variant = result["summary"]["variant"]
        result["periods"].to_csv(out_dir / f"{args.output_prefix}_{variant}_periods.csv", index=False, encoding="utf-8-sig")
        result["rank_ic"].to_csv(out_dir / f"{args.output_prefix}_{variant}_rank_ic.csv", index=False, encoding="utf-8-sig")

    print(comparison.to_string(index=False))
    print(f"wrote {comparison_path}")
    print(f"wrote {report_path}")


if __name__ == "__main__":
    main()
