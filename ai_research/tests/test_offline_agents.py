"""Unit checks for deterministic multi-role stock analysis, never invoke outside services."""
import csv
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from ai_research.offline_agents import (
    AUTHORIZATION, compose, date_from_any, evidence_role,
    fundamental_role, load_financial, load_validated_watchlist, month_pairs,
)


class OfflineRoles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.day = date(2026, 10, 9)
        self.symbol = "600351.SH"

    def test_rolling_months_and_date_conversion(self):
        self.assertEqual(list(month_pairs(date(2026, 1, 1), 3)),
                         [(2026, 1), (2025, 12), (2025, 11)])
        self.assertEqual(date_from_any("20260425"), date(2026, 4, 25))

    def test_financial_announcement_is_pit_filtered(self):
        path = self.root / "financial.csv"
        keys = ["ts_code", "end_date", "ann_date", "roe",
                "debt_to_assets", "current_ratio", "netprofit_margin"]
        with path.open("w", encoding="utf-8", newline="") as stream:
            w = csv.DictWriter(stream, fieldnames=keys)
            w.writeheader()
            w.writerow(dict(zip(keys, [self.symbol, "20260331", "20260425",
                                       "3.7186", "14.7926", "3.9154", "18.7008"])))
            w.writerow(dict(zip(keys, [self.symbol, "20260930", "20261020",
                                       "95", "1", "9", "99"])))
        records = load_financial(path, {self.symbol}, self.day)
        f = fundamental_role(records, self.symbol, self.day)
        self.assertEqual(f["facts"]["financial_end_date"], "2026-03-31")
        self.assertEqual(f["facts"]["roe_pct"], 3.719)
        self.assertEqual(f["status"], "DATED_FINANCIAL_STALE")

    def test_web_evidence_rejects_hindsight(self):
        path = self.root / "evidence.json"
        path.write_text(json.dumps([
            {"symbol": self.symbol, "published_at": "2026-10-07",
             "captured_at": "2026-10-08", "source_url": "https://example.org/1",
             "headline": "PIT historical notice"},
            {"symbol": self.symbol, "published_at": "2026-10-07",
             "captured_at": "2026-10-12", "source_url": "https://example.org/2",
             "headline": "Found too late"},
            {"symbol": self.symbol, "published_at": "2026-10-10",
             "captured_at": "2026-10-10", "source_url": "https://example.org/3",
             "headline": "Future"},
        ], ensure_ascii=False), encoding="utf-8")
        evidence = evidence_role(path, {self.symbol}, self.day)
        self.assertEqual([x["headline"] for x in evidence[self.symbol]],
                         ["PIT historical notice"])

    def test_manager_never_authorizes_trade(self):
        row = {"vendor_symbol": self.symbol, "name_zh": "测试", "research_rank": "1",
               "research_priority_score": "75", "state_cn": "拉升",
               "max_disclosure_age_days": "68"}
        tech = {"status": "CALCULATED_GOLD_PIT",
                "facts": {"ma5_above_ma20": True,
                          "breakout_vs_prior20_high": True}, "flags": []}
        finance = {"status": "DATED_FINANCIAL_AVAILABLE",
                   "facts": {"roe_pct": 3.5}, "flags": []}
        result = compose(row, tech, finance, [])
        self.assertEqual(result["manager_agent"]["trade_authorization"], AUTHORIZATION)
        self.assertTrue(result["manager_agent"]["missing_news_is_not_no_risk"])
        self.assertEqual(result["news_agent"]["status"], "NO_CAPTURED_PIT_NEWS")

    def test_watchlist_date_guard(self):
        source = self.root / "watch.csv"
        summary = self.root / "summary.json"
        with source.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["research_rank", "vendor_symbol",
               "name_zh", "state_cn", "research_priority_score", "confidence",
               "max_disclosure_age_days"])
            w.writeheader()
            w.writerow(dict(research_rank="1", vendor_symbol=self.symbol,
                            name_zh="测试", state_cn="拉升",
                            research_priority_score="75", confidence="90",
                            max_disclosure_age_days="68"))
        summary.write_text(json.dumps({"as_of_date": "2026-10-10",
                  "authorization": "WATCHLIST_ONLY_NO_TRADE",
                  "original_candidates_unchanged": True, "top_count": 1}),
                  encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Unverified"):
            load_validated_watchlist(source, summary, self.day, 1)


if __name__ == "__main__":
    unittest.main()
