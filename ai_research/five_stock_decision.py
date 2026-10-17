"""PIT-gated fixed-five A-share paper research; never alters frozen R1."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

from ai_research.manual_ai_review_pack import SYMBOLS, AUTHORIZATION, verified_finance, utc_time

POLICY = "R1_PULLBACK_BREADTH50_WATCH5_PAPER"
VERSION = "FIVE_STOCK_DECISION_V1"
FIELDS = ("income", "balancesheet", "cashflow", "fina_indicator")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256(obj):
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()


def num(record, key):
    value = record.get("fields", {}).get(key)
    return float(value) if isinstance(value, (float, int)) and not isinstance(value, bool) else None


def div(a, b):
    return round(a / b, 4) if a is not None and b not in (None, 0) else None


def financial_review(periods, as_of):
    half = periods.get("20260630", {})
    q1 = periods.get("20260331", {})
    problems = []
    for period, statements in periods.items():
        for dataset, record in statements.items():
            dates = (record.get("announcement_date"), record.get("actual_announcement_date"),
                     record.get("available_date"))
            if any(d and str(d)[:10] > as_of for d in dates):
                raise ValueError("Future dated finance: " + period + " " + dataset)
    for name in FIELDS:
        if name not in half:
            problems.append("MISSING_" + name.upper())
    inc = half.get("income", {})
    bal = half.get("balancesheet", {})
    cash = half.get("cashflow", {})
    indi = half.get("fina_indicator", {})
    profit = num(inc, "n_income_attr_p")
    revenue = num(inc, "total_revenue")
    ocf = num(cash, "n_cashflow_act")
    assets = num(bal, "total_assets")
    liabs = num(bal, "total_liab")
    ar = num(bal, "accounts_receiv")
    ded = num(indi, "profit_dedt")
    if ocf is not None and ocf < 0:
        problems.append("OPERATING_CASHFLOW_NEGATIVE")
    if profit is not None and profit < 0:
        problems.append("ATTRIBUTABLE_PROFIT_NEGATIVE")
    if profit is not None and profit > 0 and ded is not None and ded < 0:
        problems.append("POSITIVE_PROFIT_NEGATIVE_RECURRING")
    if ar is not None and assets and ar / assets > 0.25:
        problems.append("RECEIVABLES_OVER_25PCT_ASSETS")
    if (revenue is not None and num(indi, "or_yoy") is not None
            and num(indi, "or_yoy") < 0 and num(indi, "netprofit_yoy") is not None
            and num(indi, "netprofit_yoy") > 0):
        problems.append("PROFIT_GROWTH_REVENUE_DECLINE")
    if num(cash, "n_cashflow_inv") is None or num(cash, "n_cash_flows_fnc") is None:
        problems.append("CASHFLOW_INVESTING_FINANCING_NOT_CAPTURED")
    q1_inc = q1.get("income", {})
    h1_profit = num(inc, "n_income_attr_p")
    q1_profit = num(q1_inc, "n_income_attr_p")
    return {
        "source_version": inc.get("version"), "report_period": "2026-06-30",
        "announced_at": inc.get("announcement_date"),
        "h1_revenue_cny": revenue,
        "h1_attributable_profit_cny": profit,
        "h1_operating_cashflow_cny": ocf,
        "h1_recurring_profit_cny": ded,
        "h1_assets_cny": assets,
        "h1_liabilities_cny": liabs,
        "h1_receivables_cny": ar,
        "debt_to_assets_ratio": div(liabs, assets),
        "cash_to_profit_ratio": div(ocf, profit) if profit and profit > 0 else None,
        "receivables_to_assets_ratio": div(ar, assets),
        "current_ratio_reported": num(indi, "current_ratio"),
        "revenue_yoy_pct_reported": num(indi, "or_yoy"),
        "profit_yoy_pct_reported": num(indi, "netprofit_yoy"),
        "q2_incremental_profit_cny": round(h1_profit - q1_profit, 2)
            if h1_profit is not None and q1_profit is not None else None,
        "investing_cashflow_cny": num(cash, "n_cashflow_inv"),
        "financing_cashflow_cny": num(cash, "n_cash_flows_fnc"),
        "data_gaps_and_risks": problems,
        "record_ids": {k: half.get(k, {}).get("id") for k in FIELDS},
    }


def accepted_events(evidence, symbol, cutoff):
    if not evidence:
        return []
    if not isinstance(evidence, dict) or not isinstance(evidence.get("events", []), list):
        raise ValueError("Invalid announcement evidence")
    events = []
    for e in evidence.get("events", []):
        if e.get("symbol") != symbol:
            continue
        if not all(e.get(k) for k in ("source_url", "published_at", "captured_at", "title")):
            continue
        if not str(e["source_url"]).startswith("https://"):
            continue
        try:
            pub, cap = utc_time(e["published_at"]), utc_time(e["captured_at"])
        except (TypeError, ValueError):
            continue
        if pub <= cutoff and cap <= cutoff and pub <= cap:
            events.append({k: e[k] for k in ("title", "source_url", "published_at", "captured_at", "symbol")})
    return events


def debate(r1_entry, finance, announcements):
    """Two auditable rounds of *rules*, explicitly NOT LLM debate."""
    risks = finance.get("data_gaps_and_risks", [])
    bull = []
    bear = []
    if finance.get("h1_attributable_profit_cny") is not None and finance["h1_attributable_profit_cny"] > 0:
        bull.append("H1归母净利润为正；证据:" + str(finance["record_ids"].get("income")))
    if finance.get("h1_operating_cashflow_cny") is not None and finance["h1_operating_cashflow_cny"] > 0:
        bull.append("H1经营现金流为正；证据:" + str(finance["record_ids"].get("cashflow")))
    if r1_entry.get("candidate") is True:
        bull.append("R1在该信号日列入候选；证据:R1快照")
    bear.append("市场或个股R1交易条件不能由历史财务指标替代")
    for code in risks:
        if code in ("OPERATING_CASHFLOW_NEGATIVE", "POSITIVE_PROFIT_NEGATIVE_RECURRING",
                    "RECEIVABLES_OVER_25PCT_ASSETS", "PROFIT_GROWTH_REVENUE_DECLINE"):
            bear.append(code + "；证据:2026H1四表")
    if not announcements:
        bear.append("缺少符合研究时点的可核验公告集合，不代表没有风险")
    return {
        "mode": "DETERMINISTIC_TWO_ROUND_RULES_NOT_LLM",
        "round1_bull": bull or ["正向证据不足"],
        "round1_bear": bear,
        "round2_bear_rebuttal": "盈利与现金流为正也不能推出次日可成交或符合冻结R1入场条件",
        "round2_bull_response": "只保留已核验证据；没有有效R1候选或次日开盘验真则不得认定可入场",
        "risk_manager_review": risks + ([] if announcements else ["NEWS_COVERAGE_MISSING"]),
        "needs_manual_verification": True,
    }


def build(r1, finance, finance_marker, news=None, previous=None, now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Review time must be timezone aware")
    now = now.astimezone(timezone.utc)
    as_of = r1.get("snapshot_date")
    date.fromisoformat(str(as_of))
    if date.fromisoformat(as_of) > now.date():
        raise ValueError("Future R1 snapshot")
    if (r1.get("status") != "PAPER_ONLY_R1_FIVE_NO_ENTRY_AUTHORIZATION"
            or r1.get("gold_as_of") != as_of
            or r1.get("policy", {}).get("id") != POLICY
            or r1.get("scope") != list(SYMBOLS)
            or not isinstance(r1.get("manifest_sha256"), str)
            or len(r1.get("manifest_sha256", "")) != 64
            or len(r1.get("policy_sha256", "")) != 64):
        raise ValueError("Unverified R1 contract")
    entries = r1.get("checked", [])
    if (len(entries) != 5 or {e.get("symbol") for e in entries} != set(SYMBOLS)
            or any(e.get("signal_date") != as_of for e in entries)
            or sum(e.get("candidate") is True for e in entries) != r1.get("candidate_count")):
        raise ValueError("Invalid fixed-five R1 scope/entry count")
    breadth = r1.get("market_breadth", {})
    eligible, above, fraction = (breadth.get("eligible_symbols"),
                                 breadth.get("above_ma20"), breadth.get("fraction"))
    if (not isinstance(eligible, int) or eligible < 500 or not isinstance(above, int)
            or not 0 <= above <= eligible or not isinstance(fraction, (float, int))
            or abs(above / eligible - fraction) > 0.00001
            or breadth.get("passed") is not (fraction >= r1["policy"]["ma20_above_minimum"])):
        raise ValueError("Inconsistent market breadth")
    finance_available = bool(finance) and finance_marker is not None
    if finance_available and utc_time(finance_marker["published_at"]) > now:
        raise ValueError("Future captured Clean snapshot")
    records = []
    prev_lookup = {}
    if previous and previous.get("as_of") < as_of:
        prev_lookup = {s["symbol"]: s for s in previous.get("stocks", [])}
    for item in entries:
        sym = item["symbol"]
        if item.get("status") == "BREADTH_NOT_PASSED" and item.get("candidate") is True:
            raise ValueError("Contradictory R1 candidate")
        if item.get("candidate") and breadth.get("passed") is not True:
            raise ValueError("R1 candidate despite breadth gate")
        has_finance = finance_available and sym in finance
        fin = financial_review(finance[sym], as_of) if has_finance else {
            "data_gaps_and_risks": ["FINANCE_COVERAGE_MISSING"],
            "record_ids": {}, "source_version": None}
        events = accepted_events(news, sym, now)
        hard_risks = set(fin["data_gaps_and_risks"]) & {
            "OPERATING_CASHFLOW_NEGATIVE", "POSITIVE_PROFIT_NEGATIVE_RECURRING",
            "ATTRIBUTABLE_PROFIT_NEGATIVE"}
        if item.get("candidate") is not True:
            label = "WATCH" if item.get("status") == "BREADTH_NOT_PASSED" else "NEEDS_REVIEW"
        elif hard_risks:
            label = "AVOID_EXIT_REVIEW"
        elif not has_finance or not events:
            label = "NEEDS_REVIEW"
        else:
            label = "SETUP_PAPER_NEXT_OPEN_UNVERIFIED"
        waiting = (["市场广度达到冻结R1条件，且重新出现真实个股候选"]
                   if not item.get("candidate") else
                   ["下一交易日开盘价/涨跌停/停复牌和成交条件待验证"])
        if not events:
            waiting.append("补充当时已采集的公告证据")
        if not has_finance:
            waiting.append("补齐截至研究时点可用的已验收财务数据")
        debate_result = debate(item, fin, events)
        prior = prev_lookup.get(sym)
        records.append({
            "symbol": sym, "name": item.get("display_label_unverified"),
            "as_of": as_of, "close_raw_cny": item.get("signal_close_raw"),
            "r1_status": item.get("status"), "r1_pattern": item.get("pattern"),
            "r1_candidate": item.get("candidate") is True,
            "research_status": label, "next_open_executability": "UNKNOWN",
            "paper_entry_authorized": False, "paper_entry_price_cny": None,
            "paper_stop_cny": None, "paper_weight": None,
            "waiting_for": waiting, "finance": fin,
            "announcements": events, "news_status":
                "VERIFIED_CAPTURED_EVIDENCE" if events else "NEWS_COVERAGE_MISSING",
            "two_round_rule_based_review": debate_result,
            "prior_research_status": prior.get("research_status") if prior else None,
            "status_changed": (prior.get("research_status") != label) if prior else None,
        })
    report = {
        "version": VERSION, "as_of": as_of, "created_utc": now.isoformat(),
        "mode": "DETERMINISTIC_FIXED_FIVE_NO_LLM_NO_TRADE",
        "authorization": AUTHORIZATION,
        "source": {"r1_policy_id": POLICY, "r1_policy_sha256": r1["policy_sha256"],
                   "gold_manifest_sha256": r1["manifest_sha256"],
                   "r1_snapshot_sha256": sha256(r1),
                   "finance_version": finance_marker.get("version") if finance_marker else None,
                   "finance_published_utc": finance_marker.get("published_at") if finance_marker else None},
        "breadth": breadth,
        "r1_candidate_count": r1["candidate_count"],
        "stocks": records,
        "limitations": [
            "冻结R1是原始候选权威；研究层不能制造、删除或修改候选信号",
            "任何纸面入场仍须下一交易日验证开盘与可成交性",
            "规则化两轮审查不是LLM辩论；新闻缺失不等于安全",
            "40/40是两个报告期的五股定向覆盖，不等于历史三表完整",
            "研究并非已验证A/B超额收益；不得自动交易",
        ],
    }
    return report


def markdown(report):
    b = report["breadth"]
    lines = ["# 固定五股入场评测（研究/模拟，非交易许可）",
             f"研究日：{report['as_of']}；生成 UTC：{report['created_utc']}",
             f"全市场广度：{b['above_ma20']}/{b['eligible_symbols']} = {b['fraction']:.2%}；门槛：50%；R1候选：{report['r1_candidate_count']}/5。",
             "", "| 股票 | 收盘价(元) | 原始R1 | 综合研究 | 主要等待条件 |",
             "|---|---:|---|---|---|"]
    for s in report["stocks"]:
        lines.append(f"| {s['name']} ({s['symbol']}) | {s['close_raw_cny']} | {s['r1_status']} | {s['research_status']} | {'；'.join(s['waiting_for'])} |")
    lines.extend(["", "## 逐股财务与风险摘要"])
    for s in report["stocks"]:
        f = s["finance"]
        lines.append(f"### {s['name']} {s['symbol']}")
        lines.append("2026H1归母净利润：" + str(f.get("h1_attributable_profit_cny")) +
                     " 元；经营现金流：" + str(f.get("h1_operating_cashflow_cny")) +
                     " 元；风险/缺口：" + ", ".join(f["data_gaps_and_risks"]))
        lines.append("同期公告：" + s["news_status"] + "；两轮反证：规则化（非LLM）。")
    lines.extend(["", "## 数据约束"] + ["- " + x for x in report["limitations"]])
    return "\n".join(lines) + "\n"


def save(report, destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (destination / "report.md").write_text(markdown(report), encoding="utf-8")
    ab = {"as_of": report["as_of"], "version": "FIVE_AB_FORWARD_V1",
          "status": "UNREALIZED_NEEDS_FUTURE_MARKET_BARS",
          "a_r1_candidates": [s["symbol"] for s in report["stocks"] if s["r1_candidate"]],
          "b_research_cleared": [s["symbol"] for s in report["stocks"]
                                 if s["r1_candidate"] and s["research_status"] == "SETUP_PAPER_NEXT_OPEN_UNVERIFIED"],
          "fees_slippage_policy": report["source"]["r1_policy_sha256"],
          "pnl": None, "max_drawdown": None, "trade_count": None,
          "note": "A/B uses same future fills; no PnL invented from current close-only signals"}
    (destination / "ab_forward.json").write_text(json.dumps(ab, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    coverage = {"as_of": report["as_of"], "symbols": len(report["stocks"]),
                "finance_covered": sum(bool(s["finance"]["source_version"]) for s in report["stocks"]),
                "announcement_covered": sum(bool(s["announcements"]) for s in report["stocks"]),
                "llm_execution": False, "real_order_execution": False}
    (destination / "coverage.json").write_text(json.dumps(coverage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    man = {"as_of": report["as_of"], "policy_sha256": report["source"]["r1_policy_sha256"],
           "sources": report["source"],
           "files_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in destination.iterdir() if p.is_file()},
           "authorization": AUTHORIZATION}
    (destination / "manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--r1-json", type=Path, required=True)
    p.add_argument("--finance-root", type=Path)
    p.add_argument("--finance-latest", type=Path)
    p.add_argument("--evidence", type=Path)
    p.add_argument("--previous", type=Path)
    p.add_argument("--output-dir", type=Path, required=True)
    args = p.parse_args()
    now = datetime.now(timezone.utc)
    if bool(args.finance_root) != bool(args.finance_latest):
        p.error("--finance-root and --finance-latest are both required or both absent")
    finance, marker = (verified_finance(args.finance_root, args.finance_latest, now)
                       if args.finance_root else ({}, None))
    r = build(read_json(args.r1_json), finance, marker,
              read_json(args.evidence) if args.evidence else None,
              read_json(args.previous) if args.previous else None, now)
    save(r, args.output_dir)
    print(canonical({"status": "FIVE_PAPER_RESEARCH_READY", "day": r["as_of"],
                     "stocks": len(r["stocks"]), "candidates": r["r1_candidate_count"],
                     "finance_complete": bool(marker), "entry_authorized": False}))


if __name__ == "__main__":
    main()
