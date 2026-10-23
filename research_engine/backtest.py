"""Small, explicit long-only event backtester for engineering validation."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# Raw A-share prices are quoted in 0.01 CNY ticks; half a tick separates
# "at the limit" from "one tick inside the limit" without float equality.
LIMIT_TOLERANCE = 0.005


@dataclass(frozen=True)
class BacktestConfig:
    initial_cash: float = 1_000_000.0
    commission_bps: float = 0.0
    slippage_bps: float = 0.0
    sell_tax_bps: float = 0.0
    # Fraction of the last known mark recovered when a held security delists.
    # 0.0 writes the position off completely (conservative).
    delisting_recovery_ratio: float = 0.0
    # Applies only when market carries raw open/up_limit/down_limit columns.
    enforce_price_limits: bool = True

    def validate(self) -> None:
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        if min(self.commission_bps, self.slippage_bps, self.sell_tax_bps) < 0:
            raise ValueError("cost rates cannot be negative")
        if not 0.0 <= self.delisting_recovery_ratio <= 1.0:
            raise ValueError("delisting_recovery_ratio must be within [0, 1]")


@dataclass
class BacktestResult:
    nav: pd.DataFrame
    trades: pd.DataFrame


def _next_session_map(dates: list) -> dict:
    return {dates[i]: dates[i + 1] for i in range(len(dates) - 1)}


def _as_date(value):
    """Normalize date / datetime / Timestamp values so they compare safely."""
    return pd.Timestamp(value).date()


def run_backtest(
    market: pd.DataFrame,
    targets: pd.DataFrame,
    config: BacktestConfig | None = None,
    delistings: pd.DataFrame | None = None,
) -> BacktestResult:
    """Execute close-generated target weights at the next session's adjusted open.

    Assumptions for this engineering version:
    - long-only, no leverage, fractional shares;
    - target weights are generated after signal_date close;
    - execution occurs at the next global session open, never on signal_date;
    - a missing open means that security is not tradable that session;
    - an unquoted held security is carried at its last known adjusted close,
      unless ``delistings`` (columns security_id, delist_date) says it delisted:
      on the first session on or after delist_date with no open, the position is
      removed and ``delisting_recovery_ratio`` of its last mark is credited;
    - when market also carries raw ``open``, ``up_limit`` and ``down_limit``
      columns, a security opening at its up limit cannot be bought and one
      opening at its down limit cannot be sold (set ``enforce_price_limits``
      to False to disable); rows without limits are treated as tradable;
    - costs are explicit bps inputs; no historical tax schedule is invented.

    The adjusted price is an arbitrary-scale accounting price, so fractional
    shares are intentional.  A later execution model can use raw lots while
    preserving the same signal/execution timing contract.
    """
    config = config or BacktestConfig()
    config.validate()
    required_market = {"trade_date", "security_id", "adj_open", "adj_close"}
    required_targets = {"signal_date", "security_id", "target_weight"}
    if required_market - set(market.columns):
        raise ValueError(f"market missing: {sorted(required_market - set(market.columns))}")
    if required_targets - set(targets.columns):
        raise ValueError(f"targets missing: {sorted(required_targets - set(targets.columns))}")
    weights = pd.to_numeric(targets["target_weight"], errors="raise")
    if weights.isna().any() or np.isinf(weights).any():
        raise ValueError("target_weight must be finite; NaN or inf would silently skip trades")
    if (weights < 0).any():
        raise ValueError("long-only backtester does not accept negative weights")
    if targets.duplicated(["signal_date", "security_id"]).any():
        raise ValueError("duplicate (signal_date, security_id) targets would overwrite each other")
    sums = targets.groupby("signal_date")["target_weight"].sum()
    if (sums > 1.0000001).any():
        raise ValueError("target weights cannot exceed 100% gross")

    delist_dates: dict = {}
    if delistings is not None and len(delistings):
        if {"security_id", "delist_date"} - set(delistings.columns):
            raise ValueError("delistings must contain security_id and delist_date")
        valid = delistings.dropna(subset=["security_id", "delist_date"])
        delist_dates = valid.groupby("security_id")["delist_date"].min().to_dict()

    use_limits = config.enforce_price_limits and {"open", "up_limit", "down_limit"} <= set(market.columns)

    panel = market.sort_values(["trade_date", "security_id"], kind="stable").copy()
    dates = sorted(panel["trade_date"].drop_duplicates().tolist())
    next_session = _next_session_map(dates)

    scheduled: dict = {}
    for signal_date, group in targets.groupby("signal_date"):
        execution_date = next_session.get(signal_date)
        if execution_date is None:
            continue
        scheduled[execution_date] = dict(zip(group["security_id"], group["target_weight"]))

    by_date = {day: group.set_index("security_id") for day, group in panel.groupby("trade_date", sort=True)}
    cash = float(config.initial_cash)
    shares: dict[str, float] = {}
    last_mark: dict[str, float] = {}
    nav_rows, trade_rows = [], []
    previous_nav = float(config.initial_cash)

    for day in dates:
        day_frame = by_date[day]
        open_px = day_frame["adj_open"].dropna().astype(float).to_dict()
        close_px = day_frame["adj_close"].dropna().astype(float).to_dict()

        # A delisted holding stops quoting; without this it would be carried at
        # its last close forever and overstate NAV.
        if delist_dates:
            today = _as_date(day)
            for sec in sorted(s for s, qty in shares.items()
                              if qty and s in delist_dates
                              and _as_date(delist_dates[s]) <= today and s not in open_px):
                mark = last_mark.get(sec, 0.0) * config.delisting_recovery_ratio
                recovered = shares.pop(sec) * mark
                cash += recovered
                trade_rows.append({"trade_date": day, "security_id": sec, "side": "DELIST",
                                   "price": mark, "notional": recovered, "cost": 0.0})

        blocked_buy: set = set()
        blocked_sell: set = set()
        if use_limits:
            raw_open = pd.to_numeric(day_frame["open"], errors="coerce")
            up = pd.to_numeric(day_frame["up_limit"], errors="coerce")
            down = pd.to_numeric(day_frame["down_limit"], errors="coerce")
            at_up = (raw_open.notna() & up.notna() & (raw_open >= up - LIMIT_TOLERANCE)).to_numpy()
            at_down = (raw_open.notna() & down.notna() & (raw_open <= down + LIMIT_TOLERANCE)).to_numpy()
            blocked_buy = set(day_frame.index[at_up])
            blocked_sell = set(day_frame.index[at_down])

        def mark_open(sec: str) -> float | None:
            return open_px.get(sec, last_mark.get(sec))

        equity_open = cash + sum(qty * price for sec, qty in shares.items()
                                 if qty and (price := mark_open(sec)) is not None)
        traded_notional = 0.0
        costs = 0.0

        if day in scheduled and equity_open > 0:
            target = scheduled[day]
            securities = set(shares) | set(target)
            desired = {sec: equity_open * target.get(sec, 0.0) for sec in securities}

            # Sells first. Securities without an open are carried and cannot trade.
            for sec in sorted(securities):
                price = open_px.get(sec)
                if price is None or sec in blocked_sell:
                    continue
                current_value = shares.get(sec, 0.0) * price
                sell_value = max(0.0, current_value - desired[sec])
                if sell_value <= 0:
                    continue
                qty = sell_value / price
                rate = (config.commission_bps + config.slippage_bps + config.sell_tax_bps) / 10_000
                cost = sell_value * rate
                shares[sec] = shares.get(sec, 0.0) - qty
                cash += sell_value - cost
                traded_notional += sell_value
                costs += cost
                trade_rows.append({"trade_date": day, "security_id": sec, "side": "SELL",
                                   "price": price, "notional": sell_value, "cost": cost})

            buys = []
            for sec in sorted(securities):
                price = open_px.get(sec)
                if price is None or sec in blocked_buy:
                    continue
                current_value = shares.get(sec, 0.0) * price
                buy_value = max(0.0, desired[sec] - current_value)
                if buy_value > 0:
                    buys.append((sec, price, buy_value))

            buy_rate = (config.commission_bps + config.slippage_bps) / 10_000
            required_cash = sum(v * (1 + buy_rate) for _, _, v in buys)
            scale = min(1.0, cash / required_cash) if required_cash > 0 else 1.0
            for sec, price, requested in buys:
                buy_value = requested * scale
                if buy_value <= 0:
                    continue
                cost = buy_value * buy_rate
                shares[sec] = shares.get(sec, 0.0) + buy_value / price
                cash -= buy_value + cost
                traded_notional += buy_value
                costs += cost
                trade_rows.append({"trade_date": day, "security_id": sec, "side": "BUY",
                                   "price": price, "notional": buy_value, "cost": cost})

            shares = {sec: qty for sec, qty in shares.items() if abs(qty) > 1e-12}

        last_mark.update(close_px)
        nav_close = cash + sum(qty * last_mark[sec] for sec, qty in shares.items() if sec in last_mark)
        daily_return = nav_close / previous_nav - 1 if previous_nav else 0.0
        nav_rows.append({
            "trade_date": day,
            "nav": nav_close,
            "return": daily_return,
            "cash": cash,
            "gross_exposure": (nav_close - cash) / nav_close if nav_close else 0.0,
            "turnover": traded_notional / equity_open if equity_open else 0.0,
            "cost": costs,
        })
        previous_nav = nav_close

    return BacktestResult(pd.DataFrame(nav_rows), pd.DataFrame(trade_rows))
