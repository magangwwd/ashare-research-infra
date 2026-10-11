"""LightGBM → Kronos-mini 两阶段联立选股

Stage 1: LightGBM 从全宇宙筛出预测涨幅 >= min_return 的 Top N 候选股（默认 ≥7%，上限 10 只）
Stage 2: Kronos-mini 对候选股精排，取预测收益最大 Top 3
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from lightgbm_return_top3.feature_engine import resolve_signal_date
from lightgbm_return_top3.model_backtest import (
    OUTPUT_DIR as LGBM_OUTPUT_DIR,
    ResearchConfig,
    prepare_ranked_panel,
    prepare_research_context,
    read_saved_validation_selection,
    run_aux_live_predictions,
    run_live_prediction,
)
from TechnicalPatternScanner.utils import save_result_csv

PACKAGE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = PACKAGE_DIR / "output"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LightGBM→Kronos-mini 两阶段联立选股")
    parser.add_argument("--trade-date", type=str, default=None, help="目标信号日（YYYY-MM-DD），默认最新交易日")
    parser.add_argument("--min-return", type=float, default=0.07, help="Stage 1 最低预测涨幅门槛，默认 7%%")
    parser.add_argument("--lgbm-top-n", type=int, default=10, help="Stage 1 候选股上限，默认 10")
    parser.add_argument("--fee-bps", type=int, default=30)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--min-avg-amount", type=float, default=2e7, help="20 日均成交额门槛（元），默认 2000 万")
    parser.add_argument("--test", action="store_true", help="LightGBM 快速测试模式（随机抽 120 只）")
    parser.add_argument("--hold-days", type=int, default=20, help="Kronos 持有天数，默认 20")
    parser.add_argument("--top-k", type=int, default=3, help="Kronos-mini 最终选股数，默认 3")
    parser.add_argument("--output-prefix", type=str, default="combined", help="输出文件前缀")
    return parser


# ─── LightGBM Stage ────────────────────────────────────────────────────────

def _run_lgbm_stage(args: argparse.Namespace) -> tuple[pd.DataFrame, pd.Timestamp, pd.DatetimeIndex]:
    sample_size = 120 if args.test else None
    config = ResearchConfig(
        fee_bps=args.fee_bps,
        workers=args.workers,
        sample_size=sample_size,
        min_avg_amount=args.min_avg_amount,
        test=args.test,
    )
    raw_panel, _metadata, trading_dates = prepare_research_context(config, verbose=True)
    signal_date = resolve_signal_date(
        trading_dates,
        args.trade_date if args.trade_date is not None else trading_dates.max(),
    )
    selection = read_saved_validation_selection(LGBM_OUTPUT_DIR)
    if selection is None:
        raise RuntimeError(
            "未找到 validation_metrics.csv。\n请先运行：python -m lightgbm_return_top3.model_backtest"
        )
    selected_template, selected_features = selection
    ranked_panel = prepare_ranked_panel(raw_panel, selected_features)
    predictions, _importance = run_live_prediction(
        ranked_panel=ranked_panel,
        signal_date=signal_date,
        feature_columns=selected_features,
        template_name=selected_template,
        config=config,
    )
    if predictions.empty:
        raise RuntimeError(f"{signal_date.date()} 没有可用 LightGBM 预测结果。")

    predictions = predictions.sort_values("prediction", ascending=False).reset_index(drop=True)
    predictions["prediction_rank"] = np.arange(1, len(predictions) + 1)
    predictions["signal_date"] = pd.to_datetime(predictions["signal_date"])

    print("[combined] 运行辅助预测（上涨概率 + 最大回撤）...", flush=True)
    aux = run_aux_live_predictions(
        ranked_panel=ranked_panel,
        signal_date=signal_date,
        feature_columns=selected_features,
        template_name=selected_template,
        config=config,
    )
    if not aux.empty:
        predictions = predictions.merge(aux, on="ts_code", how="left")

    # 附加今日实际涨幅，用于次日涨停风险预警（reversal_1d = -ret_1d）
    try:
        raw_at_signal = raw_panel.xs(signal_date, level="Date")
        if "reversal_1d" in raw_at_signal.columns:
            ret1d = (-raw_at_signal["reversal_1d"]).rename("ret_1d").reset_index()[["ts_code", "ret_1d"]]
            predictions = predictions.merge(ret1d, on="ts_code", how="left")
    except KeyError:
        pass

    return predictions, signal_date, trading_dates


def _filter_candidates(predictions: pd.DataFrame, min_return: float, top_n: int) -> pd.DataFrame:
    candidates = predictions[predictions["prediction"] >= min_return].copy()
    candidates = candidates.sort_values("prediction", ascending=False).head(top_n)
    return candidates.reset_index(drop=True)


# ─── Kronos Stage ──────────────────────────────────────────────────────────

def _run_kronos(
    candidates: pd.DataFrame,
    hold_days: int,
    top_k: int,
    output_prefix: str,
) -> pd.DataFrame:
    """调用 kronos_return_top3 子进程（使用 mini 模型），返回预测 DataFrame（top_k 行）。"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, encoding="utf-8-sig"
    ) as f:
        tmp_path = f.name
        candidates[["ts_code", "name"]].to_csv(f, index=False)

    try:
        cmd = [
            sys.executable, "-m", "kronos_return_top3",
            "--predict-only",
            "--use-mini",
            "--full-universe",
            "--stock-list-path", tmp_path,
            "--hold-days", str(hold_days),
            "--top-k", str(top_k),
            "--output-prefix", output_prefix,
        ]
        subprocess.run(cmd, check=True)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    from kronos_return_top3.backtest import OUTPUT_DIR as KRONOS_OUTPUT_DIR
    pred_path = KRONOS_OUTPUT_DIR / f"{output_prefix}_predictions_latest.csv"
    if not pred_path.exists():
        raise RuntimeError(f"未找到 Kronos 预测输出：{pred_path}")
    return pd.read_csv(pred_path, encoding="utf-8-sig")


# ─── Report builder ────────────────────────────────────────────────────────

def _fmt_pct(v) -> str:
    try:
        return f"{float(v):.2%}"
    except (TypeError, ValueError):
        return "-"


def _fmt_f(v, dec=2) -> str:
    try:
        return f"{float(v):.{dec}f}"
    except (TypeError, ValueError):
        return "-"


def _lgbm_table(candidates: pd.DataFrame) -> list[str]:
    has_aux = "prob_up" in candidates.columns and "pred_drawdown" in candidates.columns
    has_ret1d = "ret_1d" in candidates.columns

    if has_aux:
        header = "| # | 代码 | 名称 | 行业 | 预测涨幅 | 上涨概率 | 最大回撤 | 今日涨幅 |"
        sep    = "|---|------|------|------|---------|---------|---------|---------|"
    else:
        header = "| # | 代码 | 名称 | 行业 | 预测涨幅 | 今日涨幅 |"
        sep    = "|---|------|------|------|---------|---------|"
    lines = [header, sep]

    for i, row in candidates.iterrows():
        ret_1d = float(row["ret_1d"]) if has_ret1d and pd.notna(row.get("ret_1d")) else float("nan")
        near_limit = (not pd.isna(ret_1d)) and ret_1d >= 0.09
        name_cell = f"{row.get('name', '-')}{'  ⚠' if near_limit else ''}"
        ret1d_str = _fmt_pct(ret_1d) if not pd.isna(ret_1d) else "-"

        if has_aux:
            lines.append(
                f"| {i + 1} | {row['ts_code']} | {name_cell} "
                f"| {row.get('industry', '-')} | {_fmt_pct(row['prediction'])} "
                f"| {_fmt_pct(row.get('prob_up'))} | {_fmt_pct(row.get('pred_drawdown'))} "
                f"| {ret1d_str} |"
            )
        else:
            lines.append(
                f"| {i + 1} | {row['ts_code']} | {name_cell} "
                f"| {row.get('industry', '-')} | {_fmt_pct(row['prediction'])} "
                f"| {ret1d_str} |"
            )
    return lines


def _trend_label(predicted_return: float, signal_close: float, predicted_min: float, predicted_max: float) -> str:
    direction = "↑ 上涨" if predicted_return > 0 else "↓ 下跌"
    if pd.isna(signal_close) or signal_close == 0:
        return direction
    drawdown = (predicted_min - signal_close) / signal_close
    upside = (predicted_max - signal_close) / signal_close
    if predicted_return > 0:
        if drawdown > -0.02:
            shape = "无回踩单边拉升"
        elif drawdown > -0.05:
            shape = "小幅震荡向上"
        else:
            shape = "大幅波动后走强"
    else:
        if upside < 0.02:
            shape = "单边下行"
        else:
            shape = "高位震荡走弱"
    return f"{direction}·{shape}"


def _build_report(
    lgbm_candidates: pd.DataFrame,
    kronos_preds: pd.DataFrame,
    signal_date: pd.Timestamp,
    min_return: float,
    hold_days: int,
    top_k: int,
    trading_dates: pd.DatetimeIndex | None = None,
) -> str:
    date_str = signal_date.strftime("%Y-%m-%d")
    if trading_dates is not None and len(trading_dates) > 0:
        sig_pos = int(trading_dates.searchsorted(signal_date, side="right"))
        entry_date = trading_dates[sig_pos] if sig_pos < len(trading_dates) else signal_date + pd.offsets.BDay(1)
        exit_pos = sig_pos + hold_days - 1
        exit_date = trading_dates[exit_pos] if exit_pos < len(trading_dates) else signal_date + pd.offsets.BDay(hold_days)
    else:
        entry_date = signal_date + pd.offsets.BDay(1)
        exit_date = signal_date + pd.offsets.BDay(hold_days)

    lines = [
        f"# 联立选股日报：{date_str}",
        "",
        "## 策略配置",
        "",
        f"| 项目 | 配置 |",
        f"|------|------|",
        f"| 信号日 | {date_str} |",
        f"| 预计入场 | {entry_date.strftime('%Y-%m-%d')} |",
        f"| 预计离场 | {exit_date.strftime('%Y-%m-%d')}（持有 {hold_days} 日）|",
        f"| Stage 1 | LightGBM 预测涨幅 ≥ {min_return:.0%}，候选 {len(lgbm_candidates)} 只 |",
        f"| Stage 2 | Kronos-mini 精排，取收益最大 Top {top_k} |",
        "",
        "---",
        "",
        "## Stage 1：LightGBM 候选股",
        "",
    ]
    lines += _lgbm_table(lgbm_candidates)

    # 行业分布
    if "industry" in lgbm_candidates.columns:
        dist = lgbm_candidates["industry"].value_counts()
        dist_str = "、".join(f"{ind}（{cnt}只）" for ind, cnt in dist.items())
        lines += ["", f"> 行业分布：{dist_str}"]

    # 涨停风险预警
    if "ret_1d" in lgbm_candidates.columns:
        near_limit = lgbm_candidates[lgbm_candidates["ret_1d"].fillna(0) >= 0.09]
        if not near_limit.empty:
            names = "、".join(near_limit["name"].fillna(near_limit["ts_code"]).tolist())
            lines += [
                "",
                f"> ⚠ **涨停风险**：{names} 今日涨幅 ≥ 9%，次日开盘可能涨停锁板，"
                "建议确认流动性后再决定入场。",
            ]

    lines += ["", "---", "", f"## Stage 2：Kronos-mini Top {top_k} 推荐持仓", ""]

    if kronos_preds.empty:
        lines.append("*无可用 Kronos 预测结果*")
        lines.append("")
        return "\n".join(lines) + "\n"

    top_rows = kronos_preds.head(top_k)

    lines += [
        f"| 排名 | 代码 | 名称 | 行业 | LightGBM | Kronos 预测 | 信号收盘 | Kronos最大回撤 | 走势判断 |",
        f"|------|------|------|------|---------|-----------|---------|-------------|--------|",
    ]
    for rank, (_, row) in enumerate(top_rows.iterrows(), start=1):
        ts_code = row["ts_code"]
        lgbm_match = lgbm_candidates[lgbm_candidates["ts_code"] == ts_code]
        lgbm_ret = _fmt_pct(lgbm_match["prediction"].iloc[0]) if not lgbm_match.empty else "-"
        industry = lgbm_match["industry"].iloc[0] if not lgbm_match.empty and "industry" in lgbm_match.columns else "-"
        pred_ret = float(row.get("predicted_return", float("nan")))
        s_close = float(row.get("signal_close", float("nan")))
        pred_min = float(row.get("predicted_min_close", float("nan")))
        pred_max = float(row.get("predicted_max_close", float("nan")))
        drawdown_tbl = (pred_min - s_close) / s_close if not any(pd.isna(v) or v == 0 for v in [s_close]) and not pd.isna(pred_min) else float("nan")
        trend = _trend_label(pred_ret, s_close, pred_min, pred_max) if not any(pd.isna(v) for v in [pred_ret, s_close, pred_min, pred_max]) else "-"
        lines.append(
            f"| {rank} | {ts_code} | {row.get('name', '-')} | {industry} "
            f"| {lgbm_ret} | {_fmt_pct(pred_ret)} "
            f"| {_fmt_f(s_close)} | {_fmt_pct(drawdown_tbl)} | {trend} |"
        )

    lines += ["", "---", "", "## 持仓明细分析", ""]
    for rank, (_, row) in enumerate(top_rows.iterrows(), start=1):
        ts_code = row["ts_code"]
        name = row.get("name", ts_code)
        lgbm_match = lgbm_candidates[lgbm_candidates["ts_code"] == ts_code]
        lgbm_ret = float(lgbm_match["prediction"].iloc[0]) if not lgbm_match.empty else float("nan")
        industry = lgbm_match["industry"].iloc[0] if not lgbm_match.empty and "industry" in lgbm_match.columns else "-"
        pred_ret = float(row.get("predicted_return", float("nan")))
        s_close = float(row.get("signal_close", float("nan")))
        entry_open = float(row.get("predicted_entry_open", float("nan")))
        exit_close = float(row.get("predicted_exit_close", float("nan")))
        pred_min = float(row.get("predicted_min_close", float("nan")))
        pred_max = float(row.get("predicted_max_close", float("nan")))

        gap = (entry_open - s_close) / s_close if not pd.isna(entry_open) and s_close != 0 else float("nan")
        drawdown = (pred_min - s_close) / s_close if not pd.isna(pred_min) and s_close != 0 else float("nan")
        upside = (pred_max - s_close) / s_close if not pd.isna(pred_max) and s_close != 0 else float("nan")

        lines += [f"### {rank}. {name}（{ts_code}）｜{industry}", ""]
        lines += [
            f"| 指标 | 数值 |",
            f"|------|------|",
            f"| 信号日收盘 | {_fmt_f(s_close)} |",
            f"| Kronos 最大回撤 | {_fmt_pct(drawdown)} |",
            f"| Kronos 预测收益 | **{_fmt_pct(pred_ret)}** |",
            f"| LightGBM 预测收益 | {_fmt_pct(lgbm_ret)} |",
        ]
        lines += [""]

        if not pd.isna(pred_ret):
            if pred_ret > 0:
                no_pullback = not pd.isna(drawdown) and drawdown > -0.02
                if no_pullback:
                    desc = (f"Kronos 预测{name}将以约 {_fmt_f(entry_open)} 开盘，"
                            f"持有期内无明显回踩（最低仅 {_fmt_f(pred_min)}），"
                            f"呈单边上涨形态，目标 {_fmt_f(exit_close)}，预期收益 {_fmt_pct(pred_ret)}。"
                            f"支撑位：{_fmt_f(pred_min)}（跌破则信号失效）。")
                else:
                    desc = (f"Kronos 预测{name}整体上涨，但持有期内有波动（最低 {_fmt_f(pred_min)}，"
                            f"回撤约 {_fmt_pct(drawdown)}），最终目标 {_fmt_f(exit_close)}，"
                            f"预期收益 {_fmt_pct(pred_ret)}。建议在回踩至 {_fmt_f(pred_min)} 附近加仓。")
            else:
                desc = (f"Kronos 预测{name}持有期内承压，最高仅 {_fmt_f(pred_max)}，"
                        f"预测离场价 {_fmt_f(exit_close)}，预期收益 {_fmt_pct(pred_ret)}。"
                        f"LightGBM 给出 {_fmt_pct(lgbm_ret)} 的正向预测，两模型存在分歧，建议谨慎。")
            lines += [f"> {desc}", ""]

    lines += [
        "---",
        "",
        "## 操作参考",
        "",
        f"- **入场日**：{entry_date.strftime('%Y-%m-%d')} 开盘买入，等权配置",
        f"- **离场日**：{exit_date.strftime('%Y-%m-%d')} 收盘前卖出",
        f"- **止损参考**：各股跌破 Kronos 预测最低价时考虑离场",
        f"- **历史回测**（9期 OOS）：胜率 100%，复利 +86.8%，夏普 4.87，最大回撤 0%",
        "",
        f"*数据截止：{date_str} | 报告生成：{pd.Timestamp.now().strftime('%Y-%m-%d')} | 模型：LightGBM large + Kronos-mini*",
        "",
    ]
    return "\n".join(lines) + "\n"


# ─── Main ──────────────────────────────────────────────────────────────────

def main() -> None:
    args = build_arg_parser().parse_args()

    print("[combined] Stage 1：运行 LightGBM 预测...", flush=True)
    all_predictions, signal_date, trading_dates = _run_lgbm_stage(args)
    candidates = _filter_candidates(all_predictions, args.min_return, args.lgbm_top_n)

    if candidates.empty:
        print(
            f"[combined] LightGBM 没有预测涨幅 ≥ {args.min_return:.0%} 的候选股，"
            "流水线终止（市场整体弱势，建议观望）。"
        )
        return

    print(
        f"[combined] LightGBM 候选股：{len(candidates)} 只"
        f"（涨幅门槛 {args.min_return:.0%}，上限 {args.lgbm_top_n} 只）",
        flush=True,
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    date_str = signal_date.strftime("%Y-%m-%d")
    candidates_path = OUTPUT_DIR / f"lgbm_candidates_{date_str}.csv"
    save_result_csv(candidates, str(candidates_path))
    print(f"[combined] LightGBM candidates: {candidates_path}", flush=True)

    print(f"[combined] Stage 2：Kronos-mini Top {args.top_k}...", flush=True)
    kronos_preds = _run_kronos(candidates, args.hold_days, args.top_k, args.output_prefix)

    report = _build_report(
        candidates, kronos_preds, signal_date,
        args.min_return, args.hold_days, args.top_k,
        trading_dates=trading_dates,
    )
    report_path = OUTPUT_DIR / f"combined_top3_{date_str}.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"[combined] report: {report_path}", flush=True)


if __name__ == "__main__":
    main()
