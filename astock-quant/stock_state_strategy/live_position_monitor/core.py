"""Pure strategy rules used by the live position monitor.

This module deliberately contains no networking, email, or broker integration.
It translates the frozen LOCK_MARKUP_SINGLE_PAPER_V1 exit rules into testable
alert events.  Alerts are observations only; they never place an order.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime, time
from statistics import mean
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class Quote:
    provider: str
    symbol: str
    name: str
    timestamp: datetime
    last_price: float
    previous_close: float
    open_price: float
    high_price: float
    low_price: float
    volume: float | None = None
    amount: float | None = None

    @property
    def trade_date(self) -> date:
        return self.timestamp.astimezone(SHANGHAI_TZ).date()

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["timestamp"] = self.timestamp.isoformat()
        return result


@dataclass(frozen=True)
class DailyBar:
    trade_date: date
    open_price: float
    close_price: float
    high_price: float
    low_price: float
    volume: float | None = None
    amount: float | None = None


@dataclass(frozen=True)
class FrozenStop:
    atr_reference_date: date
    reference_close: float
    atr20: float
    atr20_pct: float
    stop_pct: float
    stop_price: float
    source: str

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["atr_reference_date"] = self.atr_reference_date.isoformat()
        return result


@dataclass(frozen=True)
class AlertEvent:
    event_type: str
    severity: str
    observed_at: datetime
    action: str
    summary: str
    details: dict[str, Any]
    dedupe_key: str

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["observed_at"] = self.observed_at.isoformat()
        return result


@dataclass(frozen=True)
class QuoteBundle:
    quotes: tuple[Quote, ...]
    primary: Quote
    cross_source_diff_pct: float | None
    sources_agree: bool

    @property
    def source_count(self) -> int:
        return len(self.quotes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "quotes": [quote.to_dict() for quote in self.quotes],
            "primary_provider": self.primary.provider,
            "cross_source_diff_pct": self.cross_source_diff_pct,
            "sources_agree": self.sources_agree,
        }


def parse_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def position_fingerprint(symbol: str, entry_date: date, entry_price: float) -> str:
    payload = json.dumps(
        {
            "symbol": symbol.upper(),
            "entry_date": entry_date.isoformat(),
            "entry_price": round(float(entry_price), 6),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_positive_price(value: float, field: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{field} must be a positive finite price, got {value!r}")
    return number


def calculate_frozen_stop(
    bars: Sequence[DailyBar],
    entry_date: date,
    entry_price: float,
    *,
    atr_window: int,
    atr_multiplier: float,
    minimum_stop_pct: float,
    maximum_stop_pct: float,
    source: str = "EASTMONEY_QFQ_DAILY_BARS_BEFORE_ENTRY",
) -> FrozenStop:
    """Calculate and freeze the strategy stop from bars strictly before entry.

    The production strategy calculates ATR on adjusted OHLC.  The provider
    supplies forward-adjusted daily bars, and the ratio is frozen immediately
    so later corporate-action recalculation cannot silently move the stop.
    """

    entry_price = validate_positive_price(entry_price, "entry_price")
    eligible = sorted(
        (bar for bar in bars if bar.trade_date < entry_date),
        key=lambda bar: bar.trade_date,
    )
    if len(eligible) < atr_window + 1:
        raise ValueError(
            f"Need at least {atr_window + 1} completed bars before entry, "
            f"got {len(eligible)}"
        )

    true_ranges: list[float] = []
    for previous, current in zip(eligible, eligible[1:]):
        high = validate_positive_price(current.high_price, "daily high")
        low = validate_positive_price(current.low_price, "daily low")
        previous_close = validate_positive_price(
            previous.close_price, "previous daily close"
        )
        if high < low:
            raise ValueError(f"Invalid OHLC on {current.trade_date}: high < low")
        true_ranges.append(
            max(high - low, abs(high - previous_close), abs(low - previous_close))
        )

    atr20 = mean(true_ranges[-atr_window:])
    reference = eligible[-1]
    reference_close = validate_positive_price(reference.close_price, "reference close")
    atr20_pct = atr20 / reference_close
    stop_pct = min(
        maximum_stop_pct,
        max(minimum_stop_pct, atr_multiplier * atr20_pct),
    )
    stop_price = entry_price * (1.0 - stop_pct)
    return FrozenStop(
        atr_reference_date=reference.trade_date,
        reference_close=reference_close,
        atr20=atr20,
        atr20_pct=atr20_pct,
        stop_pct=stop_pct,
        stop_price=stop_price,
        source=source,
    )


def make_fixed_stop(
    entry_date: date,
    entry_price: float,
    stop_pct: float,
    minimum_stop_pct: float,
    maximum_stop_pct: float,
) -> FrozenStop:
    stop_pct = float(stop_pct)
    if not minimum_stop_pct <= stop_pct <= maximum_stop_pct:
        raise ValueError(
            f"fixed_stop_pct must be within [{minimum_stop_pct}, {maximum_stop_pct}]"
        )
    return FrozenStop(
        atr_reference_date=entry_date,
        reference_close=entry_price,
        atr20=math.nan,
        atr20_pct=stop_pct / 1.5,
        stop_pct=stop_pct,
        stop_price=entry_price * (1.0 - stop_pct),
        source="USER_FIXED_STOP_PCT",
    )


def build_quote_bundle(
    quotes: Iterable[Quote], max_cross_source_diff_pct: float
) -> QuoteBundle:
    values = tuple(quotes)
    if not values:
        raise ValueError("No valid quote was available")
    for quote in values:
        validate_positive_price(quote.last_price, f"{quote.provider} last_price")
        if quote.high_price < quote.low_price:
            raise ValueError(f"{quote.provider} returned high below low")

    primary = next(
        (quote for quote in values if quote.provider == "eastmoney"), values[0]
    )
    if len(values) < 2:
        return QuoteBundle(values, primary, None, False)

    prices = [quote.last_price for quote in values]
    denominator = mean(prices)
    difference = (max(prices) - min(prices)) / denominator
    same_trade_date = len({quote.trade_date for quote in values}) == 1
    return QuoteBundle(
        values,
        primary,
        difference,
        same_trade_date and difference <= max_cross_source_diff_pct,
    )


def _quote_crossed_stop(
    quote: Quote,
    stop_price: float,
    entry_date: date,
    entry_day_low_baseline: float | None,
) -> bool:
    if quote.last_price <= stop_price:
        return True
    if quote.trade_date > entry_date:
        return quote.low_price <= stop_price
    if quote.trade_date == entry_date and entry_day_low_baseline is not None:
        # The quote's day-low may pre-date the user's actual fill.  Only regard
        # it as a post-monitor crossing if it moved below the initialization
        # baseline.  Current price below stop always triggers above.
        return (
            quote.low_price < entry_day_low_baseline - 1e-9
            and quote.low_price <= stop_price
        )
    return False


def evaluate_price_stop(
    bundle: QuoteBundle,
    *,
    entry_date: date,
    entry_price: float,
    stop_price: float,
    entry_day_low_baseline: float | None,
    now: datetime,
) -> AlertEvent | None:
    crossed = [
        _quote_crossed_stop(quote, stop_price, entry_date, entry_day_low_baseline)
        for quote in bundle.quotes
    ]
    if not any(crossed):
        return None

    confirmed = bundle.sources_agree and len(crossed) >= 2 and all(crossed)
    quote_date = bundle.primary.trade_date
    on_entry_day = quote_date <= entry_date
    gap_stop = (
        not on_entry_day
        and confirmed
        and all(quote.open_price <= stop_price for quote in bundle.quotes)
    )

    if on_entry_day:
        action = "因A股T+1限制，下一可卖交易日开盘按策略处理"
        rule = "ENTRY_DAY_STOP_TOUCH"
    elif gap_stop:
        action = "可卖日开盘已低于止损价，按开盘止损规则立即人工核对"
        rule = "LATER_GAP_STOP"
    else:
        action = "盘中已触及冻结止损价，立即人工核对可卖状态"
        rule = "LATER_INTRADAY_STOP"

    confirmation = "多源一致确认" if confirmed else "来源不足或多源未一致确认"
    event_type = "PRICE_STOP_CONFIRMED" if confirmed else "PRICE_STOP_UNCONFIRMED"
    details = {
        "rule": rule,
        "confirmation": confirmation,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "current_price": bundle.primary.last_price,
        "pnl_pct_before_cost": bundle.primary.last_price / entry_price - 1.0,
        "providers_crossed": [
            quote.provider for quote, did_cross in zip(bundle.quotes, crossed) if did_cross
        ],
        "source_count": bundle.source_count,
        "cross_source_diff_pct": bundle.cross_source_diff_pct,
    }
    return AlertEvent(
        event_type=event_type,
        severity="CRITICAL" if confirmed else "WARNING",
        observed_at=now,
        action=action,
        summary=f"香山股份触及冻结止损（{confirmation}）",
        details=details,
        dedupe_key=f"{event_type}:{quote_date.isoformat()}:{stop_price:.4f}",
    )


def evaluate_distribution_exit(
    transition_row: dict[str, str] | None,
    *,
    symbol: str,
    entry_date: date,
    now: datetime,
) -> AlertEvent | None:
    if not transition_row:
        return None
    transition = str(transition_row.get("transition", "")).strip().lower()
    signal_date_text = str(transition_row.get("trade_date", "")).strip()
    if transition != "markup_to_distribution" or not signal_date_text:
        return None
    signal_date = parse_date(signal_date_text)
    if signal_date < entry_date:
        return None
    return AlertEvent(
        event_type="MARKUP_TO_DISTRIBUTION_EXIT",
        severity="HIGH",
        observed_at=now,
        action="按策略在信号日后的首个可卖交易日开盘人工退出",
        summary="香山股份出现拉升转派发退出信号",
        details={
            "symbol": symbol,
            "signal_date": signal_date.isoformat(),
            "defined_state": transition_row.get("defined_state"),
            "pool_status": transition_row.get("pool_status"),
            "confidence": transition_row.get("confidence"),
        },
        dedupe_key=f"MARKUP_TO_DISTRIBUTION_EXIT:{symbol}:{signal_date.isoformat()}",
    )


def scheduled_time_exit_date(
    bars: Sequence[DailyBar], entry_date: date, forward_sessions: int
) -> date | None:
    sessions = sorted({bar.trade_date for bar in bars if bar.trade_date > entry_date})
    if len(sessions) < forward_sessions:
        return None
    return sessions[forward_sessions - 1]


def evaluate_time_exit(
    scheduled_date: date | None,
    *,
    symbol: str,
    now: datetime,
    market_close: time,
) -> AlertEvent | None:
    if scheduled_date is None:
        return None
    local_now = now.astimezone(SHANGHAI_TZ)
    if local_now.date() < scheduled_date:
        return None
    if local_now.date() == scheduled_date and local_now.time() < market_close:
        return None
    return AlertEvent(
        event_type="MAX_HOLDING_TIME_EXIT",
        severity="HIGH",
        observed_at=now,
        action="20个后续交易日窗口已结束，按策略在收盘人工退出；不可卖则顺延",
        summary="香山股份达到最长持有期退出条件",
        details={"symbol": symbol, "scheduled_exit_date": scheduled_date.isoformat()},
        dedupe_key=f"MAX_HOLDING_TIME_EXIT:{symbol}:{scheduled_date.isoformat()}",
    )


def choose_strategy_events(
    price_event: AlertEvent | None,
    distribution_event: AlertEvent | None,
    time_event: AlertEvent | None,
) -> list[AlertEvent]:
    """Apply the frozen exit priority while retaining unconfirmed warnings."""

    if price_event and price_event.event_type == "PRICE_STOP_CONFIRMED":
        return [price_event]
    if distribution_event:
        result = [distribution_event]
        if price_event is not None:
            result.insert(0, price_event)
        return result
    if time_event:
        result = [time_event]
        if price_event is not None:
            result.insert(0, price_event)
        return result
    return [price_event] if price_event is not None else []


def parse_clock(value: str) -> time:
    return time.fromisoformat(value)
