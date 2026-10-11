from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from TechnicalPatternScanner.utils import save_result_csv

from .feature_engine import HOLD_DAYS, build_rebalance_dates, resolve_signal_date
from .model_backtest import (
    OOS_START,
    OUTPUT_DIR,
    ResearchConfig,
    VALIDATION_START,
    build_arg_parser as build_backtest_arg_parser,
    prepare_ranked_panel,
    prepare_research_context,
    read_saved_validation_selection,
    run_live_prediction,
)


def _format_pct(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):.2%}"


def _feature_label(feature: str) -> str:
    return feature.replace("_", " ")


def _report_schedule_start(signal_date: pd.Timestamp) -> pd.Timestamp | None:
    if signal_date >= pd.Timestamp(OOS_START):
        return pd.Timestamp(OOS_START)
    if signal_date >= pd.Timestamp(VALIDATION_START):
        return pd.Timestamp(VALIDATION_START)
    return None


def build_driver_summary(
    row: pd.Series,
    importance_frame: pd.DataFrame,
    feature_columns: list[str],
) -> str:
    if importance_frame.empty:
        return "缺少特征重要性输出"

    ranked = (
        importance_frame[importance_frame["feature"].isin(feature_columns)]
        .sort_values("gain_importance", ascending=False)
    )
    # Pre-extract (feature, value) pairs in importance order, dropping NaN
    fv_pairs = [
        (str(r["feature"]), float(row.get(str(r["feature"]), np.nan)))
        for _, r in ranked.iterrows()
    ]
    non_null = [(f, v) for f, v in fv_pairs if not np.isnan(v)]

    extremes = [
        f"{_feature_label(f)} {'高分位' if v >= 0.75 else '低分位'}({v:.2f})"
        for f, v in non_null[:10]
        if v >= 0.75 or v <= 0.25
    ][:4]
    if extremes:
        return "；".join(extremes)

    fallback = [f"{_feature_label(f)}({v:.2f})" for f, v in non_null[:4]]
    return "；".join(fallback) if fallback else "截面分位没有明显极值"


def build_markdown_report(
    predictions: pd.DataFrame,
    signal_date: pd.Timestamp,
    selected_template: str,
    selected_features: list[str],
    importance_frame: pd.DataFrame,
    on_rebalance_schedule: bool,
    schedule_anchor: pd.Timestamp | None,
) -> str:
    top3 = predictions.head(3).copy()
    top_importance = (
        importance_frame[importance_frame["feature"].isin(selected_features)]
        .sort_values("gain_importance", ascending=False)
        .head(15)
    )

    lines: list[str] = []
    lines.append(f"# LightGBM Top3 报告 - {signal_date.strftime('%Y-%m-%d')}")
    lines.append("")
    lines.append(f"- 信号日期：{signal_date.strftime('%Y-%m-%d')}")
    lines.append(f"- 模型模板：`{selected_template}`")
    lines.append(f"- 特征数量：{len(selected_features)}")
    if on_rebalance_schedule:
        lines.append(f"- 是否为 {HOLD_DAYS} 日非重叠调仓日：**是** — 本日属于回测序列的正式调仓点，Top3 预测可作为换仓依据。")
    else:
        lines.append(f"- 是否为 {HOLD_DAYS} 日非重叠调仓日：否 — ⚠️ **本日不在官方调仓序列上，仅供观察，不应据此换仓。**")
    if schedule_anchor is not None:
        lines.append(
            f"- 调仓序列说明：自 {schedule_anchor.strftime('%Y-%m-%d')} 起每 {HOLD_DAYS} 个交易日触发一次换仓。"
            f"回测绩效（胜率 / Sharpe / 复合收益）均基于该非重叠序列统计；"
            f"在序列外日期额外操作会造成持仓重叠、换手成本重复计算，导致实际表现偏离回测假设。"
        )
    lines.append(f"- 候选股票数：{len(predictions)}")
    lines.append("")

    lines.append("## Top3")
    lines.append("")
    lines.append("| 排名 | 代码 | 名称 | 行业 | 预测20日收益 | 收盘价 | 20日均成交额 | 主要驱动 |")
    lines.append("| --- | --- | --- | --- | ---: | ---: | ---: | --- |")
    for i, (_, row) in enumerate(top3.iterrows(), start=1):
        amount_yi = row["avg_amount_20d"] / 1e8
        liquidity_warn = " ⚠️低流动" if amount_yi < 5.0 else ""
        lines.append(
            f"| {i} | {row['ts_code']} | {row['name']} | {row['industry']} | "
            f"{_format_pct(row['prediction'])} | {row['signal_close']:.2f} | "
            f"{amount_yi:.2f} 亿{liquidity_warn} | {build_driver_summary(row, importance_frame, selected_features)} |"
        )
    lines.append("")

    lines.append("## 审计提示")
    lines.append("")
    lines.append(
        "- 当前回测训练集和历史候选池只使用信号日可见的 `signal_eligible` 条件，"
        "成交约束单独放在交易执行层评估。"
    )
    lines.append(
        "- 简化执行层会把次日开盘缺失、次日零成交量和次日近似开盘涨停视为未成交，"
        "未成交仓位按现金处理。"
    )
    lines.append("")

    lines.append("## 交易口径")
    lines.append("")
    lines.append(
        f"- 回测口径固定为信号日后第 1 个交易日开盘买入，持有 {HOLD_DAYS} 个交易日后收盘卖出。"
    )
    lines.append(
        "- 日报输出的是当日截面预测结果；如果不是官方调仓日，应视为观察名单，而不是回测序列中的正式换仓点。"
    )
    lines.append("- 当前版本仍未处理跌停顺延、停牌冻结、冲击成本和真实撮合。")
    lines.append("")

    if not top_importance.empty:
        lines.append("## 最新模型重要性 Top15")
        lines.append("")
        lines.append("| 因子 | Gain Importance | Split Importance |")
        lines.append("| --- | ---: | ---: |")
        for _, row in top_importance.iterrows():
            lines.append(
                f"| {_feature_label(row['feature'])} | {float(row['gain_importance']):.2f} | {float(row['split_importance']):.0f} |"
            )
        lines.append("")

    lines.append("## Top3 详情")
    lines.append("")
    for i, (_, row) in enumerate(top3.iterrows(), start=1):
        lines.append(f"### {i}. {row['ts_code']} {row['name']}")
        lines.append("")
        lines.append(f"- 预测 20 日收益：{_format_pct(row['prediction'])}")
        lines.append(f"- 行业：{row['industry']}")
        lines.append(f"- 信号日收盘价：{row['signal_close']:.2f}")
        lines.append(f"- 20 日均成交额：{row['avg_amount_20d'] / 1e8:.2f} 亿")
        lines.append(f"- 主要驱动：{build_driver_summary(row, importance_frame, selected_features)}")
        lines.append("")

    return "\n".join(lines) + "\n"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = build_backtest_arg_parser(include_ab_args=False)
    parser.description = "LightGBM 20日收益 Top3 日报"
    parser.add_argument("--trade-date", type=str, default=None, help="目标信号日，默认使用最新交易日")
    parser.add_argument(
        "--rebalance-anchor",
        type=str,
        default=None,
        help=(
            "自定义调仓序列锚点（格式 YYYY-MM-DD）。"
            "默认锚点为 OOS_START（2025-01-02），每 20 个交易日触发一次。"
            "在漏掉某期或节假日错位后，可用此参数将序列平移到新起点，"
            "重新对齐后续调仓日。注意：平移后历史回测统计仍基于原锚点，"
            "新序列的实际表现需自行积累。"
        ),
    )
    return parser


def parse_args() -> tuple[ResearchConfig, str | None, str | None]:
    args = build_arg_parser().parse_args()
    sample_size = args.sample_size
    if args.test and sample_size is None:
        sample_size = 120
    config = ResearchConfig(
        fee_bps=args.fee_bps,
        workers=args.workers,
        sample_size=sample_size,
        min_avg_amount=args.min_avg_amount,
        test=args.test,
    )
    return config, args.trade_date, args.rebalance_anchor


def main() -> None:
    config, trade_date_arg, rebalance_anchor_arg = parse_args()
    raw_panel, metadata, trading_dates = prepare_research_context(config, verbose=True)
    signal_date = resolve_signal_date(
        trading_dates,
        trade_date_arg if trade_date_arg is not None else trading_dates.max(),
    )

    selection = read_saved_validation_selection(OUTPUT_DIR)
    if selection is None:
        raise RuntimeError(
            "未找到有效的 validation_metrics.csv。\n"
            "请先运行：python -m lightgbm_return_top3.model_backtest"
        )
    selected_template, selected_features = selection
    ranked_panel = prepare_ranked_panel(raw_panel, selected_features)

    predictions, importance = run_live_prediction(
        ranked_panel=ranked_panel,
        signal_date=signal_date,
        feature_columns=selected_features,
        template_name=selected_template,
        config=config,
    )
    if predictions.empty:
        raise RuntimeError(f"{signal_date.date()} 没有可用预测结果。")

    predictions = predictions.sort_values("prediction", ascending=False).reset_index(drop=True)
    predictions["prediction_rank"] = np.arange(1, len(predictions) + 1)
    predictions["signal_date"] = pd.to_datetime(predictions["signal_date"])

    if rebalance_anchor_arg is not None:
        schedule_start = resolve_signal_date(trading_dates, rebalance_anchor_arg)
        print(f"[daily_report] 使用自定义调仓锚点：{schedule_start.date()}（原默认锚点 {OOS_START}）", flush=True)
    else:
        schedule_start = _report_schedule_start(signal_date)
    rebalance_dates = build_rebalance_dates(
        trading_dates=trading_dates,
        start_date=schedule_start,
        end_date=trading_dates.max(),
        step=HOLD_DAYS,
    )
    predictions["on_rebalance_schedule"] = predictions["signal_date"].isin(rebalance_dates)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    prediction_path = save_result_csv(predictions, str(OUTPUT_DIR / "predictions_latest.csv"))

    schedule_anchor = rebalance_dates[0] if len(rebalance_dates) else None
    on_rebalance_schedule = bool(signal_date in set(rebalance_dates))
    report_text = build_markdown_report(
        predictions=predictions,
        signal_date=signal_date,
        selected_template=selected_template,
        selected_features=selected_features,
        importance_frame=importance,
        on_rebalance_schedule=on_rebalance_schedule,
        schedule_anchor=schedule_anchor,
    )
    report_path = OUTPUT_DIR / f"lightgbm_top3_{signal_date.strftime('%Y-%m-%d')}.md"
    report_path.write_text(report_text, encoding="utf-8")

    print(f"[daily_report] predictions_latest.csv: {prediction_path}", flush=True)
    print(f"[daily_report] markdown: {report_path}", flush=True)


if __name__ == "__main__":
    main()
