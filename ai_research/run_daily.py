"""AI_RESEARCH_SHADOW_V1: evidence-bound, paper-only A-share research.

Default execution is deterministic and makes zero network/API calls.
No model API access is supported. Use offline_agents.py for full data-based analysis.
No code in this package places orders or modifies stock_state_strategy V1.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import math
import re
from datetime import date, datetime, timezone
from pathlib import Path

VERSION = "AI_RESEARCH_SHADOW_V1"
AUTHORIZATION = "PAPER_ONLY_NO_ENTRY_AUTHORIZATION"
REQUIRED = {
    "research_rank", "priority_tier", "research_priority_score",
    "vendor_symbol", "name_zh", "state_cn", "confidence", "stable_days_5",
    "state_margin", "avg_turnover_20_100m_cny", "max_disclosure_age_days",
}
SYMBOL = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")


def verified_rows(csv_path: Path, summary_path: Path, as_of: str, top: int) -> list[dict]:
    date.fromisoformat(as_of)
    if not 1 <= top <= 5:
        raise ValueError("V0.1 top must be between 1 and 5")
    summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
    if summary.get("as_of_date") != as_of:
        raise ValueError("Snapshot summary date mismatch")
    if summary.get("original_candidates_unchanged") is not True:
        raise ValueError("Cannot verify original A-pool provenance")
    if summary.get("authorization") != "WATCHLIST_ONLY_NO_TRADE":
        raise ValueError("Snapshot is not authorized as a paper-only watchlist")
    with csv_path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not REQUIRED.issubset(reader.fieldnames):
            raise ValueError("Missing ranked A-pool columns")
        rows = list(reader)
    if not rows:
        raise ValueError("Ranked A-pool is empty")
    if int(summary.get("top_count", -1)) != len(rows):
        raise ValueError("Ranked watchlist length differs from summary")
    if len({r.get("vendor_symbol") for r in rows}) != len(rows):
        raise ValueError("Duplicate stock codes in watchlist")
    last_rank = 0
    for row in rows:
        rank = int(row["research_rank"])
        if rank != last_rank + 1:
            raise ValueError("Ranks are not sequential")
        last_rank = rank
        if not SYMBOL.fullmatch(row["vendor_symbol"]):
            raise ValueError("Invalid stock code")
        if not row["priority_tier"].startswith(("P1_", "P2_")):
            raise ValueError("Unrecognized research tier")
        for column in ("research_priority_score", "confidence", "stable_days_5",
                       "state_margin", "avg_turnover_20_100m_cny",
                       "max_disclosure_age_days"):
            value = float(row[column])
            if not math.isfinite(value) or (column in (
                    "stable_days_5", "avg_turnover_20_100m_cny",
                    "max_disclosure_age_days") and value < 0):
                raise ValueError("Invalid numeric metric: " + column)
    return rows[:top]


def load_evidence(path: Path | None, as_of: str) -> dict:
    """Only allow observations with an explicit source and published date <= as_of.

    The filter is conservative by calendar day (the reports are EOD research).
    Evidence is supplied externally; this package does not scrape live news.
    """
    if path is None:
        return {}
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        raise ValueError("Evidence must be keyed by stock symbol")
    allowed = {}
    for symbol, collection in raw.items():
        if not SYMBOL.fullmatch(symbol) or not isinstance(collection, dict):
            raise ValueError("Invalid evidence mapping")
        allowed[symbol] = {}
        for kind in ("fundamental", "news"):
            records = collection.get(kind, [])
            if not isinstance(records, list):
                raise ValueError("Evidence category must be a list")
            permitted = []
            for entry in records:
                if not isinstance(entry, dict):
                    raise ValueError("Evidence record must be an object")
                published = str(entry.get("published_at", ""))
                source = str(entry.get("source", "")).strip()
                content = str(entry.get("text", "")).strip()
                if not source or not content or not published:
                    raise ValueError("Evidence requires source, text, published_at")
                day = date.fromisoformat(published[:10])
                if day <= date.fromisoformat(as_of):
                    permitted.append({"published_at": published,
                                      "source": source[:160],
                                      "text": content[:500]})
            allowed[symbol][kind] = permitted[:5]
    return allowed


def research_row(row: dict, evidence: dict) -> dict:
    symbol = row["vendor_symbol"]
    disclosed_age = int(float(row["max_disclosure_age_days"]))
    older = disclosed_age > 120
    fundamental = evidence.get(symbol, {}).get("fundamental", [])
    news = evidence.get(symbol, {}).get("news", [])
    return {
        "symbol": symbol, "name": row["name_zh"],
        "research_rank": int(row["research_rank"]),
        "original_priority_score": float(row["research_priority_score"]),
        "state": row["state_cn"],
        "priority_tier": row["priority_tier"],
        "technical_agent": {
            "status": "STATE_ONLY_NO_OHLC",
            "facts": [
                "原状态分类：" + row["state_cn"],
                "原状态可信度：" + row["confidence"],
                "最近5日稳定天数：" + row["stable_days_5"],
                "状态分差：" + row["state_margin"],
            ],
            "limitation": "未提供K线、成交量序列；不能验证突破、量价或均线。",
        },
        "fundamental_agent": {
            "status": "EVIDENCE_ONLY" if fundamental else "NO_POINT_IN_TIME_DATA",
            "evidence": fundamental,
            "limitation": "未导入可核对财务披露；不对盈利质量或估值下结论。" if not fundamental
                          else "证据仅供核对，尚未执行财务比率交叉验证。",
        },
        "news_agent": {
            "status": "EVIDENCE_ONLY" if news else "NO_POINT_IN_TIME_DATA",
            "evidence": news,
            "limitation": "未导入公告/新闻历史快照；不能声称无利空。" if not news
                          else "仅显示截至研究日已披露的输入证据。",
        },
        "risk_agent": {
            "status": "DISCLOSURE_RECHECK" if older else "DISCLOSURE_FRESHNESS_CHECKED",
            "facts": [
                "最长股东相关披露距今：" + str(disclosed_age) + "天",
                "20日均成交额：" + row["avg_turnover_20_100m_cny"] + "亿元",
            ],
            "flags": (["披露超过120天，需复核"] if older else []) + [
                "未验证停牌、涨跌停可买性与当日流动性；不授权交易"
            ],
        },
        "bull_case": "原模型标记为" + row["state_cn"] + "，可作为后续研究方向；不是预测。",
        "bear_case": "可能存在状态误判、披露时滞及未覆盖的财务/公告风险。",
        "research_decision": "披露复核优先" if older else "待补齐财务与公告证据",
        "llm_commentary": None,
        "llm_status": "DISABLED",
        "authorization": AUTHORIZATION,
    }


def render_markdown(report: dict) -> str:
    lines = [
        "# A股 AI 辅助投研（影子版）",
        "",
        "研究日期：" + report["as_of"] + "；模式：" + report["mode"],
        "",
        "**仅限纸面研究，不提供买卖许可，也不改变冻结 V1。**",
        "",
    ]
    for item in report["stocks"]:
        lines += [
            "## " + str(item["research_rank"]) + ". " + item["name"] + " (" + item["symbol"] + ")",
            "",
            "原状态：" + item["state"] + "；原辅助排序分：" + str(item["original_priority_score"]),
            "",
            "**技术 Agent**：" + "；".join(item["technical_agent"]["facts"]),
            "",
            "**基本面 Agent**：" + item["fundamental_agent"]["status"],
            "",
            "**新闻 Agent**：" + item["news_agent"]["status"],
            "",
            "**风险 Agent**：" + "；".join(item["risk_agent"]["facts"] + item["risk_agent"]["flags"]),
            "",
            "**多方观点**：" + item["bull_case"],
            "",
            "**反方观点**：" + item["bear_case"],
            "",
            "**研究结论**：" + item["research_decision"],
            "",
        ]
        for kind in ("fundamental_agent", "news_agent"):
            for fact in item[kind]["evidence"]:
                lines.append("- 来源：" + fact["source"] + "；披露：" +
                             fact["published_at"] + "；内容：" + fact["text"])
    lines += [
        "", "## 数据限制",
        "- 仅依据原 A 级研究池；未在本模块核对实时成交、未来收益或真实可买性。",
        "- 未提供来源、发布时间的财务/新闻不得用于研究结论。",
        "- 排序分并非盈利概率；所有 AI 观点均为独立影子观察。",
        "- " + AUTHORIZATION,
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watchlist", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--evidence-json", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    rows = verified_rows(args.watchlist, args.summary, args.as_of, args.top)
    evidence = load_evidence(args.evidence_json, args.as_of)
    records = [research_row(row, evidence) for row in rows]
    report = {
        "version": VERSION,
        "as_of": args.as_of,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "RULE_ONLY_NO_API",
        "source": "verified A-pool research_watchlist snapshot",
        "authorization": AUTHORIZATION,
        "stocks": records,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown = render_markdown(report)
    (args.output_dir / "report.md").write_text(markdown, encoding="utf-8")
    page = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<title>A股影子投研</title><style>'
            'body{max-width:900px;margin:3em auto;padding:0 1em;font:16px/1.7 sans-serif}'
            'pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f6f7f9;padding:20px}'
            '</style></head><body><pre>' + html.escape(markdown) + '</pre></body></html>')
    (args.output_dir / "report.html").write_text(page, encoding="utf-8")
    print(json.dumps({"status": "SUCCESS", "mode": report["mode"],
                      "as_of": args.as_of, "stocks": len(records),
                      "authorization": AUTHORIZATION}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
