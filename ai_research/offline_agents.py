"""Offline multi-role research over already-saved Drive snapshots.

No LLM, API clients, brokerage actions, or network access. Five independent
deterministic research roles plus bull/bear evidence and paper-only review.
"""
from __future__ import annotations
import argparse
import calendar
import csv
import html
import json
import math
from datetime import date, datetime, timezone
from pathlib import Path

VERSION = "OFFLINE_MULTIAGENT_SHADOW_V2"
AUTHORIZATION = "PAPER_ONLY_NO_ENTRY_AUTHORIZATION"
REQUIRED_MARKET = {"vendor_symbol", "trade_date", "open", "high", "low", "close",
                   "volume_shares", "turnover_cny", "is_quality_valid"}
REQUIRED_FINANCE = {"ts_code", "end_date", "ann_date", "roe", "debt_to_assets",
                    "current_ratio", "netprofit_margin"}


def date_from_any(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip().split(" ")[0][:10]
    if len(text) == 8 and text.isdigit():
        return datetime.strptime(text, "%Y%m%d").date()
    return date.fromisoformat(text)


def finite(value):
    try:
        out = float(value)
    except (ValueError, TypeError):
        return None
    return out if math.isfinite(out) else None


def round_value(value, places=3):
    return round(float(value), places) if value is not None and math.isfinite(float(value)) else None


def month_pairs(as_of: date, months=6):
    year, month = as_of.year, as_of.month
    for _ in range(months):
        yield year, month
        month -= 1
        if month == 0:
            year, month = year - 1, 12


def load_validated_watchlist(csv_path: Path, summary_path: Path, day: date, top: int):
    if top < 1 or top > 20:
        raise ValueError("top must be 1..20")
    summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
    if (summary.get("as_of_date") != day.isoformat() or
            summary.get("authorization") != "WATCHLIST_ONLY_NO_TRADE" or
            summary.get("original_candidates_unchanged") is not True):
        raise ValueError("Unverified date/provenance/authorization of A-pool")
    with csv_path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not {"research_rank", "vendor_symbol", "name_zh",
                "state_cn", "research_priority_score", "confidence",
                "max_disclosure_age_days"}.issubset(reader.fieldnames):
            raise ValueError("Incorrect A-pool watchlist schema")
        all_rows = list(reader)
    if not all_rows or len(all_rows) != int(summary.get("top_count", -1)):
        raise ValueError("A-pool row count mismatch")
    if len({x["vendor_symbol"] for x in all_rows}) != len(all_rows):
        raise ValueError("Duplicate A-pool codes")
    if [int(x["research_rank"]) for x in all_rows] != list(range(1, len(all_rows)+1)):
        raise ValueError("Unverified ranks")
    return all_rows[:top]


def load_market(gold_root: Path, day: date, symbols: set[str]):
    import pandas as pd
    manifest_file = gold_root / "manifest.json"
    if not manifest_file.is_file():
        raise FileNotFoundError("Gold validation manifest not found")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    validated = manifest.get("validation", {})
    gold_date = date_from_any(manifest["as_of_date"])
    if gold_date < day:
        raise ValueError("Gold snapshot older than research date")
    if validated.get("primary_keys_unique") is not True or validated.get("ohlc_valid") is not True:
        raise ValueError("Gold has not passed OHLC / key validation")
    out = {}
    for dataset in ("daily_prices", "adjustment_factors"):
        frames = []
        for y, m in month_pairs(day):
            path = gold_root / dataset / f"year={y}" / f"month={m:02d}" / "data.parquet"
            if path.is_file():
                data = pd.read_parquet(path)
                if "vendor_symbol" not in data or "trade_date" not in data:
                    raise ValueError(f"{dataset} schema missing symbol/date")
                data = data.loc[data["vendor_symbol"].isin(symbols)]
                frames.append(data)
        if not frames:
            raise FileNotFoundError(f"No six-month {dataset} partitions for date {day}")
        result = pd.concat(frames, ignore_index=True)
        if result.duplicated(["vendor_symbol", "trade_date"]).any():
            raise ValueError(f"Duplicate {dataset} source bars")
        result["trade_date"] = pd.to_datetime(result["trade_date"]).dt.date
        result = result.loc[result["trade_date"] <= day].copy()
        if dataset == "daily_prices" and not REQUIRED_MARKET.issubset(result.columns):
            raise ValueError("Missing required Gold OHLCV fields")
        if dataset == "adjustment_factors" and "adjustment_factor" not in result:
            raise ValueError("Missing adjustment_factor")
        out[dataset] = result
    factor = out["adjustment_factors"][["vendor_symbol", "trade_date", "adjustment_factor"]]
    bars = out["daily_prices"].merge(factor,
                     on=["vendor_symbol", "trade_date"], how="left", validate="one_to_one")
    return bars, {"gold_as_of": gold_date.isoformat(),
                  "price_partitions": "Gold/daily_prices/year=*/month=*/data.parquet",
                  "factor_partitions": "Gold/adjustment_factors/year=*/month=*/data.parquet",
                  "period_months": 6}


def technical_role(bars, symbol: str, day: date):
    import pandas as pd
    part = bars.loc[bars["vendor_symbol"].eq(symbol)].copy()
    if part.empty:
        return {"status": "NO_BAR_DATA", "facts": {}, "flags": ["无该股票的K线"]}
    part = part.sort_values("trade_date")
    part = part.dropna(subset=["open", "high", "low", "close", "adjustment_factor"])
    for column in ("open", "high", "low", "close", "volume_shares",
                   "turnover_cny", "adjustment_factor"):
        part[column] = pd.to_numeric(part[column], errors="coerce")
    valid = (part["is_quality_valid"].eq(True) &
             part[["open", "high", "low", "close", "adjustment_factor"]].gt(0).all(axis=1) &
             part["volume_shares"].gt(0))
    part = part.loc[valid].copy()
    if part.empty or part["trade_date"].iloc[-1] != day:
        return {"status": "NO_SAME_DAY_VALID_BAR", "facts": {},
                "flags": ["研究日无有效K线；不使用旧价格冒充当日K线"]}
    if len(part) < 21:
        return {"status": "INSUFFICIENT_HISTORY", "facts": {"valid_bars": len(part)},
                "flags": ["有效历史不足21日，不计算趋势"]}
    # Rebase point-in-time adjusted series to the unadjusted as-of price.
    # Raw provider adjustment factors have an arbitrary scale across issuers.
    scale = part["adjustment_factor"] / float(part["adjustment_factor"].iloc[-1])
    close = part["close"] * scale
    high = part["high"] * scale
    low = part["low"] * scale
    prev = close.shift(1)
    tr = pd.concat([high-low, (high-prev).abs(), (low-prev).abs()], axis=1).max(axis=1)
    current = float(close.iloc[-1])
    ma5, ma20 = float(close.tail(5).mean()), float(close.tail(20).mean())
    ma60 = float(close.tail(60).mean()) if len(part) >= 60 else None
    ret20 = current / float(close.iloc[-21]) - 1
    atr14 = float(tr.tail(14).mean() / current)
    prior20 = float(high.iloc[-21:-1].max())
    v20 = float(part["volume_shares"].iloc[-21:-1].mean())
    volume_ratio = float(part["volume_shares"].iloc[-1] / v20) if v20 > 0 else None
    flags = []
    if len(part) < 60:
        flags.append("少于60日历史，无法计算MA60")
    if atr14 > 0.07:
        flags.append("ATR14占价格比例较高")
    if volume_ratio and volume_ratio > 4:
        flags.append("近期量能异常放大，需要核实消息来源")
    facts = {
        "bar_date": day.isoformat(), "valid_bars": len(part),
        "close_unadjusted": round_value(part["close"].iloc[-1], 3),
        "ma5_adjusted": round_value(ma5), "ma20_adjusted": round_value(ma20),
        "ma60_adjusted": round_value(ma60),
        "return_20_sessions_pct": round_value(ret20 * 100, 2),
        "atr14_pct": round_value(atr14 * 100, 2),
        "volume_vs_prior20": round_value(volume_ratio, 2),
        "breakout_vs_prior20_high": bool(current > prior20),
        "ma5_above_ma20": bool(ma5 > ma20),
    }
    return {"status": "CALCULATED_GOLD_PIT", "facts": facts, "flags": flags,
            "source": "validated 6-month Gold OHLCV + same-day adjustment factors"}


def load_financial(csv_path: Path | None, symbols: set[str], day: date):
    if csv_path is None or not csv_path.is_file():
        return {}
    with csv_path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not REQUIRED_FINANCE.issubset(reader.fieldnames):
            raise ValueError("Financial CSV missing required dates/indicators")
        rows = [r for r in reader if r["ts_code"] in symbols]
    records = {}
    for symbol in symbols:
        known = []
        for row in rows:
            if row["ts_code"] != symbol or not row["ann_date"] or not row["end_date"]:
                continue
            ann, end = date_from_any(row["ann_date"]), date_from_any(row["end_date"])
            if ann <= day and end <= day:
                known.append((ann, end, row))
        if known:
            known.sort(key=lambda x: (x[0], x[1]))
            records[symbol] = known[-1]
    return records


def fundamental_role(records, symbol: str, day: date):
    if symbol not in records:
        return {"status": "NO_DATED_FINANCIAL", "facts": {}, "flags": ["缺少可回溯公告日期的财报数据"]}
    ann, end, row = records[symbol]
    age = (day - ann).days
    facts = {
        "financial_end_date": end.isoformat(), "announcement_date": ann.isoformat(),
        "announcement_age_days": age,
        "roe_pct": round_value(finite(row["roe"])),
        "debt_to_assets_pct": round_value(finite(row["debt_to_assets"])),
        "current_ratio": round_value(finite(row["current_ratio"])),
        "netprofit_margin_pct": round_value(finite(row["netprofit_margin"])),
    }
    flags = []
    if age > 150:
        flags.append("财务指标披露距研究日超过150天，需核验更新报告")
    if facts["roe_pct"] is not None and facts["roe_pct"] < 0:
        flags.append("报告期ROE为负")
    if facts["debt_to_assets_pct"] is not None and facts["debt_to_assets_pct"] > 75:
        flags.append("资产负债率较高")
    if facts["current_ratio"] is not None and facts["current_ratio"] < 1:
        flags.append("流动比率低于1")
    return {"status": "DATED_FINANCIAL_STALE" if age > 150 else "DATED_FINANCIAL_AVAILABLE",
            "facts": facts, "flags": flags,
            "source": "Drive financial_data/fina_indicator_q.csv (ann_date filtered)"}


def evidence_role(path: Path | None, symbols: set[str], day: date):
    """Curated web research from a human/chat window; never fetched by workflow.

    captured_at must not be later than as_of for PIT evidence (no hindsight).
    """
    if path is None:
        return {}
    obj = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(obj, list):
        raise ValueError("Web evidence requires a JSON list")
    saved = {symbol: [] for symbol in symbols}
    for record in obj:
        if not isinstance(record, dict):
            raise ValueError("Web evidence row must be object")
        symbol = record.get("symbol")
        if symbol not in symbols:
            continue
        needed = ("published_at", "captured_at", "source_url", "headline")
        if not all(isinstance(record.get(k), str) and record[k].strip() for k in needed):
            raise ValueError("Evidence must have dates, URL and headline")
        published, captured = date_from_any(record["published_at"]), date_from_any(record["captured_at"])
        if published > day or captured > day:
            continue
        url = record["source_url"]
        if not (url.startswith("https://") or url.startswith("http://")):
            raise ValueError("Invalid evidence source URL")
        saved[symbol].append({"published_at": published.isoformat(),
                              "captured_at": captured.isoformat(),
                              "headline": record["headline"][:260],
                              "source_url": url[:600],
                              "evidence_type": record.get("evidence_type", "unclassified")})
    return saved


def compose(row, tech, financial, news):
    flags = list(tech.get("flags", [])) + list(financial.get("flags", []))
    age = finite(row.get("max_disclosure_age_days"))
    if age is not None and age > 120:
        flags.append("股东披露间隔超过120天")
    if not news:
        flags.append("未导入当时已捕获的可核验公告或新闻；不可据此认定无利空")
    if tech["status"] != "CALCULATED_GOLD_PIT":
        flags.append("当日技术分析缺少有效K线")
    positive = []
    negative = []
    tf, ff = tech.get("facts", {}), financial.get("facts", {})
    if tf.get("ma5_above_ma20"):
        positive.append("MA5高于MA20（Gold K线计算）")
    elif tf.get("ma5_above_ma20") is False:
        negative.append("MA5未高于MA20（Gold K线计算）")
    if tf.get("breakout_vs_prior20_high"):
        positive.append("收盘突破此前20日高点")
    if ff.get("roe_pct") is not None:
        (positive if ff["roe_pct"] > 0 else negative).append(
            f"已披露ROE为{ff['roe_pct']}%")
    if ff.get("netprofit_margin_pct") is not None and ff["netprofit_margin_pct"] < 0:
        negative.append("已披露净利率为负")
    if not positive:
        positive.append("原状态研究池标记为" + str(row["state_cn"]) + "（仅观察）")
    if not negative:
        negative.append("基本面、公告或技术指标可能存在未覆盖风险")
    incomplete = (tech["status"] != "CALCULATED_GOLD_PIT" or
                  financial["status"] == "NO_DATED_FINANCIAL" or
                  not news)
    return {
        "symbol": row["vendor_symbol"], "name": row["name_zh"],
        "research_rank": int(row["research_rank"]),
        "original_priority_score": finite(row["research_priority_score"]),
        "original_state": row["state_cn"],
        "technical_agent": tech, "fundamental_agent": financial,
        "news_agent": {"status": "DATED_SOURCE_AVAILABLE" if news else "NO_CAPTURED_PIT_NEWS",
                       "evidence": news},
        "bull_agent": {"evidence": positive},
        "bear_agent": {"evidence": negative},
        "risk_agent": {"flags": flags, "holder_disclosure_age_days": age},
        "manager_agent": {"decision": "RESEARCH_ONLY_NEEDS_REVIEW" if incomplete
                                     else "RESEARCH_ONLY_EVIDENCE_COMPILED",
                          "trade_authorization": AUTHORIZATION,
                          "missing_news_is_not_no_risk": not bool(news)},
    }


def render(report):
    lines = ["# A股离线多角色投研（无LLM/无外部API）", "",
             f"研究日：{report['as_of']}；模式：{report['mode']}", "",
             "**原冻结 V1 不变；仅作研究，绝无实盘买卖许可。**", ""]
    for s in report["stocks"]:
        lines += [f"## {s['research_rank']}. {s['name']}（{s['symbol']}）", "",
                  f"原状态：{s['original_state']}；原研究排序分：{s['original_priority_score']}", ""]
        for key, title in [("technical_agent", "技术"), ("fundamental_agent", "基本面")]:
            agent = s[key]
            facts = "；".join(f"{k}={v}" for k, v in agent["facts"].items())
            lines += [f"**{title} Agent：{agent['status']}**", facts or "无可用数据", ""]
        lines += ["**消息 Agent：**" + s["news_agent"]["status"]]
        for note in s["news_agent"]["evidence"]:
            lines.append(f"- [{note['headline']}]({note['source_url']})，披露 {note['published_at']}，采集 {note['captured_at']}")
        lines += ["", "**多方 Agent：**" + "；".join(s["bull_agent"]["evidence"]), "",
                  "**空方 Agent：**" + "；".join(s["bear_agent"]["evidence"]), "",
                  "**风控 Agent：**" + "；".join(s["risk_agent"]["flags"]), "",
                  "**研究经理：**" + s["manager_agent"]["decision"], ""]
    lines += ["## 边界与溯源", "",
              "- 技术数据：Drive Gold 六个月行情和对应日期复权因子，禁止使用研究日以后行情。",
              "- 财务数据：仅使用公告日不晚于研究日的单期财务指标，不对无公告日期的收入表进行历史补算。",
              "- 新闻数据：来自当前窗口人工整理的网页证据，只有采集日与披露日均不晚于研究日才参加时点研究。",
              "- 未抓取到新闻不代表无风险；脚本不联网、不获取收费数据、不调用任何大模型。",
              "- " + AUTHORIZATION]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watchlist", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--gold-root", type=Path, required=True)
    parser.add_argument("--financial-csv", type=Path)
    parser.add_argument("--web-evidence", type=Path)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    day = date.fromisoformat(args.as_of)
    rows = load_validated_watchlist(args.watchlist, args.summary, day, args.top)
    symbols = {r["vendor_symbol"] for r in rows}
    market, provenance = load_market(args.gold_root, day, symbols)
    financial = load_financial(args.financial_csv, symbols, day)
    evidence = evidence_role(args.web_evidence, symbols, day)
    stocks = [
        compose(row,
                technical_role(market, row["vendor_symbol"], day),
                fundamental_role(financial, row["vendor_symbol"], day),
                evidence.get(row["vendor_symbol"], []))
        for row in rows
    ]
    report = {"version": VERSION, "as_of": day.isoformat(),
              "generated_utc": datetime.now(timezone.utc).isoformat(),
              "mode": "DETERMINISTIC_MULTI_ROLE_NO_LLM_NO_NETWORK",
              "source": {"gold": provenance, "financial": str(args.financial_csv)
                         if args.financial_csv else None,
                         "web_evidence": "supplied PIT observations" if args.web_evidence else None},
              "authorization": AUTHORIZATION, "stocks": stocks}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False,
                                                           indent=2), encoding="utf-8")
    markdown = render(report)
    (args.output_dir / "report.md").write_text(markdown, encoding="utf-8")
    page = ('<!doctype html><meta charset="utf-8"><title>离线多角色投研</title>'
            '<style>body{max-width:1000px;margin:auto;padding:30px;font:16px/1.6 sans-serif}'
            'pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><pre>' +
            html.escape(markdown) + '</pre>')
    (args.output_dir / "report.html").write_text(page, encoding="utf-8")
    print(json.dumps({"status": "SUCCESS", "mode": report["mode"],
                      "date": day.isoformat(), "count": len(stocks),
                      "authorization": AUTHORIZATION}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
