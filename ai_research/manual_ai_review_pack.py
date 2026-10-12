"""Manual, no-API five-stock AI research evidence packaging.

Only constructs a grounded review packet. An LLM is NOT invoked by this module,
and no report is allowed to modify R1 signals or authorize trades.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from datetime import date, datetime, timezone
from pathlib import Path

SYMBOLS = ("600351.SH", "002107.SZ", "600641.SH", "002568.SZ", "002296.SZ")
PERIODS = ("20260331", "20260630")
DATASETS = ("income", "balancesheet", "cashflow", "fina_indicator")
AUTHORIZATION = "PAPER_ONLY_NO_ENTRY_AUTHORIZATION"
FIELDS = {
    "income": ("total_revenue", "n_income_attr_p", "operate_profit", "total_profit",
               "total_cogs", "oper_cost", "rd_exp", "admin_exp"),
    "balancesheet": ("total_assets", "total_liab", "total_cur_assets", "total_cur_liab",
                     "total_hldr_eqy_exc_min_int", "money_cap", "accounts_receiv"),
    "cashflow": ("n_cashflow_act", "n_cashflow_inv", "n_cash_flows_fnc"),
    "fina_indicator": ("roe", "roa", "debt_to_assets", "current_ratio",
                       "netprofit_yoy", "or_yoy", "gross_margin", "profit_dedt",
                       "eps", "dt_eps"),
}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def utc_time(value):
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Timestamp lacks timezone")
    return stamp.astimezone(timezone.utc)


def date_value(value):
    if value is None or str(value) in ("NaT", "<NA>"):
        return None
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def number(value):
    import pandas as pd
    if value is None or pd.isna(value):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def verified_finance(root, latest, review_clock):
    """Accept only an immutable 40/40 five-stock version, with actual byte hashes."""
    import pandas as pd
    root = Path(root)
    latest = read_json(latest)
    marker = read_json(root / "targeted_current.json")
    acceptance = read_json(root / "targeted_acceptance.json")
    audit = read_json(root / "audit.json")
    if (latest.get("status") != "COMPLETE" or latest.get("groups") != 40 or
            latest.get("run_id") != marker.get("version", "").removeprefix("five-clean-") or
            str(latest.get("version_path", "")).split("/")[-1] != latest.get("run_id") or
            not marker.get("validated") or not marker.get("target_scope_complete") or
            marker.get("authorization") != AUTHORIZATION or
            sorted(marker.get("target_symbols", [])) != sorted(SYMBOLS) or
            sorted(marker.get("report_periods", [])) != sorted(PERIODS) or
            sorted(marker.get("datasets", [])) != sorted(DATASETS) or
            acceptance.get("passed") is not True or acceptance.get("groups") != 40 or
            audit.get("status") != "COMPLETE" or audit.get("valid") != 40 or
            audit.get("issues") or len(audit.get("records", [])) != 40):
        raise ValueError("Finance snapshot not accepted for exact 40-group target scope")
    publication = utc_time(marker.get("published_at"))
    if publication > review_clock:
        raise ValueError("Finance captured or published after review time")
    filenames = {f"{ds}_{period}.parquet" for ds in DATASETS for period in PERIODS}
    records = acceptance.get("partitions", {})
    if set(records) != filenames or set(audit.get("files", {})) != filenames:
        raise ValueError("Missing or extra finance partitions")
    for filename in filenames:
        if (records[filename]["sha256"] != audit["files"][filename]["sha256"] or
                records[filename]["rows"] != 5 or audit["files"][filename]["rows"] != 5):
            raise ValueError("Audit and acceptance partition ledgers disagree: " + filename)
    output = {s: {} for s in SYMBOLS}
    for dataset in DATASETS:
        for period in PERIODS:
            filename = f"{dataset}_{period}.parquet"
            path = root / filename
            expected = records[filename]
            if (not path.is_file() or path.stat().st_size != expected["bytes"] or
                hashlib.sha256(path.read_bytes()).hexdigest() != expected["sha256"]):
                raise ValueError("Finance partition checksum mismatch: " + filename)
            frame = pd.read_parquet(path)
            if len(frame) != 5 or set(frame["ts_code"]) != set(SYMBOLS) or frame["ts_code"].duplicated().any():
                raise ValueError("Finance partition must cover exactly five distinct symbols")
            if frame["schema_version"].dropna().astype(str).nunique() != 1:
                raise ValueError("Mixed Clean schema in partition")
            for _, row in frame.iterrows():
                symbol = row["ts_code"]
                if date_value(row["report_period"]) != date.fromisoformat(
                        f"{period[:4]}-{period[4:6]}-{period[6:]}").isoformat():
                    raise ValueError("Finance period mismatch")
                ann = date_value(row["ann_date"])
                f_ann = date_value(row.get("actual_ann_date"))
                available = date_value(row.get("available_date"))
                if not ann:
                    raise ValueError("Undated issuer financial record")
                if any(d > review_clock.date().isoformat() for d in (ann, f_ann, available) if d):
                    raise ValueError("Future financial disclosure")
                fields = {k: number(row[k]) if k in row.index else None for k in FIELDS[dataset]}
                if not any(v is not None for v in fields.values()):
                    raise ValueError("No usable numeric indicators")
                output[symbol].setdefault(period, {})[dataset] = {
                    "id": f"F:{symbol}:{dataset}:{period}",
                    "source": "TUSHARE_PRO_ACCEPTED_TARGET_CLEAN",
                    "version": marker["version"], "schema": str(row["schema_version"]),
                    "report_period": date_value(row["report_period"]),
                    "announcement_date": ann, "actual_announcement_date": f_ann,
                    "available_date": available, "fields": fields,
                    "partition": filename, "partition_sha256": expected["sha256"],
                }
    return output, marker


def package(report, clean_root, latest_path, review_clock):
    """Manual review after capture, not historical backtest or new R1 entry."""
    if review_clock.tzinfo is None:
        raise ValueError("Review timestamp must include timezone")
    review_clock = review_clock.astimezone(timezone.utc)
    source = read_json(report)
    if source.get("authorization") != AUTHORIZATION or source.get("mode") != "DETERMINISTIC_MULTI_ROLE_NO_LLM_NO_NETWORK":
        raise ValueError("Unverified or unexpected offline source report")
    market_day = date.fromisoformat(source["as_of"])
    if market_day > review_clock.date():
        raise ValueError("Market date after review")
    stocks = source.get("stocks", [])
    if len(stocks) != 5 or {s["symbol"] for s in stocks} != set(SYMBOLS):
        raise ValueError("Offline report must contain exactly the fixed five stocks")
    clean, pointer = verified_finance(clean_root, latest_path, review_clock)
    capture_day = utc_time(pointer["published_at"]).date()
    result = {
        "mode": "MANUAL_CHATGPT_REVIEW_NO_API_NO_AUTOMATED_LLM",
        "authorization": AUTHORIZATION,
        "market_as_of": market_day.isoformat(),
        "review_generated_utc": review_clock.isoformat(),
        "finance_capture_utc": pointer["published_at"],
        "finance_clean_version": pointer["version"],
        "finance_scope": "5_SYMBOLS_2026Q1_H1_FOUR_DATASETS_40_OF_40",
        "historical_backtest_eligible": False,
        "research_type": "CURRENT_DISCRETIONARY_REVIEW_NOT_HISTORICAL_SIGNAL",
        "limitations": [
            "Market technical facts refer to the offline report's market_as_of only, not live prices.",
            "New financial version was captured after market_as_of; never backfill it into prior-day R1 or backtests.",
            "A fully accepted targeted Clean subset is not a complete A-share historical financial database.",
            "No independently verified issuer announcements/news means event analysis is evidence insufficient.",
            "No trade, position size, stop modification, timing, or price target authorization.",
            "Old offline report fundamental agent uses an older partial Clean source and is expressly excluded.",
        ],
        "stocks": []
    }
    if capture_day > market_day:
        result["limitations"].append("Finance was not available to the earlier offline report execution.")
    for raw in stocks:
        symbol = raw["symbol"]
        technical = raw.get("technical_agent", {})
        if technical.get("status") != "CALCULATED_GOLD_PIT":
            tech_facts = {}
        else:
            tech_facts = technical.get("facts", {})
        news = raw.get("news_agent", {}).get("evidence", [])
        curated = []
        for item in news:
            try:
                published = date.fromisoformat(item["published_at"])
                captured = date.fromisoformat(item["captured_at"])
                url = str(item["source_url"])
                if published <= review_clock.date() and captured <= review_clock.date() and url.startswith("https://"):
                    curated.append({**item, "evidence_id": f"N:{symbol}:{len(curated)+1}"})
            except (ValueError, KeyError, TypeError):
                continue
        current = clean[symbol]["20260630"]
        inc = current["income"]["fields"]
        cash = current["cashflow"]["fields"]
        indicators = current["fina_indicator"]["fields"]
        flags = []
        if cash["n_cashflow_act"] is not None and cash["n_cashflow_act"] < 0:
            flags.append("2026H1 经营活动现金流为负（核验Clean）")
        if inc["n_income_attr_p"] is not None and inc["n_income_attr_p"] < 0:
            flags.append("2026H1 归母净利润为负（核验Clean）")
        if (indicators["profit_dedt"] is not None and
                inc["n_income_attr_p"] is not None and
                indicators["profit_dedt"] < 0 < inc["n_income_attr_p"]):
            flags.append("2026H1 归母盈利但扣非亏损（核验Clean）")
        if not curated:
            flags.append("没有已核验公告/新闻事件，不能推断不存在风险")
        result["stocks"].append({
            "symbol": symbol, "name": raw["name"], "research_rank_historical": raw.get("research_rank"),
            "historical_pool_state": raw.get("original_state"),
            "technical": {"id": f"T:{symbol}:{market_day.isoformat()}",
                          "source": "OLDER_OFFLINE_REPORT_GOLD", "market_as_of": market_day.isoformat(),
                          "status": technical.get("status"), "facts": tech_facts,
                          "flags": technical.get("flags", [])},
            "finance": clean[symbol], "curated_announcements": curated,
            "deterministic_risk_checks": flags,
            "ai_bull_argument": None, "ai_bear_argument": None,
            "ai_risk_review": None, "ai_final_research_conclusion": None,
            "r1_signal": "NOT_GENERATED_OR_CHANGED_BY_THIS_REVIEW",
        })
    return result


def instruction(packet):
    return f"""# 五股低成本 AI 人工复核提示词

你是研究助手。请仅基于同目录的 manual_ai_packet.json 中可追溯数据分析五只固定 A 股：
600351.SH、002107.SZ、600641.SH、002568.SZ、002296.SZ。

**严格时间限制：** 技术行情截至 {packet['market_as_of']}；新财务 Clean 采集/发布于
{packet['finance_capture_utc']}。这是采集后的**当前人工研究**，不是
{packet['market_as_of']} 的历史可交易信息，绝对不得把新财务倒灌到原 R1 回测。

你必须遵守：
1. 只引述 packet 中具体的 F:xxx 财务证据 ID、T:xxx 技术证据；每条事实注明财报期、公告日、数据源与采集时间。
2. 用四个角色按顺序分析每只：公告/事件研究员、看多分析师、看空分析师、风险经理。
3. 每只股票看多与看空至少各尝试两项**基于实际数据**的论据；没有证据就写「证据不足」，不能填充虚构消息。
4. 看空分析师要逐点反驳看多的关键推论；风险经理再对照双方，区分「已验证」「推论」「需补资料」。
5. 财务同比、季节性变化要注意 Q1 与 H1 是不同累计期间，不能简单视为两个独立季度。
6. 新闻公告只允许引用 packet 中已有已采集证据；如要联网新增，必须另外记录 source_url、published_at、captured_at，并把它标记为**本次研究新采集**，不得用于旧时点。
7. 不使用旧离线报告中过时的 fundamental_agent；新财务只用 finance_clean_version={packet['finance_clean_version']}。
8. 每只输出：事实与来源、公告研读（可缺）、看多论证、看空反驳、风险级别与理由、至少两项可证伪检查、未决问题、研究结论。
9. 五股末尾给出证据质量对比，允许「暂缓判断」，不构造 R1 信号，不建议实盘买卖、仓位、具体交易价、目标价或参数调优。
10. 所有结论必须是研究观点，模型不能自行更改 GitHub、Google Drive 或交易策略。

**输出要求：** 中文；事实/推断分离；明确列出 packet 的财务实际采集时间。
授权边界：{AUTHORIZATION}。
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--clean-root", type=Path, required=True)
    parser.add_argument("--latest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    pack = package(args.report_json, args.clean_root, args.latest, now)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    contents = json.dumps(pack, ensure_ascii=False, indent=2) + "\n"
    (args.output_dir / "manual_ai_packet.json").write_text(contents, encoding="utf-8")
    (args.output_dir / "chatgpt_prompt.md").write_text(instruction(pack), encoding="utf-8")
    manifest = {"mode":pack["mode"],"stock_count":len(pack["stocks"]),
                "finance_version":pack["finance_clean_version"],
                "market_as_of":pack["market_as_of"],
                "created_utc":pack["review_generated_utc"],
                "packet_sha256":hashlib.sha256(contents.encode()).hexdigest(),
                "no_model_api":True,"authorization":AUTHORIZATION}
    (args.output_dir / "manual_review_manifest.json").write_text(
        json.dumps(manifest,ensure_ascii=False,indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"status":"PACKET_READY_NO_LLM_CALLED",
                      "count":len(pack["stocks"]),
                      "clean":pack["finance_clean_version"],
                      "historical_backtest_eligible":False},ensure_ascii=False))


if __name__ == "__main__":
    main()
