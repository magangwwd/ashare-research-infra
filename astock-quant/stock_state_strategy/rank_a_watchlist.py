"""Research-only ranking for a verified A-pool snapshot.

Does not alter frozen V1, original labels, or authorization to trade.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from pathlib import Path

REQUIRED = {
    "vendor_symbol", "name_zh", "trade_date", "pool_status", "state_cn",
    "confidence", "stable_days_5", "state_margin", "state_score",
    "average_turnover_cny_20", "holder_disclosure_age_days",
    "composition_disclosure_age_days",
}
COLUMNS = [
    "research_rank", "priority_tier", "research_priority_score",
    "vendor_symbol", "name_zh", "state_cn", "confidence", "stable_days_5",
    "state_score", "state_margin", "avg_turnover_20_100m_cny",
    "holder_disclosure_age_days", "composition_disclosure_age_days",
    "max_disclosure_age_days", "holder_announcement_date",
    "composition_announcement_date", "disclosure_attention",
]
FRESH_DAYS = 120


def clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def numeric(row: dict, key: str) -> float:
    raw = (row.get(key) or "").strip()
    if not raw:
        raise ValueError("Missing " + key + " for " + row.get("vendor_symbol", "?"))
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError("Nonfinite " + key)
    return value


def score_row(row: dict) -> dict:
    if row["pool_status"] != "A_STRATEGY_CANDIDATE":
        raise ValueError("Unexpected non-A stock: " + row["vendor_symbol"])
    c = numeric(row, "confidence")
    stable = numeric(row, "stable_days_5")
    margin = numeric(row, "state_margin")
    state_score = numeric(row, "state_score")
    turnover = numeric(row, "average_turnover_cny_20")
    holder_age = numeric(row, "holder_disclosure_age_days")
    comp_age = numeric(row, "composition_disclosure_age_days")
    if min(turnover, holder_age, comp_age, stable) < 0 or turnover == 0:
        raise ValueError("Invalid input metric for " + row["vendor_symbol"])
    older = max(holder_age, comp_age)
    fresh = older <= FRESH_DAYS
    # Deterministic supplementary research ranking, not a return estimate.
    # 30 confidence, 20 five-day stability, 20 disclosure age,
    # 20 margin/separation, 10 log trading liquidity.
    score = (
        30 * clip((c - 80) / 10, 0, 1)
        + 20 * clip((stable - 3) / 2, 0, 1)
        + 20 * clip((180 - older) / 180, 0, 1)
        + 20 * clip((margin - 10) / 20, 0, 1)
        + 10 * clip(math.log10(max(1, turnover / 1e8)) / math.log10(20), 0, 1)
    )
    return {
        "research_rank": 0,
        "priority_tier": "P1_DISCLOSURES_WITHIN_120D" if fresh else "P2_RECHECK_DISCLOSURES",
        "research_priority_score": round(score, 2),
        "vendor_symbol": row["vendor_symbol"], "name_zh": row["name_zh"],
        "state_cn": row["state_cn"], "confidence": round(c, 2),
        "stable_days_5": int(stable), "state_score": round(state_score, 2),
        "state_margin": round(margin, 2),
        "avg_turnover_20_100m_cny": round(turnover / 1e8, 2),
        "holder_disclosure_age_days": int(holder_age),
        "composition_disclosure_age_days": int(comp_age),
        "max_disclosure_age_days": int(older),
        "holder_announcement_date": row.get("holder_announcement_date", ""),
        "composition_announcement_date": row.get("composition_announcement_date", ""),
        "disclosure_attention": "Both <=120d; still verify" if fresh
                                else "One or both >120d; check announcements",
    }


def load_ranked(path: Path, as_of: str) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or not REQUIRED.issubset(reader.fieldnames):
            raise ValueError("Missing required A-pool columns")
        originals = list(reader)
    if not originals:
        raise ValueError("Empty A-pool")
    if {row["trade_date"] for row in originals} != {as_of}:
        raise ValueError("Data date differs from requested trading day")
    symbols = [row["vendor_symbol"] for row in originals]
    if len(set(symbols)) != len(symbols):
        raise ValueError("Duplicate symbols")
    ranked = [score_row(row) for row in originals]
    ranked.sort(key=lambda r: (
        r["priority_tier"] != "P1_DISCLOSURES_WITHIN_120D",
        -r["research_priority_score"], -r["state_margin"], r["vendor_symbol"],
    ))
    for i, row in enumerate(ranked, 1):
        row["research_rank"] = i
    return ranked


def save_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)


def write_report(rows: list[dict], as_of: str, top: int) -> str:
    p1 = sum(r["priority_tier"].startswith("P1") for r in rows)
    result = [
        "# A级状态池研究优先级（" + as_of + "）",
        "",
        "仅作研究观察；不是买卖信号，保持 PAPER_ONLY_NO_ENTRY_AUTHORIZATION。",
        "",
        f"原始A级 {len(rows)} 只；双重披露均≤120天的P1 {p1} 只；披露需复核的P2 {len(rows)-p1} 只。",
        "",
        "## 前20只研究观察名单",
        "",
        "| 排名 | 股票 | 代码 | 状态 | 层级 | 辅助分 | 原可信度 | 5日稳定 | 20日均成交额(亿元) | 最久披露(天) |",
        "|---:|---|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for r in rows[:top]:
        result.append(
            f"| {r['research_rank']} | {r['name_zh']} | {r['vendor_symbol']} | "
            f"{r['state_cn']} | {r['priority_tier'].split('_')[0]} | "
            f"{r['research_priority_score']:.2f} | {r['confidence']:.2f} | "
            f"{r['stable_days_5']}/5 | {r['avg_turnover_20_100m_cny']:.2f} | "
            f"{r['max_disclosure_age_days']} |"
        )
    result += [
        "",
        "## 排序说明",
        "",
        "先按两种股东披露均不超过120天分P1/P2层级，层内按辅助分降序。",
        "120天是额外研究注意阈值，不改变原A级入池规则，也不意味着P2必然有质量问题。",
        "辅助分最高100：原confidence 30分，5日稳定20分，最久披露时效20分，"
        "状态领先margin 20分，20日均成交额按对数刻度10分（20亿元封顶）。",
        "原confidence≥90计满分，5日稳定≥5计满分，margin≥30计满分，披露0天计满分。",
        "同分再比margin和证券代码。辅助分不代表未来涨幅或盈利概率。",
        "",
        "## 使用限制",
        "",
        "- A级仅代表通过原状态识别阈值，不是买入信号或实盘许可。",
        "- 股东户数和十大流通股东披露有自然周期；请逐一核实真实公告日期。",
        "- 不包含本交易日之后收益、滑点、真实成交或任何前向验证。",
        "- 没有逐日资金流拆单数据，不能由这些指标推断实际账户控盘。",
        "- 原始池数据和策略冻结规则未做任何修改。",
        "",
        "源：Google Drive stock_state_strategy/output/state_definition_"
        + as_of.replace("-", "") + "/a_strategy_pool.csv",
        "完整排名：full_rank.csv；前20：watchlist_top20.csv；统计：ranking_summary.json。",
        "",
    ]
    return "\n".join(result)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", required=True, type=Path)
    p.add_argument("--output-dir", required=True, type=Path)
    p.add_argument("--as-of", required=True)
    p.add_argument("--top", type=int, default=20)
    args = p.parse_args()
    if args.top < 1:
        raise ValueError("Top count must be positive")
    rows = load_ranked(args.input, args.as_of)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    save_csv(args.output_dir / "full_rank.csv", rows)
    save_csv(args.output_dir / "watchlist_top20.csv", rows[:args.top])
    (args.output_dir / "research_watchlist.md").write_text(
        write_report(rows, args.as_of, args.top), encoding="utf-8"
    )
    p1 = sum(row["priority_tier"].startswith("P1") for row in rows)
    summary = {
        "as_of_date": args.as_of, "source_A_count": len(rows),
        "p1_disclosures_within_120d": p1, "p2_disclosure_recheck": len(rows) - p1,
        "top_count": min(args.top, len(rows)), "original_candidates_unchanged": True,
        "authorization": "WATCHLIST_ONLY_NO_TRADE",
    }
    (args.output_dir / "ranking_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    for r in rows[:args.top]:
        print(f"{r['research_rank']:2} {r['vendor_symbol']} {r['name_zh']} "
              f"{r['priority_tier']} score={r['research_priority_score']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
