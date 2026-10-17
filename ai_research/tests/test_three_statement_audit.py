import unittest
from ai_research.three_statement_audit import audit

class ThreeStatementAuditTest(unittest.TestCase):
    def test_missing_statement_fails(self):
        r = audit([{"ts_code":"600351.SH","dataset":"income","report_period":"2026-06-30",
                    "ann_date":"2026-08-20","available_date":"2026-08-20"}],
                  ["600351.SH"],["2026-06-30"],"2026-10-17")
        self.assertEqual(r["status"],"FAIL_CLOSED")
        self.assertEqual(r["symbols"]["600351.SH"]["2026-06-30"]["cashflow"],"MISSING")

    def test_future_disclosure_is_rejected(self):
        rows = [{"ts_code":"600351.SH","dataset":ds,"report_period":"2026-06-30",
                 "ann_date":"2026-10-17"} for ds in ("income","balancesheet","cashflow")]
        r = audit(rows,["600351.SH"],["2026-06-30"],"2026-10-17")
        self.assertEqual(r["status"],"FAIL_CLOSED")
        self.assertEqual(len(r["rejected_rows"]),3)

    def test_complete_prior_disclosures_pass(self):
        rows = [{"ts_code":"600351.SH","dataset":ds,"report_period":"2026-06-30",
                 "ann_date":"2026-08-20"} for ds in ("income","balancesheet","cashflow")]
        r = audit(rows,["600351.SH"],["2026-06-30"],"2026-10-17")
        self.assertEqual(r["status"],"PASS")
        self.assertEqual(r["accounting_reconciliation"],"NOT_TESTED")

if __name__ == "__main__":
    unittest.main()
