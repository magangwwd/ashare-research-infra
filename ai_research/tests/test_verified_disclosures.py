import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from ai_research.verified_disclosures import evaluate, load_records, SYMBOLS

SEED = Path(__file__).resolve().parents[1] / "seed" / "verified_five_halfyear_20261012.json"

class VerifiedDisclosuresTest(unittest.TestCase):
    def test_fixture_has_five_validated_company_filing_records(self):
        self.assertEqual({r["symbol"] for r in load_records(SEED)}, SYMBOLS)

    def test_no_hindsight_at_or_before_capture_date(self):
        for asof in ("2026-10-09", "2026-10-12"):
            available, audit = evaluate(SEED, date.fromisoformat(asof), SYMBOLS)
            self.assertFalse(available)
            self.assertEqual(audit["covered"], 0)
            self.assertTrue(audit["does_not_establish_clean_etl_coverage"])

    def test_next_session_can_read_documentary_sidecar(self):
        records, audit = evaluate(SEED, date(2026, 10, 13), SYMBOLS)
        self.assertEqual(len(records), 5)
        self.assertEqual(audit["covered"], 5)
        self.assertLess(records["600641.SH"]["financials"]["operating_cashflow_cny"], 0)

    def test_reject_duplicate(self):
        rows = load_records(SEED)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "bad.json"
            path.write_text(json.dumps(rows + [rows[0]], ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                load_records(path)

    def test_reject_missing_currency_number(self):
        rows = load_records(SEED)
        rows[0]["financials"]["revenue_cny"] = None
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "bad.json"
            path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Missing financial"):
                load_records(path)

if __name__ == "__main__":
    unittest.main()
