"""
生成综合报告：financial_data/report.md
"""

import os
from datetime import datetime, timedelta

import pandas as pd

from .config import DATA_DIR, DAILY_DATA_DIR, RATING_THRESHOLDS


def _fmt_pct(val, decimals=1) -> str:
    try:
        return f"{float(val) * 100:.{decimals}f}%"
    except (TypeError, ValueError):
        return "N/A"


def _fmt_num(val, decimals=1) -> str:
    try:
        return f"{float(val):.{decimals}f}"
    except (TypeError, ValueError):
        return "N/A"


def _calc_industry_momentum(df_scores: pd.DataFrame, lookback_days: int) -> dict[str, float]:
    """
    计算各行业近 lookback_days 天的涨跌幅中位数。
    返回 {industry: median_return}，行业内有效股票 < 5 只时不纳入。
    """
    cutoff = (datetime.today() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    ind_returns: dict[str, list[float]] = {}

    for _, row in df_scores.iterrows():
        code = row["ts_code"]
        ind  = row.get("industry", "")
        if not ind:
            continue
        path = os.path.join(DAILY_DATA_DIR, f"{code}.csv")
        if not os.path.exists(path):
            continue
        try:
            tmp = pd.read_csv(path, usecols=["Date", "Close"],
                              encoding="utf-8", dtype={"Date": str, "Close": float})
            tmp = tmp[tmp["Date"] >= cutoff].sort_values("Date")
            if len(tmp) < 2:
                continue
            start_p = tmp["Close"].iloc[0]
            end_p   = tmp["Close"].iloc[-1]
            if start_p > 0:
                ind_returns.setdefault(ind, []).append((end_p - start_p) / start_p)
        except Exception:
            continue

    return {ind: float(pd.Series(v).median())
            for ind, v in ind_returns.items() if len(v) >= 5}


def _cycle_label(industry: str, mom6m: dict, mom1y: dict) -> str:
    r6  = mom6m.get(industry)
    r1y = mom1y.get(industry)
    if r6 is not None and r6 < -0.10:
        if r1y is not None and r1y < -0.10:
            return "景气↓"
        return "近期↓"
    return "-"


def generate_report(df: pd.DataFrame | None = None) -> str:
    if df is None:
        scores_path = os.path.join(DATA_DIR, "scores.csv")
        if not os.path.exists(scores_path):
            raise FileNotFoundError("scores.csv 不存在，请先运行评分步骤")
        df = pd.read_csv(scores_path, encoding="utf-8-sig")

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    total_stocks = len(df)

    # 行业景气（计算略慢，读日线文件）
    print("计算行业景气动量...")
    mom6m = _calc_industry_momentum(df, 180)
    mom1y = _calc_industry_momentum(df, 365)

    lines = [
        "# 财务评级报告",
        "",
        f"> 生成时间：{now}  |  覆盖股票：{total_stocks} 只",
        "",
    ]

    # 评级分布（含实际得分区间，动态阈值下每次运行可能不同）
    lines += ["## 评级分布", ""]
    lines += ["| 评级 | 含义 | 数量 | 占比 | 得分区间 |",
              "|------|------|-----:|-----:|---------|"]
    labels = {"A+": "卓越", "A": "优质", "B+": "良好", "B": "中等", "C": "一般", "D": "风险关注"}
    rating_order = ["A+", "A", "B+", "B", "C", "D"]
    for rating in rating_order:
        sub = df[df["rating"] == rating]["total_score"]
        if sub.empty:
            continue
        cnt = len(sub)
        pct = cnt / total_stocks * 100 if total_stocks else 0
        score_range = f"{sub.min():.1f}–{sub.max():.1f}"
        lines.append(f"| {rating} | {labels.get(rating, '')} | {cnt} | {pct:.1f}% | {score_range} |")
    lines.append("")

    # 得分统计
    lines += [
        "## 得分统计",
        "",
        f"- 平均分：{df['total_score'].mean():.1f}",
        f"- 中位数：{df['total_score'].median():.1f}",
        f"- 最高分：{df['total_score'].max():.1f}",
        f"- 最低分：{df['total_score'].min():.1f}",
        "",
    ]

    # Top 20（新增 PE、PB、估值分、景气列）
    lines += ["## Top 20 优质股票", ""]
    lines += [
        "| 排名 | 代码 | 名称 | 行业 | 总分 | 评级 | ROE趋势 | 营收CAGR | 净利CAGR | 分红年数 | 资产负债率 | PE(TTM) | PB | 景气 |",
        "|----:|------|------|------|-----:|------|--------:|--------:|--------:|--------:|----------:|--------:|---:|------|",
    ]
    top20 = df.head(20)
    for rank, (_, r) in enumerate(top20.iterrows(), 1):
        roe_delta = r.get("roe_delta")
        roe_str = (f"{float(roe_delta):+.1f}pp"
                   if roe_delta is not None and str(roe_delta) not in ("", "nan", "None")
                   else "N/A")
        pe_str = _fmt_num(r.get("pe_ttm"), 1) if r.get("pe_ttm") else "N/A"
        pb_str = _fmt_num(r.get("pb"), 2) if r.get("pb") else "N/A"
        cyc    = _cycle_label(r.get("industry", ""), mom6m, mom1y)
        lines.append(
            f"| {rank} | {r['ts_code']} | {r['name']} | {r.get('industry', '')} "
            f"| {_fmt_num(r['total_score'])} | {r['rating']} "
            f"| {roe_str} "
            f"| {_fmt_pct(r.get('revenue_cagr_3yr'))} "
            f"| {_fmt_pct(r.get('profit_cagr_3yr'))} "
            f"| {r.get('dividend_years', 'N/A')} "
            f"| {_fmt_num(r.get('debt_to_assets'))}% "
            f"| {pe_str} | {pb_str} | {cyc} |"
        )
    lines.append("")

    # Bottom 10 风险股
    lines += ["## Bottom 10 风险关注", ""]
    lines += ["| 代码 | 名称 | 行业 | 总分 | 评级 | 是否ST | 审计意见 | 资产负债率 |",
              "|------|------|------|-----:|------|--------|----------|----------:|"]
    bottom10 = df.tail(10).iloc[::-1]
    for _, r in bottom10.iterrows():
        is_st = "是" if r.get("is_st") else "否"
        audit = str(r.get("audit_latest", "") or "")[:20]
        lines.append(
            f"| {r['ts_code']} | {r['name']} | {r.get('industry', '')} "
            f"| {_fmt_num(r['total_score'])} | {r['rating']} "
            f"| {is_st} | {audit} | {_fmt_num(r.get('debt_to_assets'))}% |"
        )
    lines.append("")

    # 行业平均分排行
    if "industry" in df.columns:
        ind_avg = (
            df.groupby("industry")["total_score"]
            .agg(["mean", "count"])
            .rename(columns={"mean": "avg_score", "count": "stock_count"})
            .sort_values("avg_score", ascending=False)
            .reset_index()
        )
        ind_avg = ind_avg[ind_avg["stock_count"] >= 3]
        lines += ["## 行业平均分排行（≥3只）", ""]
        lines += ["| 行业 | 平均分 | 股票数 |", "|------|-------:|------:|"]
        for _, r in ind_avg.iterrows():
            lines.append(f"| {r['industry']} | {r['avg_score']:.1f} | {int(r['stock_count'])} |")
        lines.append("")

    # 行业景气警示
    # 优先从 scores.csv 的 cycle_discount 列获取折扣行业（与评分逻辑一致）
    if "cycle_discount" in df.columns:
        discounted_inds = set(df[df["cycle_discount"] == True]["industry"].dropna())
    else:
        discounted_inds = {ind for ind, r6 in mom6m.items() if r6 < -0.10}

    warn_inds = {ind: (mom6m.get(ind), mom1y.get(ind)) for ind in discounted_inds}
    if warn_inds:
        lines += ["## 行业景气警示（6M涨跌幅中位 < -10%，评分已×0.85折扣）", ""]
        lines += [
            "| 行业 | 6M中位涨跌 | 1Y中位涨跌 | 折扣后高分股(A/B+) |",
            "|------|----------:|----------:|------------------:|",
        ]
        high_rating_codes = set(df[df["rating"].isin(["A", "B+"])]["ts_code"])
        for ind in sorted(warn_inds, key=lambda x: (warn_inds[x][0] or 0)):
            r6, r1y = warn_inds[ind]
            high_cnt = df[(df["industry"] == ind) & (df["ts_code"].isin(high_rating_codes))].shape[0]
            r6_str  = f"{r6 * 100:+.1f}%" if r6 is not None else "N/A"
            r1y_str = f"{r1y * 100:+.1f}%" if r1y is not None else "N/A"
            lines.append(f"| {ind} | {r6_str} | {r1y_str} | {high_cnt} |")
        lines.append("")

    content = "\n".join(lines)
    out_path = os.path.join(DATA_DIR, "report.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"报告已保存至 {out_path}")
    return out_path
