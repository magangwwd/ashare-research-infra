"""No-network tests for A/B forward trade-proxy metrics and PIT guards."""
import unittest
from ai_research.five_stock_ab_evaluate import evaluate
from ai_research.manual_ai_review_pack import SYMBOLS, AUTHORIZATION


def base_report(candidate=False):
    return {
        "version": "FIVE_STOCK_DECISION_V1", "authorization": AUTHORIZATION,
        "as_of": "2026-10-16",
        "stocks": [{"symbol": sym, "r1_candidate": candidate and i == 0,
                    "r1_estimated_2atr_fraction": .03 if candidate and i == 0 else None,
                    "research_status": "SETUP_PAPER_NEXT_OPEN_UNVERIFIED" if candidate and i == 0 else "WATCH"}
                   for i, sym in enumerate(SYMBOLS)]
    }


def fill(exit_price=12.0):
    return {"records": [{"signal_date": "2026-10-16", "symbol": SYMBOLS[0],
                         "entry_date": "2026-10-19", "exit_date": "2026-10-23",
                         "entry_raw_cny": 10.0, "exit_raw_cny": exit_price,
                         "same_adjustment_basis": True, "entry_quality_valid": True,
                         "exit_quality_valid": True, "entry_risk_pass": True,
                         "entry_order_book_liquidity_confirmed": True,
                         "paid_gap_including_slippage": 0.005,
                         "source_sha256": "c"*64}]}


class PaperComparisonTests(unittest.TestCase):
    def test_zero_candidate_is_not_zero_percent_strategy_return(self):
        r = evaluate([base_report()], {"records": []})
        self.assertEqual(r["status"], "NO_ORIGINAL_R1_CANDIDATES")
        self.assertIsNone(r["a_r1"]["mean_net_trade_return"])
        self.assertIsNone(r["b_r1_plus_review"]["win_rate"])

    def test_future_observed_same_fill_ab(self):
        out = evaluate([base_report(True)], fill(12))
        self.assertEqual(out["status"], "OBSERVED_PAPER_PROXY_ONLY")
        self.assertEqual(out["a_r1"], out["b_r1_plus_review"])
        self.assertGreater(out["a_r1"]["mean_net_trade_return"], 0)
        self.assertEqual(out["a_r1"]["completed"], 1)

    def test_b_veto_not_retroactively_changing_a(self):
        r = base_report(True)
        r["stocks"][0]["research_status"] = "AVOID_EXIT_REVIEW"
        out = evaluate([r], fill(9.0))
        self.assertEqual(out["a_r1"]["completed"], 1)
        self.assertEqual(out["b_r1_plus_review"]["completed"], 0)
        self.assertEqual(out["blocked_losing_events"], 1)

    def test_no_future_fill_cannot_claim_advantage(self):
        r = evaluate([base_report(True)], {"records": []})
        self.assertEqual(r["status"], "INCOMPLETE_DO_NOT_CLAIM_OUTPERFORMANCE")
        self.assertEqual(len(r["pending"]), 1)

    def test_forged_same_day_execution_is_rejected(self):
        obs = fill()
        obs["records"][0]["entry_date"] = "2026-10-16"
        out = evaluate([base_report(True)], obs)
        self.assertEqual(len(out["rejected"]), 1)

    def test_wrong_symbol_observation_is_rejected(self):
        obs = fill()
        obs["records"][0]["symbol"] = SYMBOLS[1]
        with self.assertRaisesRegex(ValueError, "without"):
            evaluate([base_report(True)], obs)


if __name__ == "__main__":
    unittest.main()
