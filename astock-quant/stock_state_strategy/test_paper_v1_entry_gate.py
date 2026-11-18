"""Synthetic-only contract tests: frozen V1 is never live trading permission."""
import unittest

from paper_v1_entry_gate import check_next_open, select_close_candidate, PAPER_ONLY


def candidate():
    return {
        "signal_date": "2026-11-02", "trade_date": "2026-11-02",
        "vendor_symbol": "600400.SH", "instrument_id": "CN.XSHG.600400",
        "defined_state": "markup", "pool_status": "A_STRATEGY_CANDIDATE",
        "previous_defined_state": "lock", "transition": "lock_to_markup",
        "transition_gap_days": "8", "state_score": "79.2",
        "state_margin": "11", "score_coverage": "1",
        "stable_days_5": "4", "confidence": "87.2",
        "average_turnover_cny_20": "168417196",
        "decision": "SHADOW_SIGNAL_NO_LIVE_AUTHORIZATION",
        "estimated_pl_ratio": "0.78", "net_r_percentile": "0.28",
        "atr20_pct": "0.04", "stop_pct": "0.06", "close": "10.00",
    }


def account():
    return {"flat": True, "pending_other_order": False, "equity": 100000, "cash": 100000}


def next_bar():
    return {
        "verified": True, "trade_date": "2026-11-03",
        "vendor_symbol": "600400.SH", "paused": False,
        "historical_is_st": False, "buyable": True,
        "one_word_up": False, "open": 10.15,
    }


class FrozenEntryGateTests(unittest.TestCase):
    def plan(self, rows=None, **kwargs):
        return select_close_candidate("2026-11-02", [candidate()] if rows is None else rows,
                                      verified_source=kwargs.get("verified_source", True),
                                      flat_account=kwargs.get("flat_account", True))

    def adjudicate(self, plan=None, bar=None, acc=None, **kwargs):
        return check_next_open(plan if plan is not None else self.plan(),
                               bar if bar is not None else next_bar(),
                               acc if acc is not None else account(),
                               expected_next_session=kwargs.get("expected_next_session", "2026-11-03"),
                               verified_session_and_bar=kwargs.get("verified_session_and_bar", True))

    def test_accepted_t_close_is_pending_not_a_buy(self):
        got = self.plan()
        self.assertEqual(got["status"], "PENDING_NEXT_OPEN")
        self.assertEqual(got["authorization"], PAPER_ONLY)

    def test_unverified_input_fails_closed(self):
        self.assertEqual(self.plan(verified_source=False)["status"], "BLOCKED")

    def test_lock_only_cannot_trade(self):
        row = candidate()
        row["defined_state"] = "lock"
        self.assertEqual(self.plan([row])["status"], "NO_ENTRY")

    def test_missing_rank_fields_fail_closed(self):
        row = candidate()
        row["estimated_pl_ratio"] = ""
        self.assertEqual(self.plan([row])["status"], "NO_ENTRY")

    def test_future_signal_date_cannot_be_relabelled_today(self):
        row = candidate()
        row["signal_date"] = "2026-11-03"
        self.assertEqual(self.plan([row])["status"], "NO_ENTRY")

    def test_frozen_stop_mismatch_rejected(self):
        row = candidate()
        row["stop_pct"] = "0.08"
        self.assertEqual(self.plan([row])["status"], "NO_ENTRY")

    def test_frozen_ranking_selects_highest_pl_not_state_score(self):
        first, second = candidate(), candidate()
        first["vendor_symbol"], first["instrument_id"] = "600400.SH", "CN.XSHG.600400"
        second["vendor_symbol"], second["instrument_id"] = "603580.SH", "CN.XSHG.603580"
        first["state_score"] = "92"
        second["state_score"] = "67"
        second["estimated_pl_ratio"] = "0.81"
        self.assertEqual(self.plan([first, second])["symbol"], "603580.SH")

    def test_pending_account_blocks_new_signal(self):
        self.assertEqual(self.plan(flat_account=False)["status"], "NO_ENTRY")

    def test_verified_next_open_simulates_one_lot_rounded_buy(self):
        got = self.adjudicate()
        self.assertEqual(got["status"], "PAPER_BUY_CONFIRMED")
        self.assertEqual(got["authorization"], PAPER_ONLY)
        self.assertEqual(got["shares"] % 100, 0)
        self.assertLessEqual(got["position_weight"], 0.15)
        self.assertLessEqual(got["shares"] * got["entry_price"] * got["stop_pct"], 500 + 1e-9)

    def test_gap_over_three_percent_cancels(self):
        bar = next_bar()
        bar["open"] = 10.31
        self.assertEqual(self.adjudicate(bar=bar)["reason"], "OPENING_GAP_ABOVE_3_PERCENT")

    def test_one_word_limit_up_cancels(self):
        bar = next_bar()
        bar["one_word_up"] = True
        self.assertEqual(self.adjudicate(bar=bar)["status"], "CANCELLED")

    def test_paused_or_unknown_st_cancels(self):
        bar = next_bar()
        bar["paused"] = True
        self.assertEqual(self.adjudicate(bar=bar)["status"], "CANCELLED")
        bar = next_bar()
        del bar["historical_is_st"]
        self.assertEqual(self.adjudicate(bar=bar)["status"], "CANCELLED")

    def test_missing_verified_session_cancels(self):
        self.assertEqual(self.adjudicate(verified_session_and_bar=False)["status"], "CANCELLED")

    def test_wrong_next_trading_date_cancels(self):
        self.assertEqual(self.adjudicate(expected_next_session="2026-11-04")["status"], "CANCELLED")

    def test_missing_buyability_cancels(self):
        bar = next_bar()
        del bar["buyable"]
        self.assertEqual(self.adjudicate(bar=bar)["status"], "CANCELLED")

    def test_account_position_or_pending_order_cancels(self):
        a = account()
        a["pending_other_order"] = True
        self.assertEqual(self.adjudicate(acc=a)["status"], "CANCELLED")

    def test_too_little_cash_for_board_lot_cancels(self):
        a = account()
        a["cash"] = 10
        self.assertEqual(self.adjudicate(acc=a)["reason"], "LESS_THAN_ONE_BOARD_LOT")

    def test_no_reuse_of_t_close_plan_as_open_bar(self):
        self.assertEqual(self.adjudicate(bar={})["status"], "CANCELLED")


if __name__ == "__main__":
    unittest.main()
