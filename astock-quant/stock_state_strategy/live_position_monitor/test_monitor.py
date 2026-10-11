from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import date, datetime, time, timedelta

from live_position_monitor.core import (
    DailyBar,
    Quote,
    SHANGHAI_TZ,
    build_quote_bundle,
    calculate_frozen_stop,
    choose_strategy_events,
    evaluate_distribution_exit,
    evaluate_price_stop,
    evaluate_time_exit,
    scheduled_time_exit_date,
)
from live_position_monitor.providers import (
    parse_eastmoney_quote,
    parse_sina_quote,
    parse_tencent_quote,
)
from live_position_monitor.monitor import load_env_file


def quote(
    provider: str,
    *,
    trade_date: date,
    price: float,
    open_price: float | None = None,
    low_price: float | None = None,
) -> Quote:
    return Quote(
        provider=provider,
        symbol="002870.SZ",
        name="香山股份",
        timestamp=datetime.combine(trade_date, time(10, 0), SHANGHAI_TZ),
        last_price=price,
        previous_close=49.0,
        open_price=price if open_price is None else open_price,
        high_price=max(price, 49.0),
        low_price=price if low_price is None else low_price,
        volume=100.0,
        amount=10000.0,
    )


class FrozenStopTests(unittest.TestCase):
    def test_atr_stop_uses_twenty_ranges_and_six_percent(self) -> None:
        bars = []
        start = date(2026, 7, 1)
        for index in range(25):
            bars.append(
                DailyBar(
                    trade_date=start + timedelta(days=index),
                    open_price=100.0,
                    close_price=100.0,
                    high_price=102.0,
                    low_price=98.0,
                )
            )
        result = calculate_frozen_stop(
            bars,
            date(2026, 8, 1),
            49.0,
            atr_window=20,
            atr_multiplier=1.5,
            minimum_stop_pct=0.04,
            maximum_stop_pct=0.08,
        )
        self.assertAlmostEqual(result.atr20_pct, 0.04)
        self.assertAlmostEqual(result.stop_pct, 0.06)
        self.assertAlmostEqual(result.stop_price, 46.06)

    def test_atr_stop_applies_four_percent_floor(self) -> None:
        bars = []
        start = date(2026, 7, 1)
        for index in range(25):
            bars.append(
                DailyBar(
                    trade_date=start + timedelta(days=index),
                    open_price=100.0,
                    close_price=100.0,
                    high_price=100.5,
                    low_price=99.5,
                )
            )
        result = calculate_frozen_stop(
            bars,
            date(2026, 8, 1),
            49.0,
            atr_window=20,
            atr_multiplier=1.5,
            minimum_stop_pct=0.04,
            maximum_stop_pct=0.08,
        )
        self.assertAlmostEqual(result.stop_pct, 0.04)


class ProviderParserTests(unittest.TestCase):
    def test_parse_eastmoney_quote(self) -> None:
        payload = json.dumps(
            {
                "rc": 0,
                "data": {
                    "f43": 4796,
                    "f44": 4899,
                    "f45": 4781,
                    "f46": 4819,
                    "f47": 5320,
                    "f48": 25652477,
                    "f57": "002870",
                    "f58": "香山股份",
                    "f60": 4820,
                    "f86": 1787535789,
                },
            },
            ensure_ascii=False,
        )
        result = parse_eastmoney_quote(payload, "002870.SZ")
        self.assertEqual(result.provider, "eastmoney")
        self.assertEqual(result.name, "香山股份")
        self.assertAlmostEqual(result.last_price, 47.96)
        self.assertAlmostEqual(result.low_price, 47.81)

    def test_parse_tencent_quote(self) -> None:
        fields = [""] * 38
        fields[0] = "51"
        fields[1] = "香山股份"
        fields[2] = "002870"
        fields[3] = "47.96"
        fields[4] = "48.20"
        fields[5] = "48.19"
        fields[6] = "5320"
        fields[30] = "20260824094312"
        fields[33] = "48.99"
        fields[34] = "47.81"
        fields[37] = "2565.2477"
        payload = f'v_sz002870="{"~".join(fields)}";'.encode("gbk")
        result = parse_tencent_quote(payload, "SZ:002870")
        self.assertEqual(result.provider, "tencent")
        self.assertAlmostEqual(result.last_price, 47.96)
        self.assertEqual(result.trade_date, date(2026, 8, 24))


class EnvironmentFileTests(unittest.TestCase):
    def test_load_env_file_without_overriding_existing_value(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = __import__("pathlib").Path(directory) / ".env"
            path.write_text(
                "# comment\nTEST_MONITOR_ALPHA=one\nTEST_MONITOR_BETA='two'\n",
                encoding="utf-8",
            )
            old_alpha = os.environ.get("TEST_MONITOR_ALPHA")
            old_beta = os.environ.get("TEST_MONITOR_BETA")
            try:
                os.environ["TEST_MONITOR_ALPHA"] = "existing"
                loaded = load_env_file(path)
                self.assertEqual(os.environ["TEST_MONITOR_ALPHA"], "existing")
                self.assertEqual(os.environ["TEST_MONITOR_BETA"], "two")
                self.assertEqual(loaded, ["TEST_MONITOR_ALPHA", "TEST_MONITOR_BETA"])
            finally:
                if old_alpha is None:
                    os.environ.pop("TEST_MONITOR_ALPHA", None)
                else:
                    os.environ["TEST_MONITOR_ALPHA"] = old_alpha
                if old_beta is None:
                    os.environ.pop("TEST_MONITOR_BETA", None)
                else:
                    os.environ["TEST_MONITOR_BETA"] = old_beta

    def test_parse_sina_quote(self) -> None:
        fields = [""] * 33
        fields[0] = "香山股份"
        fields[1] = "48.19"
        fields[2] = "48.20"
        fields[3] = "48.12"
        fields[4] = "48.99"
        fields[5] = "47.81"
        fields[8] = "782700"
        fields[9] = "37744721"
        fields[30] = "2026-08-24"
        fields[31] = "09:59:09"
        fields[32] = "00"
        payload = f'var hq_str_sz002870="{",".join(fields)}";'.encode("gbk")
        result = parse_sina_quote(payload, "002870.SZ")
        self.assertEqual(result.provider, "sina")
        self.assertAlmostEqual(result.last_price, 48.12)
        self.assertEqual(result.trade_date, date(2026, 8, 24))


class ExitRuleTests(unittest.TestCase):
    def test_two_source_later_day_stop_is_confirmed(self) -> None:
        quotes = [
            quote("eastmoney", trade_date=date(2026, 8, 25), price=45.8),
            quote("tencent", trade_date=date(2026, 8, 25), price=45.8),
        ]
        bundle = build_quote_bundle(quotes, 0.003)
        event = evaluate_price_stop(
            bundle,
            entry_date=date(2026, 8, 24),
            entry_price=49.0,
            stop_price=46.0,
            entry_day_low_baseline=47.8,
            now=datetime(2026, 8, 25, 10, 0, tzinfo=SHANGHAI_TZ),
        )
        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, "PRICE_STOP_CONFIRMED")
        self.assertEqual(event.details["rule"], "LATER_GAP_STOP")

    def test_entry_day_old_low_does_not_false_trigger(self) -> None:
        quotes = [
            quote(
                "eastmoney",
                trade_date=date(2026, 8, 24),
                price=47.5,
                low_price=45.5,
            ),
            quote(
                "tencent",
                trade_date=date(2026, 8, 24),
                price=47.5,
                low_price=45.5,
            ),
        ]
        event = evaluate_price_stop(
            build_quote_bundle(quotes, 0.003),
            entry_date=date(2026, 8, 24),
            entry_price=49.0,
            stop_price=46.0,
            entry_day_low_baseline=45.5,
            now=datetime(2026, 8, 24, 10, 0, tzinfo=SHANGHAI_TZ),
        )
        self.assertIsNone(event)

    def test_entry_day_current_price_stop_defers_for_t_plus_one(self) -> None:
        quotes = [
            quote("eastmoney", trade_date=date(2026, 8, 24), price=45.8),
            quote("tencent", trade_date=date(2026, 8, 24), price=45.8),
        ]
        event = evaluate_price_stop(
            build_quote_bundle(quotes, 0.003),
            entry_date=date(2026, 8, 24),
            entry_price=49.0,
            stop_price=46.0,
            entry_day_low_baseline=47.8,
            now=datetime(2026, 8, 24, 10, 0, tzinfo=SHANGHAI_TZ),
        )
        self.assertIsNotNone(event)
        self.assertEqual(event.details["rule"], "ENTRY_DAY_STOP_TOUCH")

    def test_distribution_exit_only_after_entry(self) -> None:
        now = datetime(2026, 8, 26, 18, 0, tzinfo=SHANGHAI_TZ)
        event = evaluate_distribution_exit(
            {
                "trade_date": "2026-08-26",
                "transition": "markup_to_distribution",
                "defined_state": "distribution",
            },
            symbol="002870.SZ",
            entry_date=date(2026, 8, 24),
            now=now,
        )
        self.assertIsNotNone(event)
        old = evaluate_distribution_exit(
            {"trade_date": "2026-08-18", "transition": "markup_to_distribution"},
            symbol="002870.SZ",
            entry_date=date(2026, 8, 24),
            now=now,
        )
        self.assertIsNone(old)

    def test_twentieth_forward_session_time_exit(self) -> None:
        entry = date(2026, 8, 1)
        bars = [
            DailyBar(
                trade_date=entry + timedelta(days=index),
                open_price=10,
                close_price=10,
                high_price=11,
                low_price=9,
            )
            for index in range(1, 22)
        ]
        scheduled = scheduled_time_exit_date(bars, entry, 20)
        self.assertEqual(scheduled, entry + timedelta(days=20))
        before_close = evaluate_time_exit(
            scheduled,
            symbol="002870.SZ",
            now=datetime.combine(scheduled, time(14, 59), SHANGHAI_TZ),
            market_close=time(15, 0),
        )
        self.assertIsNone(before_close)
        after_close = evaluate_time_exit(
            scheduled,
            symbol="002870.SZ",
            now=datetime.combine(scheduled, time(15, 1), SHANGHAI_TZ),
            market_close=time(15, 0),
        )
        self.assertIsNotNone(after_close)

    def test_confirmed_stop_has_priority(self) -> None:
        now = datetime(2026, 8, 25, 10, 0, tzinfo=SHANGHAI_TZ)
        stop = evaluate_price_stop(
            build_quote_bundle(
                [
                    quote("eastmoney", trade_date=now.date(), price=45.0),
                    quote("tencent", trade_date=now.date(), price=45.0),
                ],
                0.003,
            ),
            entry_date=date(2026, 8, 24),
            entry_price=49,
            stop_price=46,
            entry_day_low_baseline=48,
            now=now,
        )
        distribution = evaluate_distribution_exit(
            {"trade_date": "2026-08-25", "transition": "markup_to_distribution"},
            symbol="002870.SZ",
            entry_date=date(2026, 8, 24),
            now=now,
        )
        selected = choose_strategy_events(stop, distribution, None)
        self.assertEqual([item.event_type for item in selected], ["PRICE_STOP_CONFIRMED"])


if __name__ == "__main__":
    unittest.main()
