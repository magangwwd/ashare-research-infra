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

    def test_cash_dividend_missing_exact_ex_date_quote_flags_review(self):
        # Synthetic only: next available quote is 2022-01-03, not the
        # dividend ex-date 2021-12-31. Matching arithmetic is insufficient.
        registry = {"schema_version": 1, "events": [action("CASH_DIVIDEND")]}
        missing = analyze(
            registry,
            quotes(2, 1.65, 1, 2 / 1.65, "2021-12-30", "2022-01-03"),
        )[0]
        self.assertEqual(missing["quote_after"], "2022-01-03")
        self.assertTrue(missing["ex_date_quote_missing"])
        self.assertTrue(missing["needs_manual_review"])
        self.assertEqual(missing["status"], "MISSING_EX_DATE_QUOTE_REVIEW_REQUIRED")
        self.assertAlmostEqual(missing["fund_adj_minus_event_return_bp"], 0)

        exact = analyze(
            registry,
            quotes(2, 1.65, 1, 2 / 1.65, "2021-12-30", "2021-12-31"),
        )[0]
        self.assertFalse(exact["ex_date_quote_missing"])
        self.assertFalse(exact["needs_manual_review"])
        self.assertEqual(exact["status"], "DIAGNOSTIC_ONLY_NOT_TOTAL_RETURN_CERTIFICATION")

    def test_cash_dividend_requires_genuine_quotes_on_both_sides(self):
        # Missing the pre-event or all post-event real observations must fail
        # closed, not emit a fabricated dividend/factor comparison.
        registry = {"schema_version": 1, "events": [action("CASH_DIVIDEND")]}
        before_only = quotes(
            2, 1.9, 1, 1, "2021-12-29", "2021-12-30"
        )
        after_only = quotes(
            1.65, 1.7, 1, 1, "2021-12-31", "2022-01-03"
        )
        for label, rows in (("no_post_event_quote", before_only),
                            ("no_pre_event_quote", after_only)):
            with self.subTest(case=label):
                with self.assertRaisesRegex(ValueError, "No genuine quotes"):
                    analyze(registry, rows)

    def test_non_dividend_suspension_quote_gap_does_not_claim_cash_issue(self):
        # Share-consolidation event dates can coincide with a halt; this
        # new indicator is specifically for CASH_DIVIDEND diagnostics.
        row = analyze(
            {"schema_version": 1, "events": [action("UNIT_SPLIT")]},
            quotes(2, 1, 1, 2, "2021-05-13", "2021-05-17"),
        )[0]
        self.assertFalse(row["ex_date_quote_missing"])

    def test_same_day_split_and_dividend_fail_closed(self):
        # Synthetic case only: a split and a cash dividend on the same ETF
        # ex-date cannot be evaluated as independent one-event price jumps.
        split = action("UNIT_SPLIT")
        dividend = action("CASH_DIVIDEND")
        dividend["event_date"] = split["event_date"]
        dividend["registration_date"] = "2021-05-13"
        dividend["cash_payment_date"] = "2021-05-20"
        with self.assertRaisesRegex(ValueError, "Multiple corporate-action types"):
            validate_events({"schema_version": 1, "events": [split, dividend]})

        # Separate event dates remain admissible; this does not erase events.
        self.assertEqual(len(validate_events({
            "schema_version": 1, "events": [split, action("CASH_DIVIDEND")]
        })), 2)

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

    def test_candidate_coverage_flags_registry_events_missing_from_candidates(self):
        # A stale secondary list must not silently certify a registered event.
        first = action("CASH_DIVIDEND")
        second = {**first, "event_date": "2022-12-30",
                  "registration_date": "2022-12-29",
                  "cash_payment_date": "2023-01-05"}
        candidate = {"schema_version": 1, "symbol": "512690.SH", "events": [{
            "registration_date": "2021-12-30",
            "ex_dividend_date": "2021-12-31",
            "announced_payment_date": "2022-01-06",
            "dividend_per_share_cny": "0.35",
        }]}
        result = candidate_dividend_coverage(
            {"schema_version": 1, "events": [first, second]}, candidate
        )
        self.assertEqual(result["missing_from_executable_registry"], [])
        self.assertEqual(result["registry_events_absent_from_candidate_list"], ["2022-12-30"])
        self.assertTrue(result["coverage_discrepancy"])
        later = candidate_dividend_coverage(
            {"schema_version": 1, "events": [first, second]},
            candidate, start_date="2023-01-01"
        )
        self.assertEqual(later["registry_events_absent_from_candidate_list"], [])
        self.assertFalse(later["coverage_discrepancy"])

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


    def test_primary_sse_2022_notice_matches_candidate_without_claiming_cash_verification(self):
        # Research-only provenance crosscheck: inspected exchange-hosted PDF
        # text/images, NOT raw PDF bytes, market prices or brokerage cash.
        from decimal import Decimal
        from urllib.parse import urlparse

        metadata = Path(__file__).resolve().parents[1] / "metadata"
        source = json.loads((metadata /
            "510300_20220112_primary_sse_issuer_notice_crosscheck_20261025.json"
        ).read_text(encoding="utf-8"))
        candidates = json.loads((metadata /
            "510300_dividend_coverage_gap_20261024.json"
        ).read_text(encoding="utf-8"))
        event = source["issuer_notice_event"]
        matching = [row for row in candidates["events"]
                    if row["ex_dividend_date"] == event["ex_dividend_date"]]
        self.assertEqual(len(matching), 1)
        candidate = matching[0]
        self.assertEqual(source["symbol"], "510300.SH")
        self.assertEqual(source["task_id"], "ETF-DIV-004")
        self.assertEqual(urlparse(source["primary_source"]["url"]).hostname, "www.sse.com.cn")
        self.assertEqual(event["record_date"], candidate["registration_date"])
        self.assertEqual(event["announced_payment_date"], candidate["announced_payment_date"])
        self.assertEqual(Decimal(event["dividend_per_share_cny"]),
                         Decimal(candidate["dividend_per_share_cny"]))
        self.assertEqual(Decimal(event["dividend_per_10_shares_cny"]),
                         Decimal(event["dividend_per_share_cny"]) * 10)
        self.assertFalse(source["primary_source"]["pdf_bytes_downloaded"])
        self.assertIsNone(source["primary_source"]["pdf_sha256"])
        self.assertIsNone(event["actual_investor_spendable_cash_date"])
        self.assertEqual(source["status"], "REVIEW_REQUIRED")
        self.assertEqual(source["trading_status"], "NOT_FOR_TRADING")



if __name__ == "__main__":
    unittest.main()
