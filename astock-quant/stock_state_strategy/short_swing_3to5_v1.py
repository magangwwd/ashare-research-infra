"""Independent fixed-CNY, one-stock A-share main-board short swing PAPER strategy.

Signals are evaluated at the signal session close; fills are evaluated no sooner
than the next session. No broker APIs, real orders or portfolio weights.
Frozen LOCK_MARKUP_SINGLE_PAPER_V1 is deliberately not imported or changed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

VERSION = "MAINBOARD_SWING_3TO5_FIXED_CNY_PAPER_V1"
MAINBOARD = re.compile(r"^(?:60[0135]\d{3})\.SH$|^(?:00[0123]\d{3})\.SZ$")
COLUMNS = {
    "daily_prices": ["vendor_symbol", "trade_date", "open", "high", "low", "close",
                     "volume_shares", "turnover_cny", "is_quality_valid"],
    "adjustment_factors": ["vendor_symbol", "trade_date", "adjustment_factor"],
}


@dataclass(frozen=True)
class Policy:
    initial_cash_cny: float = 50000.0
    order_budget_cny: float = 40000.0
    min_order_cny: float = 30000.0
    max_order_cny: float = 50000.0
    buy_lot: int = 100
    stop_loss: float = 0.03
    take_profit: float = 0.05
    min_hold_sessions: int = 3
    max_hold_sessions: int = 5
    max_gap_up: float = 0.02
    min_turnover_cny: float = 100_000_000.0
    min_volume_ratio: float = 1.2
    min_breadth_symbols: int = 500
    min_breadth_fraction: float = 0.45
    commission_rate: float = 0.0003
    min_commission_cny: float = 5.0
    transfer_rate: float = 0.00001
    sell_stamp_rate: float = 0.0005
    slippage_rate: float = 0.001

    def validate(self) -> None:
        if not (30000 <= self.min_order_cny <= self.order_budget_cny <=
                self.max_order_cny <= 50000):
            raise ValueError("Each planned order must be between CNY 30,000 and 50,000")
        if self.initial_cash_cny < self.order_budget_cny * (1 + self.commission_rate + self.transfer_rate):
            raise ValueError("Initial cash cannot cover the requested order and fees")
        if self.buy_lot != 100 or self.min_hold_sessions != 3 or self.max_hold_sessions != 5:
            raise ValueError("Fixed one-lot, three-to-five session policy was changed")
        if not (0 < self.stop_loss < self.take_profit < 1):
            raise ValueError("Invalid risk bounds")
        if not (0 <= self.max_gap_up <= 0.03 and 0 <= self.slippage_rate < 0.02):
            raise ValueError("Invalid slippage/gap limits")
        if min(self.commission_rate, self.transfer_rate, self.sell_stamp_rate,
               self.min_commission_cny, self.min_breadth_symbols) < 0:
            raise ValueError("Negative rate/minimum")


def is_mainboard(symbol: str) -> bool:
    return bool(MAINBOARD.fullmatch(str(symbol)))


def buy_fee(notional: float, p: Policy) -> float:
    return max(p.min_commission_cny, notional * p.commission_rate) + notional * p.transfer_rate


def sell_fee(notional: float, p: Policy) -> float:
    return (max(p.min_commission_cny, notional * p.commission_rate)
            + notional * (p.transfer_rate + p.sell_stamp_rate))


def load_gold(root: Path, start: str, end: str) -> tuple[pd.DataFrame, dict, str]:
    """Gold-only input with manifest audit. No stale CSV or synthetic fallback."""
    since, until = pd.Timestamp(start), pd.Timestamp(end)
    if since < pd.Timestamp("2024-01-01") or until < since:
        raise ValueError("Backtest start >= 2024-01-01; end >= start")
    manifest_file = root / "manifest.json"
    manifest_bytes = manifest_file.read_bytes()
    manifest = json.loads(manifest_bytes)
    if (pd.Timestamp(manifest.get("as_of_date", "1970-01-01")) < until or
            manifest.get("validation", {}).get("primary_keys_unique") is not True or
            manifest.get("validation", {}).get("ohlc_valid") is not True):
        raise RuntimeError("Gold manifest is too old or failed mandatory validation")
    data = {}
    for name, cols in COLUMNS.items():
        paths = [f for y in range(since.year - 1, until.year + 1)
                 for f in sorted((root / name / f"year={y}").glob("month=*/data.parquet"))]
        if not paths:
            raise FileNotFoundError("No Gold partitions for " + name)
        data[name] = pd.concat((pd.read_parquet(f, columns=cols) for f in paths), ignore_index=True)
    prices = data["daily_prices"]
    factors = data["adjustment_factors"]
    for frame in (prices, factors):
        frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.normalize()
        frame.drop(frame.index[frame.trade_date.gt(until)], inplace=True)
        if frame.duplicated(["vendor_symbol", "trade_date"]).any():
            raise RuntimeError("Duplicate Gold price/factor key")
    prices = prices[prices.vendor_symbol.map(is_mainboard)].copy()
    merged = prices.merge(factors, on=["vendor_symbol", "trade_date"],
                          how="left", validate="one_to_one")
    if merged.empty or merged.trade_date.max() < until:
        raise RuntimeError("Gold does not cover the requested backtest end session")
    required = ["open", "high", "low", "close", "volume_shares", "turnover_cny", "adjustment_factor"]
    for key in required:
        merged[key] = pd.to_numeric(merged[key], errors="coerce")
    if (merged[required].isna().any().any() or
            not merged["is_quality_valid"].eq(True).all() or
            not merged[["open", "high", "low", "close", "adjustment_factor"]].gt(0).all().all() or
            not merged["turnover_cny"].ge(0).all() or
            not merged["volume_shares"].gt(0).all() or
            not merged["high"].ge(merged[["open", "close", "low"]].max(axis=1)).all() or
            not merged["low"].le(merged[["open", "close", "high"]].min(axis=1)).all()):
        raise RuntimeError("Gold price data failed strict row-level validation")
    return (merged.sort_values(["vendor_symbol", "trade_date"], kind="stable"),
            manifest, hashlib.sha256(manifest_bytes).hexdigest())


def build_signals(panel: pd.DataFrame, policy: Policy, risk: GoldPITRisk) -> dict:
    """20-day breakout + MA20>MA60 + volume; all features trailing/close-only."""
    dated = panel.sort_values(["vendor_symbol", "trade_date"], kind="stable").copy()
    dated["adjusted_close"] = dated.close * dated.adjustment_factor
    grouped = dated.groupby("vendor_symbol", sort=False)
    dated["ma20"] = grouped.adjusted_close.transform(lambda s: s.rolling(20, min_periods=20).mean())
    dated["ma60"] = grouped.adjusted_close.transform(lambda s: s.rolling(60, min_periods=60).mean())
    dated["prev_20_high"] = grouped.adjusted_close.transform(
        lambda s: s.shift(1).rolling(20, min_periods=20).max())
    dated["prev_5_vol"] = grouped.volume_shares.transform(
        lambda s: s.shift(1).rolling(5, min_periods=5).mean())
    dated["prev_20_turnover"] = grouped.turnover_cny.transform(
        lambda s: s.shift(1).rolling(20, min_periods=20).mean())
    dated["volume_ratio"] = dated.volume_shares / dated.prev_5_vol.replace(0, float("nan"))
    dated["strength"] = (dated.adjusted_close / dated.prev_20_high - 1) + (dated.volume_ratio - 1) * 0.005
    result = {}
    for day, frame in dated.groupby("trade_date", sort=True):
        breadth = frame.loc[frame.ma20.notna()]
        if len(breadth) < policy.min_breadth_symbols:
            continue
        above_fraction = float(breadth.adjusted_close.gt(breadth.ma20).mean())
        if above_fraction < policy.min_breadth_fraction:
            continue
        matches = frame.loc[
            frame.ma60.notna() & frame.prev_20_high.notna() &
            frame.ma20.gt(frame.ma60) & frame.adjusted_close.gt(frame.prev_20_high) &
            frame.volume_ratio.ge(policy.min_volume_ratio) &
            frame.prev_20_turnover.ge(policy.min_turnover_cny) &
            frame.close.gt(frame.open)
        ].sort_values(["strength", "vendor_symbol"], ascending=[False, True])
        result[day] = [(str(r.vendor_symbol), float(r.strength))
                       for r in matches.itertuples(index=False)
                       if risk.name_status(str(r.vendor_symbol), day) == "NORMAL"]
    return result


def simulate(panel: pd.DataFrame, risk: GoldPITRisk, policy: Policy,
             signals: dict | None = None, start: str | None = None) -> dict:
    """Exactly one non-fractional 100-share-lot position; no same-day recycling.

    Historical factor ratios are total-return PRICE PROXIES, not a corporate
    action share/cash ledger. Result is research only, not executable PnL.
    """
    policy.validate()
    if panel.duplicated(["vendor_symbol", "trade_date"]).any():
        raise ValueError("Duplicated market day-symbol")
    panel = panel.copy()
    panel["trade_date"] = pd.to_datetime(panel.trade_date).dt.normalize()
    dates = sorted(pd.Timestamp(x) for x in panel.trade_date.unique())
    by_day = {d: f.set_index("vendor_symbol") for d, f in panel.groupby("trade_date", sort=True)}
    signals = build_signals(panel, policy, risk) if signals is None else signals
    cash = policy.initial_cash_cny
    position = None
    last_mark = None
    orders, trades, nav, exceptions = [], [], [], []
    pending = None
    max_nav = cash
    for i, day in enumerate(dates):
        frame = by_day[day]
        exited_today = False
        def sell(reason: str, quote: float, *, at_open: bool) -> None:
            nonlocal position, cash, last_mark, exited_today
            sale_px = quote * (1 - policy.slippage_rate)
            gross = position["shares"] * sale_px
            fees = sell_fee(gross, policy)
            cash += gross - fees
            invested = position["gross_entry"] + position["buy_fees"]
            pnl = gross - fees - invested
            hold = i - position["entry_index"] + 1
            trades.append({
                "symbol": position["symbol"], "signal_date": str(position["signal_date"].date()),
                "entry_date": str(position["entry_date"].date()), "exit_date": str(day.date()),
                "entry_shares": position["shares"], "gross_entry_cny": round(position["gross_entry"], 2),
                "entry_price_raw": position["raw_entry_price"],
                "exit_price_entry_factor_equivalent": round(sale_px, 6),
                "buy_fees_cny": round(position["buy_fees"], 2),
                "sell_fees_cny": round(fees, 2), "pnl_cny": round(pnl, 2),
                "net_return": round(pnl / invested, 8), "holding_sessions": hold,
                "exit_reason": reason, "corporate_action_proxy": position["corporate_action_proxy"],
            })
            orders.append({"date": str(day.date()), "side": "SELL_ALL", "symbol": position["symbol"],
                           "shares": position["shares"], "reason": reason,
                           "execution": "OPEN" if at_open else "DAILY_OHLC_ASSUMPTION"})
            position = None
            last_mark = None
            exited_today = True

        if position is not None:
            symbol = position["symbol"]
            if symbol not in frame.index:
                exceptions.append({"date": str(day.date()), "symbol": symbol, "reason": "NO_BAR_EXIT_DEFERRED"})
                if i - position["entry_index"] + 1 >= policy.max_hold_sessions:
                    position["forced_exit_pending"] = True
            else:
                bar = frame.loc[symbol]
                f = float(bar.adjustment_factor)
                factor_ratio = f / position["entry_factor"]
                if not math.isclose(factor_ratio, 1.0, abs_tol=1e-8):
                    position["corporate_action_proxy"] = True
                op = float(bar.open) * factor_ratio
                hi = float(bar.high) * factor_ratio
                lo = float(bar.low) * factor_ratio
                cl = float(bar.close) * factor_ratio
                last_mark = cl
                hold = i - position["entry_index"] + 1
                block = risk.exit_reason(symbol, day, bar)
                if block:
                    exceptions.append({"date": str(day.date()), "symbol": symbol, "reason": block})
                    if hold >= policy.max_hold_sessions:
                        position["forced_exit_pending"] = True
                elif position["entry_index"] < i:
                    if position["entry_day_stop"] or position["forced_exit_pending"]:
                        sell("DEFERRED_STOP_OR_TIME", op, at_open=True)
                    elif op <= position["stop"]:
                        sell("STOP_GAP", op, at_open=True)
                    elif lo <= position["stop"]:
                        sell("STOP_INTRADAY_ASSUMED", position["stop"], at_open=False)
                    elif hold >= policy.min_hold_sessions and op >= position["target"]:
                        sell("TARGET_GAP", op, at_open=True)
                    elif hold >= policy.min_hold_sessions and hi >= position["target"]:
                        sell("TARGET_INTRADAY_ASSUMED", position["target"], at_open=False)
                    elif hold >= policy.max_hold_sessions:
                        sell("TIME_5_CLOSE", cl, at_open=False)
                elif lo <= position["stop"]:
                    position["entry_day_stop"] = True
                    exceptions.append({"date": str(day.date()), "symbol": symbol,
                                       "reason": "ENTRY_DAY_STOP_TPLUS1_PENDING"})

        if position is None and not exited_today and pending is not None and (start is None or day >= pd.Timestamp(start)):
            signal_day, choices = pending
            for symbol, score in choices:
                if not is_mainboard(symbol) or symbol not in frame.index:
                    continue
                bar = frame.loc[symbol]
                reject = risk.entry_reason(symbol, day, bar)
                if reject:
                    exceptions.append({"date": str(day.date()), "symbol": symbol, "reason": "ENTRY_" + reject})
                    continue
                signal_frame = by_day[signal_day]
                if symbol not in signal_frame.index:
                    continue
                signal_close_adjusted = (float(signal_frame.loc[symbol].close) *
                                         float(signal_frame.loc[symbol].adjustment_factor))
                open_adjusted = float(bar.open) * float(bar.adjustment_factor)
                gap = open_adjusted / signal_close_adjusted - 1
                if not math.isfinite(gap) or gap > policy.max_gap_up:
                    exceptions.append({"date": str(day.date()), "symbol": symbol, "reason": "GAP_TOO_HIGH"})
                    continue
                raw_buy = float(bar.open) * (1 + policy.slippage_rate)
                max_shares = int(policy.order_budget_cny // (raw_buy * policy.buy_lot)) * policy.buy_lot
                while max_shares >= policy.buy_lot and (max_shares * raw_buy + buy_fee(max_shares * raw_buy, policy) > cash):
                    max_shares -= policy.buy_lot
                notional = max_shares * raw_buy
                if not (policy.min_order_cny <= notional <= policy.max_order_cny):
                    exceptions.append({"date": str(day.date()), "symbol": symbol,
                                       "reason": "UNFILLABLE_30K_50K_CASH_OR_LOTS"})
                    continue
                fees = buy_fee(notional, policy)
                cash -= notional + fees
                if cash < -1e-7:
                    raise AssertionError("Negative cash")
                position = {
                    "symbol": symbol, "signal_date": signal_day, "entry_date": day,
                    "entry_index": i, "shares": max_shares, "entry_factor": float(bar.adjustment_factor),
                    "raw_entry_price": round(raw_buy, 6), "gross_entry": notional,
                    "buy_fees": fees, "stop": raw_buy * (1 - policy.stop_loss),
                    "target": raw_buy * (1 + policy.take_profit),
                    "entry_day_stop": False, "forced_exit_pending": False,
                    "corporate_action_proxy": False,
                }
                last_mark = float(bar.close)
                if float(bar.low) <= position["stop"]:
                    position["entry_day_stop"] = True
                    exceptions.append({"date": str(day.date()), "symbol": symbol,
                                       "reason": "ENTRY_DAY_STOP_TPLUS1_PENDING"})
                orders.append({"date": str(day.date()), "side": "BUY_ALL", "symbol": symbol,
                               "shares": max_shares, "notional_cny": round(notional, 2),
                               "signal_date": str(signal_day.date()), "score": round(float(score), 8)})
                break
        pending = None
        if position is None and i + 1 < len(dates):
            pending = (day, signals.get(day, []))
        if start is not None and day < pd.Timestamp(start):
            continue  # Warm-up bars build indicators but do not extend measured NAV.
        position_value = position["shares"] * last_mark if position is not None else 0.0
        equity = cash + position_value
        max_nav = max(max_nav, equity)
        nav.append({"date": str(day.date()), "cash_cny": round(cash, 2),
                    "position_cny": round(position_value, 2), "equity_cny": round(equity, 2),
                    "drawdown": round(equity / max_nav - 1, 8),
                    "symbol": None if position is None else position["symbol"]})
        if position is not None and position["entry_index"] == i and (position["shares"] % 100 != 0):
            raise AssertionError("Non-lot shares")
    net = [t["pnl_cny"] for t in trades]
    equity_final = nav[-1]["equity_cny"] if nav else cash
    return {
        "version": VERSION, "mode": "PAPER_ONLY_NO_BROKER_NO_LIVE_AUTHORIZATION",
        "policy": asdict(policy), "sessions": len(nav), "orders": orders,
        "trades": trades, "daily_nav": nav, "exceptions": exceptions,
        "open_position": position, "summary": {
            "closed_trades": len(trades), "realized_pnl_cny": round(sum(net), 2),
            "win_rate": round(sum(1 for n in net if n > 0) / len(net), 6) if net else None,
            "ending_equity_cny": equity_final, "total_return": round(equity_final / policy.initial_cash_cny - 1, 8),
            "max_drawdown": min((x["drawdown"] for x in nav), default=0),
            "factor_proxy_trades": sum(bool(x["corporate_action_proxy"]) for x in trades),
        },
        "limitations": [
            "Synthetic OHLC executions cannot establish actual fills, intraday order sequence, or liquidity.",
            "Profit target and stop on the same day: stop priority; locked limit-down defers exit.",
            "Suspension or locked limits may force holding >5 sessions; stop loss is not guaranteed.",
            "Adjusted corporate-action factors proxy total return; no audited dividend/share ledger.",
            "Unknown PIT name/limits/suspension data prohibit simulated entry or exit.",
        ],
    }


def run_cli() -> None:
    ap = argparse.ArgumentParser(description="Main-board 3-5 day, fixed RMB paper backtest; no broker")
    ap.add_argument("--market-root", type=Path, required=True)
    ap.add_argument("--risk-root", type=Path, required=True)
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", required=True)
    ap.add_argument("--budget-cny", type=float, default=40000.0)
    ap.add_argument("--cash-cny", type=float, default=50000.0)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    today = datetime.now(ZoneInfo("Asia/Shanghai"))
    if pd.Timestamp(args.end).date() > today.date() or (
            pd.Timestamp(args.end).date() == today.date() and (today.hour, today.minute) < (15, 30)):
        ap.error("End is a future or not-yet-closed market session")
    policy = Policy(order_budget_cny=args.budget_cny, initial_cash_cny=args.cash_cny)
    policy.validate()
    panel, manifest, manifest_sha = load_gold(args.market_root, args.start, args.end)
    from gold_pit_risk_guards import GoldPITRisk
    risk = GoldPITRisk(args.risk_root)
    if not risk.limits or not risk.names:
        raise RuntimeError("No valid PIT risk evidence")
    report = simulate(panel, risk, policy, start=args.start)
    report["source"] = {"manifest_as_of": manifest["as_of_date"],
                        "manifest_sha256": manifest_sha, "start": args.start, "end": args.end,
                        "risk_summary": risk.summary()}
    if args.output.exists():
        raise FileExistsError("Immutable report already exists: " + str(args.output))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"status": report["mode"], "summary": report["summary"],
                      "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    run_cli()
