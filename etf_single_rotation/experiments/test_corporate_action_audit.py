"""Synthetic-only accounting diagnostics; never treat fixtures as ETF market quotes."""
import unittest
import json
from pathlib import Path

from etf_single_rotation.experiments.audit_corporate_actions import (analyze, validate_events, candidate_dividend_coverage)


def action(kind="UNIT_SPLIT"):
    result = {
        "symbol": "512690.SH",
        "event_date": "2021-05-14",
        "type": kind,
        "source_url": "https://example.org/notice.pdf",
    }
    if kind == "CASH_DIVIDEND":
        result.update({
            "event_date": "2021-12-31",
            "dividend_per_current_share_cny": 0.35,
            "registration_date": "2021-12-30",
            "cash_payment_date": "2022-01-06",
        })
    else:
        result["new_units_per_old_unit"] = 2 if kind == "UNIT_SPLIT" else 0.5
    return result


def quotes(before, after, factor_before, factor_after, d0, d1):
    return [
        {"symbol": "512690.SH", "date": d0, "raw_close": before, "adj_factor": factor_before},
        {"symbol": "512690.SH", "date": d1, "raw_close": after, "adj_factor": factor_after},
    ]


class CorporateActionDiagnosticTests(unittest.TestCase):
    def test_split_changes_units_not_cash(self):
        result = analyze(
            {"schema_version": 1, "events": [action()]},
            quotes(2, 1, 1, 2, "2021-05-13", "2021-05-17"),
        )[0]
        self.assertAlmostEqual(result["economic_event_window_return"], 0)
        self.assertAlmostEqual(result["fund_adj_event_window_return"], 0)
        self.assertFalse(result["needs_manual_review"])

    def test_dividend_entitlement_not_double_counted(self):
        result = analyze(
            {"schema_version": 1, "events": [action("CASH_DIVIDEND")]},
            quotes(2, 1.65, 1, 2 / 1.65, "2021-12-30", "2021-12-31"),
        )[0]
        self.assertAlmostEqual(result["economic_event_window_return"], 0)
        self.assertAlmostEqual(result["fund_adj_event_window_return"], 0)
        self.assertEqual(result["cash_payment_date"], "2022-01-06")

    def test_factor_gap_flags_review(self):
        result = analyze(
            {"schema_version": 1, "events": [action()]},
            quotes(2, 1, 1, 1, "2021-05-13", "2021-05-17"),
        )[0]
        self.assertTrue(result["needs_manual_review"])
        self.assertAlmostEqual(result["fund_adj_minus_event_return_bp"], -5000)

    def test_duplicate_event_fails_closed(self):
        item = action()
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            validate_events({"schema_version": 1, "events": [item, item]})

    def test_wrong_split_ratio_fails_closed(self):
        item = action()
        item["new_units_per_old_unit"] = 0.5
        with self.assertRaisesRegex(ValueError, "contradicts"):
            validate_events({"schema_version": 1, "events": [item]})


    def test_boolean_dividend_or_price_fails_closed(self):
        # float(True) == 1.0; never silently convert boolean input into
        # a cash dividend, a market quote, or an adjustment factor.
        dividend = action("CASH_DIVIDEND")
        dividend["dividend_per_current_share_cny"] = True
        with self.assertRaisesRegex(ValueError, "Invalid dividend"):
            validate_events({"schema_version": 1, "events": [dividend]})
        for field in ("raw_close", "adj_factor"):
            rows = quotes(2, 1, 1, 2, "2021-05-13", "2021-05-17")
            rows[0][field] = True
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "Invalid"):
                    analyze({"schema_version": 1, "events": [action()]}, rows)

    def test_dividend_record_and_payment_dates_must_be_canonical(self):
        # date.fromisoformat accepts YYYYMMDD, which would not match the
        # YYYY-MM-DD snapshot keys used by the entitlement ledger.
        for field, bad in (
            ("registration_date", "20211230"),
            ("cash_payment_date", "20220106"),
            ("registration_date", "2021-12-99"),
            ("cash_payment_date", None),
        ):
            with self.subTest(field=field, bad=bad):
                item = action("CASH_DIVIDEND")
                item[field] = bad
                with self.assertRaisesRegex(ValueError, "date"):
                    validate_events({"schema_version": 1, "events": [item]})
        item = action("CASH_DIVIDEND")
        del item["registration_date"]
        with self.assertRaisesRegex(ValueError, "date"):
            validate_events({"schema_version": 1, "events": [item]})

    def test_noncanonical_real_price_dates_fail_closed(self):
        # Python date.fromisoformat accepts YYYYMMDD, but source rows and
        # evidence manifests must use exact YYYY-MM-DD to avoid silent coercion.
        for bad in ("20210513", "2021-05-99", None, 20210513):
            with self.subTest(bad=bad):
                rows = quotes(2, 1, 1, 2, "2021-05-13", "2021-05-17")
                rows[0]["date"] = bad
                with self.assertRaisesRegex(ValueError, "price date"):
                    analyze({"schema_version": 1, "events": [action()]}, rows)

    def test_missing_or_duplicate_price_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "No genuine quotes"):
            analyze({"schema_version": 1, "events": [action()]}, [])
        rows = quotes(2, 1, 1, 2, "2021-05-13", "2021-05-17")
        with self.assertRaisesRegex(ValueError, "Duplicate real price"):
            analyze({"schema_version": 1, "events": [action()]}, rows + rows[:1])


    def test_candidate_coverage_detects_eight_missing_510300_dividends(self):
        # Public secondary candidate list is NOT issuer completeness proof.
        root = Path(__file__).resolve().parents[1] / "metadata"
        registry = json.loads((root / "corporate_action_evidence.json").read_text(encoding="utf-8"))
        candidates = json.loads((root / "510300_dividend_coverage_gap_20261024.json").read_text(encoding="utf-8"))
        report = candidate_dividend_coverage(registry, candidates)
        self.assertEqual(report["candidate_events_in_window"], 8)
        self.assertEqual(len(report["missing_from_executable_registry"]), 8)
        self.assertEqual(report["conflicting_registry_events"], [])
        self.assertEqual(report["status"], "REVIEW_REQUIRED_NOT_FOR_TRADING")
        self.assertIn("SECONDARY_CANDIDATES", report["source_grade"])

    def test_candidate_coverage_detects_registry_cash_conflicts(self):
        actual = action("CASH_DIVIDEND")
        candidate = {
            "schema_version": 1,
            "symbol": "512690.SH",
            "events": [{
                "registration_date": "2021-12-30",
                "ex_dividend_date": "2021-12-31",
                "announced_payment_date": "2022-01-06",
                "dividend_per_share_cny": "0.36",
            }],
        }
        report = candidate_dividend_coverage(
            {"schema_version": 1, "events": [actual]}, candidate
        )
        self.assertEqual(report["missing_from_executable_registry"], [])
        self.assertEqual(report["conflicting_registry_events"], ["2021-12-31"])
        candidate["events"][0]["dividend_per_share_cny"] = "0.35"
        self.assertEqual(candidate_dividend_coverage(
            {"schema_version": 1, "events": [actual]}, candidate
        )["conflicting_registry_events"], [])

    def test_duplicate_or_noncanonical_candidates_fail_closed(self):
        row = {
            "registration_date": "2021-12-30",
            "ex_dividend_date": "2021-12-31",
            "announced_payment_date": "2022-01-06",
            "dividend_per_share_cny": "0.35",
        }
        candidates = {"schema_version": 1, "symbol": "512690.SH", "events": [row, dict(row)]}
        with self.assertRaisesRegex(ValueError, "Duplicate dividend candidate"):
            candidate_dividend_coverage({"schema_version": 1, "events": [action("CASH_DIVIDEND")]}, candidates)
        candidates["events"] = [{**row, "ex_dividend_date": "20211231"}]
        with self.assertRaisesRegex(ValueError, "Noncanonical candidate date"):
            candidate_dividend_coverage({"schema_version": 1, "events": [action("CASH_DIVIDEND")]}, candidates)



if __name__ == "__main__":
    unittest.main()
