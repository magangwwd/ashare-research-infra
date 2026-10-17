"""Offline invariant tests for the fixed-five report; no Drive, APIs or live orders."""
import copy
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from ai_research.five_stock_decision import build, save, POLICY
from ai_research.manual_ai_review_pack import SYMBOLS

NOW = datetime(2026, 10, 17, 8, tzinfo=timezone.utc)


def r1():
    return {
        "status": "PAPER_ONLY_R1_FIVE_NO_ENTRY_AUTHORIZATION",
        "snapshot_date": "2026-10-16", "gold_as_of": "2026-10-16",
        "manifest_sha256": "a" * 64, "policy_sha256": "b" * 64,
        "scope": list(SYMBOLS),
        "policy": {"id": POLICY, "ma20_above_minimum": .5},
        "market_breadth": {"eligible_symbols": 3188, "above_ma20": 667,
                           "fraction": 667 / 3188, "passed": False},
        "candidate_count": 0,
        "checked": [{"symbol": s, "signal_date": "2026-10-16",
                     "candidate": False, "display_label_unverified": s,
                     "signal_close_raw": 10, "status": "BREADTH_NOT_PASSED",
                     "pattern": ""} for s in SYMBOLS],
    }


def finance():
    example = {
        "20260331": {"income": {"fields": {"n_income_attr_p": 9},
                                "announcement_date": "2026-04-20"}},
        "20260630": {
            "income": {"fields": {"n_income_attr_p": 25, "total_revenue": 200},
                       "announcement_date": "2026-07-16", "version": "fake-verified-for-tests",
                       "id": "F:income"},
            "balancesheet": {"fields": {"total_assets": 500, "total_liab": 120,
                                        "accounts_receiv": 20},
                             "announcement_date": "2026-07-16", "id": "F:balance"},
            "cashflow": {"fields": {"n_cashflow_act": 10},
                         "announcement_date": "2026-07-16", "id": "F:cash"},
            "fina_indicator": {"fields": {"profit_dedt": 20, "or_yoy": 8, "netprofit_yoy": 10},
                               "announcement_date": "2026-07-16", "id": "F:indicator"}
        }}
    return {s: copy.deepcopy(example) for s in SYMBOLS}


MARKER = {"published_at": "2026-10-12T07:53:38+00:00", "version": "fake-verified-for-tests"}


class FixedFiveTests(unittest.TestCase):
    def test_realistic_zero_candidate(self):
        report = build(r1(), finance(), MARKER, now=NOW)
        self.assertEqual(len(report["stocks"]), 5)
        self.assertEqual({s["research_status"] for s in report["stocks"]}, {"WATCH"})
        self.assertEqual(report["r1_candidate_count"], 0)
        self.assertTrue(all(s["paper_entry_authorized"] is False for s in report["stocks"]))
        self.assertEqual(report["stocks"][0]["finance"]["q2_incremental_profit_cny"], 16)
        with tempfile.TemporaryDirectory() as folder:
            save(report, folder)
            files = set(p.name for p in Path(folder).iterdir())
            self.assertEqual(files, {"report.json", "report.md", "coverage.json",
                                     "ab_forward.json", "manifest.json"})
            ab = json.loads((Path(folder) / "ab_forward.json").read_text())
            self.assertIsNone(ab["pnl"])
            self.assertEqual(ab["a_r1_candidates"], [])

    def test_invalid_scope_rejected(self):
        wrong = r1()
        wrong["checked"][3]["symbol"] = "000001.SZ"
        with self.assertRaisesRegex(ValueError, "Invalid fixed-five"):
            build(wrong, finance(), MARKER, now=NOW)

    def test_candidate_requires_breadth(self):
        wrong = r1()
        wrong["checked"][0]["candidate"] = True
        wrong["candidate_count"] = 1
        with self.assertRaisesRegex(ValueError, "Contradictory R1 candidate"):
            build(wrong, finance(), MARKER, now=NOW)

    def test_candidate_with_news_is_still_next_open_conditional(self):
        data = r1()
        data["market_breadth"].update(above_ma20=1800, fraction=1800 / 3188, passed=True)
        data["checked"][0].update(candidate=True, status="CANDIDATE")
        data["candidate_count"] = 1
        news = {"events": [{"symbol": SYMBOLS[0], "source_url": "https://example.org/filing",
                            "title": "issuer document", "published_at": "2026-10-16T05:00:00Z",
                            "captured_at": "2026-10-16T06:00:00Z"}]}
        result = build(data, finance(), MARKER, news, now=NOW)
        self.assertEqual(result["stocks"][0]["research_status"], "SETUP_PAPER_NEXT_OPEN_UNVERIFIED")
        self.assertFalse(result["stocks"][0]["paper_entry_authorized"])
        self.assertTrue(result["stocks"][0]["two_round_rule_based_review"]["needs_manual_verification"])

    def test_news_capture_after_cutoff_is_excluded(self):
        data = r1()
        future_news = {"events": [{"symbol": SYMBOLS[0], "source_url": "https://example.org/filing",
                                   "title": "Future", "published_at": "2026-10-17T07:00:00Z",
                                   "captured_at": "2026-10-17T09:00:00Z"}]}
        result = build(data, finance(), MARKER, future_news, now=NOW)
        self.assertEqual(result["stocks"][0]["announcements"], [])
        self.assertEqual(result["stocks"][0]["news_status"], "NEWS_COVERAGE_MISSING")

    def test_future_finance_publication_rejected(self):
        marker = {**MARKER, "published_at": "2026-10-18T00:00:00+00:00"}
        with self.assertRaisesRegex(ValueError, "Future captured"):
            build(r1(), finance(), marker, now=NOW)

    def test_future_announcement_rejected(self):
        f = finance()
        f[SYMBOLS[0]]["20260630"]["cashflow"]["announcement_date"] = "2026-10-19"
        with self.assertRaisesRegex(ValueError, "Future dated finance"):
            build(r1(), f, MARKER, now=NOW)

    def test_missing_finance_fail_closed(self):
        result = build(r1(), {}, None, now=NOW)
        self.assertEqual(result["stocks"][0]["finance"]["data_gaps_and_risks"],
                         ["FINANCE_COVERAGE_MISSING"])
        self.assertFalse(result["stocks"][0]["paper_entry_authorized"])

    def test_inconsistent_breadth_rejected(self):
        wrong = r1()
        wrong["market_breadth"]["fraction"] = .55
        with self.assertRaisesRegex(ValueError, "Inconsistent"):
            build(wrong, finance(), MARKER, now=NOW)

    def test_financial_hard_veto(self):
        data = r1()
        data["market_breadth"].update(above_ma20=1800, fraction=1800 / 3188, passed=True)
        data["checked"][2].update(candidate=True, status="CANDIDATE")
        data["candidate_count"] = 1
        f = finance()
        f[SYMBOLS[2]]["20260630"]["cashflow"]["fields"]["n_cashflow_act"] = -10
        news = {"events": [{"symbol": SYMBOLS[2], "source_url": "https://example.org/issuer",
                            "title": "A", "published_at": "2026-10-16T02:00:00Z",
                            "captured_at": "2026-10-16T03:00:00Z"}]}
        result = build(data, f, MARKER, news, now=NOW)
        self.assertEqual(result["stocks"][2]["research_status"], "AVOID_EXIT_REVIEW")
        self.assertFalse(result["stocks"][2]["paper_entry_authorized"])


if __name__ == "__main__":
    unittest.main()
