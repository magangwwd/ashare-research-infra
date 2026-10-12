"""Offline tests: no real provider, no Git/Drive writes, no market-data fabrication."""
import csv
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_research.run_daily import (
    AUTHORIZATION, load_evidence, main, verified_rows,
)


def row(rank, symbol):
    return {
        "research_rank": str(rank),
        "priority_tier": "P1_DISCLOSURES_WITHIN_120D",
        "research_priority_score": "75.81",
        "vendor_symbol": symbol,
        "name_zh": "测试",
        "state_cn": "拉升",
        "confidence": "90",
        "stable_days_5": "5",
        "state_margin": "22.57",
        "avg_turnover_20_100m_cny": "1.31",
        "max_disclosure_age_days": "69",
    }


class ShadowV01(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.csvfile = self.root / "watchlist_top20.csv"
        self.summary = self.root / "ranking_summary.json"
        self.output = self.root / "output"
        self.rows = [row(1, "600351.SH"), row(2, "002107.SZ")]
        self.write_rows()
        self.write_summary()

    def write_rows(self):
        with self.csvfile.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=list(self.rows[0]))
            writer.writeheader()
            writer.writerows(self.rows)

    def write_summary(self, **updates):
        data = {
            "as_of_date": "2026-10-09",
            "top_count": len(self.rows),
            "original_candidates_unchanged": True,
            "authorization": "WATCHLIST_ONLY_NO_TRADE",
        }
        data.update(updates)
        self.summary.write_text(json.dumps(data), encoding="utf-8")

    def command(self, *extra):
        return [
            "--watchlist", str(self.csvfile), "--summary", str(self.summary),
            "--as-of", "2026-10-09", "--output-dir", str(self.output), *extra
        ]

    def test_offline_report_explicitly_non_ai(self):
        with patch("urllib.request.urlopen", side_effect=AssertionError("network used")):
            self.assertEqual(main(self.command("--top", "2")), 0)
        result = json.loads((self.output / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(result["mode"], "RULE_ONLY_NO_API")
        self.assertEqual(result["authorization"], AUTHORIZATION)
        self.assertEqual(len(result["stocks"]), 2)
        self.assertEqual(result["stocks"][0]["fundamental_agent"]["status"],
                         "NO_POINT_IN_TIME_DATA")
        self.assertEqual(result["stocks"][0]["news_agent"]["status"],
                         "NO_POINT_IN_TIME_DATA")
        self.assertIsNone(result["stocks"][0]["llm_commentary"])
        self.assertTrue((self.output / "report.html").is_file())

    def test_summary_date_and_authorization_are_strict(self):
        self.write_summary(as_of_date="2026-10-10")
        with self.assertRaisesRegex(ValueError, "date mismatch"):
            verified_rows(self.csvfile, self.summary, "2026-10-09", 2)
        self.write_summary(authorization="LIVE_TRADING")
        with self.assertRaisesRegex(ValueError, "paper-only"):
            verified_rows(self.csvfile, self.summary, "2026-10-09", 2)

    def test_duplicates_and_ranks_rejected(self):
        self.rows[1]["vendor_symbol"] = self.rows[0]["vendor_symbol"]
        self.write_rows()
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            verified_rows(self.csvfile, self.summary, "2026-10-09", 2)
        self.rows[1]["vendor_symbol"] = "002107.SZ"
        self.rows[1]["research_rank"] = "3"
        self.write_rows()
        with self.assertRaisesRegex(ValueError, "sequential"):
            verified_rows(self.csvfile, self.summary, "2026-10-09", 2)

    def test_evidence_future_dated_is_excluded(self):
        path = self.root / "evidence.json"
        path.write_text(json.dumps({
            "600351.SH": {"news": [
                {"published_at": "2026-10-09", "source": "公告", "text": "可用"},
                {"published_at": "2026-10-10", "source": "公告", "text": "未来"},
            ]}
        }, ensure_ascii=False), encoding="utf-8")
        data = load_evidence(path, "2026-10-09")
        self.assertEqual(len(data["600351.SH"]["news"]), 1)
        self.assertEqual(data["600351.SH"]["news"][0]["text"], "可用")

    def test_llm_requires_explicit_opt_in_configuration(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "HTTPS"):
                main(self.command("--enable-llm"))
        self.assertFalse(self.output.exists())

    def test_html_escape(self):
        self.rows[0]["name_zh"] = "<script>alert(1)</script>"
        self.write_rows()
        main(self.command("--top", "1"))
        result = (self.output / "report.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>", result)
        self.assertIn("&lt;script&gt;", result)


if __name__ == "__main__":
    unittest.main()
