"""Deterministic offline adversarial execution tests; not a profitability backtest."""
import unittest
from dataclasses import replace

import pandas as pd

from short_swing_3to5_v1 import Policy, buy_fee, build_signals, is_mainboard, simulate


class Risk:
    def __init__(self, blocked_buy=None, blocked_sell=None, unknown=None):
        self.blocked_buy = blocked_buy or {}
        self.blocked_sell = blocked_sell or {}
        self.unknown = unknown or set()

    def name_status(self, symbol, day):
        return "NAME_UNKNOWN" if symbol in self.unknown else "NORMAL"

    def entry_reason(self, symbol, day, bar):
        return self.blocked_buy.get((symbol, day)) or ("NAME_UNKNOWN" if symbol in self.unknown else None)

    def exit_reason(self, symbol, day, bar):
        return self.blocked_sell.get((symbol, day))


def market(closes=None, symbols=("600001.SH",), sessions=8, factor=1.0):
    days = pd.bdate_range("2026-01-05", periods=sessions)
    rows = []
    for sym in symbols:
        for i, day in enumerate(days):
            cl = closes[i] if closes is not None else 10.0
            rows.append({"trade_date": day, "vendor_symbol": sym, "open": cl,
                         "high": cl * 1.005, "low": cl * 0.995, "close": cl,
                         "volume_shares": 20_000_000, "turnover_cny": 200_000_000,
                         "adjustment_factor": factor, "is_quality_valid": True})
    return pd.DataFrame(rows), days


def setbar(panel, symbol, day, op, hi=None, lo=None, close=None):
    m = panel.vendor_symbol.eq(symbol) & panel.trade_date.eq(day)
    panel.loc[m, "open"] = op
    panel.loc[m, "high"] = op if hi is None else hi
    panel.loc[m, "low"] = op if lo is None else lo
    panel.loc[m, "close"] = op if close is None else close


class Contract(unittest.TestCase):
    def setUp(self):
        self.p = replace(Policy(), min_breadth_symbols=1, min_breadth_fraction=0)

    def test_ticker_scope_fixed_amount_and_costs(self):
        for ticker in ("600000.SH", "601001.SH", "603999.SH", "605000.SH",
                       "000001.SZ", "001287.SZ", "002001.SZ", "003000.SZ"):
            self.assertTrue(is_mainboard(ticker))
        for ticker in ("300001.SZ", "688001.SH", "920001.BJ", "200001.SZ", "900001.SH"):
            self.assertFalse(is_mainboard(ticker))
        with self.assertRaises(ValueError):
            replace(self.p, order_budget_cny=60000).validate()
        self.assertGreaterEqual(buy_fee(40000, self.p), 5)

    def test_no_entry_at_signal_close_lots_one_stock_and_time_exit(self):
        panel, dates = market(symbols=("600001.SH", "000001.SZ"))
        sig = {dates[0]: [("600001.SH", 2), ("000001.SZ", 1)],
               dates[1]: [("000001.SZ", 10)], dates[2]: [("000001.SZ", 10)]}
        r = simulate(panel, Risk(), self.p, sig)
        self.assertEqual(len([o for o in r["orders"] if o["side"] == "BUY_ALL"]), 1)
        self.assertEqual(r["orders"][0]["date"], str(dates[1].date()))
        self.assertEqual(r["orders"][0]["shares"] % 100, 0)
        self.assertGreaterEqual(r["orders"][0]["notional_cny"], 30000)
        self.assertLessEqual(r["orders"][0]["notional_cny"], 50000)
        self.assertEqual(r["trades"][0]["holding_sessions"], 5)
        self.assertEqual(r["trades"][0]["exit_reason"], "TIME_5_CLOSE")
        self.assertEqual(r["orders"][-1]["side"], "SELL_ALL")
        self.assertEqual(r["open_position"], None)
        self.assertLess(r["summary"]["total_return"], 0)

    def test_entry_day_stop_cannot_exit_till_next_session(self):
        panel, d = market()
        setbar(panel, "600001.SH", d[1], 10, hi=10, lo=9.5, close=9.7)
        r = simulate(panel, Risk(), self.p, {d[0]: [("600001.SH", 1)]})
        self.assertEqual(r["trades"][0]["exit_date"], str(d[2].date()))
        self.assertEqual(r["trades"][0]["holding_sessions"], 2)
        self.assertEqual(r["trades"][0]["exit_reason"], "DEFERRED_STOP_OR_TIME")

    def test_intraday_stop_wins_before_profit_target(self):
        panel, d = market()
        setbar(panel, "600001.SH", d[3], 10, hi=11, lo=9, close=10)
        r = simulate(panel, Risk(), self.p, {d[0]: [("600001.SH", 1)]})
        self.assertEqual(r["trades"][0]["exit_reason"], "STOP_INTRADAY_ASSUMED")
        self.assertEqual(r["trades"][0]["holding_sessions"], 3)

    def test_early_target_not_taken_before_third_day(self):
        panel, d = market(sessions=7)
        setbar(panel, "600001.SH", d[2], 10, hi=11, lo=10, close=10)
        setbar(panel, "600001.SH", d[3], 10, hi=11, lo=10, close=10)
        r = simulate(panel, Risk(), self.p, {d[0]: [("600001.SH", 1)]})
        self.assertEqual(r["trades"][0]["exit_date"], str(d[3].date()))
        self.assertEqual(r["trades"][0]["exit_reason"], "TARGET_INTRADAY_ASSUMED")

    def test_limit_down_blocks_fifth_day_then_next_open_exits(self):
        panel, d = market(sessions=8)
        blocked = {("600001.SH", d[5]): "EXIT_LOCKED_LIMIT_DOWN"}
        r = simulate(panel, Risk(blocked_sell=blocked), self.p, {d[0]: [("600001.SH", 1)]})
        self.assertEqual(r["trades"][0]["holding_sessions"], 6)
        self.assertEqual(r["trades"][0]["exit_reason"], "DEFERRED_STOP_OR_TIME")
        self.assertTrue(any(e["reason"] == "EXIT_LOCKED_LIMIT_DOWN" for e in r["exceptions"]))

    def test_unknown_pit_blocks_and_no_partial_or_sub_30k(self):
        panel, d = market(symbols=("600001.SH", "000001.SZ"))
        risk = Risk(blocked_buy={("600001.SH", d[1]): "LIMIT_UNKNOWN"}, unknown={"000001.SZ"})
        r = simulate(panel, risk, self.p, {d[0]: [("600001.SH", 2), ("000001.SZ", 1)]})
        self.assertEqual(len(r["orders"]), 0)
        self.assertEqual(r["summary"]["closed_trades"], 0)
        panel["open"] = 600.0
        panel["high"] = 600.0
        panel["low"] = 600.0
        panel["close"] = 600.0
        r = simulate(panel, Risk(), self.p, {d[0]: [("600001.SH", 1)]})
        self.assertFalse(r["orders"])

    def test_outstanding_position_does_not_force_end_of_data_sale(self):
        panel, d = market(sessions=3)
        r = simulate(panel, Risk(), self.p, {d[0]: [("600001.SH", 1)]})
        self.assertEqual(len(r["orders"]), 1)
        self.assertIsNotNone(r["open_position"])
        self.assertFalse(r["trades"])

    def test_signal_no_future_high_leakage_and_unknown_st(self):
        panel, d = market(sessions=65)
        panel["volume_shares"] = 20_000_000
        panel["close"] = [10 + i * 0.03 for i in range(65)]
        panel["open"] = panel.close - 0.03
        panel["high"] = panel.close + 0.04
        panel["low"] = panel.open - 0.04
        panel.loc[panel.trade_date.eq(d[64]), "volume_shares"] = 40_000_000
        signals = build_signals(panel, self.p, Risk())
        self.assertFalse(signals.get(d[63]))
        self.assertTrue(signals.get(d[64]))
        self.assertFalse(build_signals(panel, self.p, Risk(unknown={"600001.SH"})).get(d[64]))


if __name__ == "__main__":
    unittest.main()
