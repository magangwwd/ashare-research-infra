"""No-network PIT guards for GPT role-play morning evidence packets."""
import unittest
from datetime import datetime, timezone
from ai_research.morning_review_packet import build, format_prompt, SYMBOLS, STATUS

REVIEW = datetime(2026, 10, 17, 13, 40, tzinfo=timezone.utc)


def report():
    return {
        "version": "FIVE_STOCK_DECISION_V1", "as_of": "2026-10-16",
        "authorization": STATUS, "r1_candidate_count": 0,
        "breadth": {"eligible_symbols": 3188, "above_ma20": 667,
                    "fraction": 667 / 3188, "passed": False},
        "source": {"r1_snapshot_sha256": "a"*64, "finance_version": "test-clean"},
        "stocks": [{"symbol": sym, "close_raw_cny": 6.84, "r1_candidate": False,
                    "r1_status": "BREADTH_NOT_PASSED", "r1_pattern": "",
                    "research_status": "WATCH",
                    "finance": {"report_period": "2026-06-30", "h1_revenue_cny": 100,
                                "h1_attributable_profit_cny": 10,
                                "h1_operating_cashflow_cny": 12, "record_ids": {},
                                "data_gaps_and_risks": ["PARTIAL_HISTORY"]},
                    "waiting_for": ["market breadth"]} for sym in SYMBOLS],
    }


def event(pub, cap, symbol=SYMBOLS[0]):
    return {"symbol": symbol, "title": "Issuer announcement",
            "source_url": "https://sse.com.cn/announcement", "event_kind": "filing",
            "published_at": pub, "captured_at": cap,
            "company_outlook": None, "expected_date": None, "short_fact": "Verified disclosure"}


class MorningPacketTests(unittest.TestCase):
    def test_summary_before_per_stock(self):
        x = build(report(), review_at=REVIEW)
        self.assertEqual([s["symbol"] for s in x["stocks"]], list(SYMBOLS))
        self.assertEqual(x["stocks"][2]["name"], "先导基电")
        self.assertEqual(x["r1_candidates"], 0)
        self.assertTrue(all(s["original_research_status"] == "WATCH" for s in x["stocks"]))
        self.assertFalse(any(s["entry_authorized"] for s in x["stocks"]))
        text = format_prompt(x)
        self.assertLess(text.index("先呈现五股结论"), text.index("每股依次呈现"))
        self.assertIn("舆情", text)

    def test_close_pit_vs_weekend_disclosure(self):
        evt = [
            event("2026-10-16T06:00:00Z", "2026-10-16T06:30:00Z"),
            event("2026-10-16T07:00:00Z", "2026-10-16T08:00:00Z"),
            event("2026-10-15T07:00:00Z", "2026-10-17T06:00:00Z"),
        ]
        x = build(report(), {"events": evt}, REVIEW)
        news = x["stocks"][0]["disclosure_evidence"]
        self.assertEqual(len(news), 3)
        self.assertEqual(sum(n["r1_signal_eligible"] for n in news), 1)
        self.assertEqual({n["timing"] for n in news},
                         {"PIT_CAPTURED_ON_SIGNAL_DAY", "POST_SIGNAL_SESSION",
                          "LATE_CAPTURE_NO_BACKTEST_USE"})

    def test_future_and_unverifiable_not_silently_promoted(self):
        news = {"events": [event("2026-10-17T14:00:00Z", "2026-10-17T15:00:00Z"),
                           event("2026-10-16T05:00:00Z", "2026-10-16T06:00:00Z",
                                 "000001.SZ")]}
        x = build(report(), news, REVIEW)
        self.assertEqual(x["stocks"][0]["disclosure_evidence"], [])
        self.assertEqual(x["stocks"][0]["disclosure_status"], "NO_CAPTURED_NEWS")

    def test_five_guard(self):
        r = report()
        r["stocks"].pop()
        with self.assertRaisesRegex(ValueError, "complete"):
            build(r, review_at=REVIEW)


if __name__ == "__main__":
    unittest.main()
