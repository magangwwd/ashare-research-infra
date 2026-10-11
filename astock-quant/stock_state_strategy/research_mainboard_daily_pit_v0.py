"""Retrospective MAIN-BOARD daily-bar entry study, paper only, NOT frozen V1.

All historically present A-share main-board tickers are scanned on each past
trading date without selecting survivors from the 2026-10-09 A-pool.
Uses only prior/current OHLCV for signals, T+1 opening for execution.
No claims of an unseen holdout before 2026-10-10, historical PIT ownership or
realistic point-in-time corporate-news screening. No automated orders.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

START = pd.Timestamp("2024-01-01")
END = pd.Timestamp("2026-10-09")
BUY_FEE, SELL_FEE, SLIP = 0.0013, 0.0018, 0.001
MAX_POSITIONS, MAX_HOLD = 5, 20
INITIAL_CASH = 1_000_000.
MIN_TURNOVER = 100_000_000.

def load_panel(root: Path) -> pd.DataFrame:
    specs = {
        "daily_prices": ["vendor_symbol", "trade_date", "open", "high", "low",
                         "close", "volume_shares", "turnover_cny", "is_quality_valid"],
        "adjustment_factors": ["vendor_symbol", "trade_date", "adjustment_factor"],
    }
    data = {}
    for dataset, columns in specs.items():
        frames = []
        for year in (2023, 2024, 2025, 2026):
            for path in sorted((root / dataset / f"year={year}").glob("month=*/data.parquet")):
                part = pd.read_parquet(path, columns=columns)
                frames.append(part)
        if not frames:
            raise RuntimeError(f"No {dataset} data: {root}")
        data[dataset] = pd.concat(frames, ignore_index=True)
        del frames
    panel = data["daily_prices"].merge(
        data["adjustment_factors"], on=["vendor_symbol", "trade_date"],
        how="left", validate="one_to_one"
    )
    del data
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    panel = panel[panel["trade_date"].le(END)].copy()
    if panel["trade_date"].max() != END:
        raise RuntimeError(f"Stale data: {panel['trade_date'].max()}, expected {END}")
    if panel["adjustment_factor"].isna().any():
        raise RuntimeError("Missing adjustment factors; cannot responsibly backtest")
    for f in ["open", "high", "low", "close", "volume_shares", "turnover_cny", "adjustment_factor"]:
        panel[f] = pd.to_numeric(panel[f], errors="coerce")
    good = (panel["is_quality_valid"].eq(True)
            & panel[["open", "high", "low", "close", "adjustment_factor"]].gt(0).all(axis=1)
            & panel["volume_shares"].gt(0))
    bad = int((~good).sum())
    if bad:
        print(f"Excluded {bad} invalid/zero-volume rows; missing bars cannot be traded", flush=True)
    panel = panel[good].copy()
    for c in ("open", "high", "low", "close"):
        panel["p_" + c] = panel[c] * panel["adjustment_factor"]
    panel = panel.sort_values(["vendor_symbol", "trade_date"], kind="stable")
    return panel


def history_features(bars: pd.DataFrame) -> pd.DataFrame:
    x = bars.reset_index(drop=True).copy()
    close, high, low = x["p_close"], x["p_high"], x["p_low"]
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    x["atr14"] = tr.rolling(14, min_periods=14).mean()
    ma = close.rolling(20, min_periods=20).mean()
    prior_high = high.shift(1).rolling(20, min_periods=20).max()
    prior_volume = x["volume_shares"].shift(1).rolling(20, min_periods=20).mean()
    prior_turnover = x["turnover_cny"].shift(1).rolling(20, min_periods=20).mean()
    one_word_20 = high.sub(low).abs().lt(1.e-9).rolling(20, min_periods=20).sum()
    volume_factor = x["volume_shares"] / prior_volume
    age_ok = pd.Series(np.arange(len(x)) >= 249, index=x.index)
    eligible = (
        age_ok & prior_turnover.ge(MIN_TURNOVER)
        & one_word_20.le(3) & ma.gt(ma.shift(5)) & close.gt(ma)
        & x["atr14"].gt(0)
    )
    breakout = (
        eligible & close.gt(prior_high) & close.le(prior_high.mul(1.04))
        & volume_factor.ge(1.2)
    )
    pullback = (
        eligible & low.le(ma.mul(1.015)) & close.le(ma.mul(1.04))
        & close.gt(x["p_open"]) & volume_factor.between(0.5, 1.0)
    )
    x["signal"] = np.select([breakout, pullback], ["BREAKOUT", "PULLBACK"], default="")
    x["signal_strength"] = np.where(
        breakout, 2.0 + volume_factor.clip(upper=5).fillna(0),
        np.where(pullback, 1.0 + (1.0 - volume_factor).clip(0, 0.5).fillna(0), 0)
    )
    return x


def plan_market(panel: pd.DataFrame):
    sessions = sorted(panel["trade_date"].drop_duplicates().tolist())
    next_session = dict(zip(sessions[:-1], sessions[1:]))
    daily_candidates = defaultdict(list)
    daily_future = []
    per_symbol = {}
    counters = Counter()
    names = sorted(panel["vendor_symbol"].dropna().unique())
    for k, symbol in enumerate(names, 1):
        sub = history_features(panel[panel["vendor_symbol"].eq(symbol)])
        per_symbol[symbol] = sub.set_index("trade_date")[[
            "open", "high", "low", "close", "p_open", "p_high", "p_low", "p_close",
            "volume_shares", "adjustment_factor"
        ]]
        sigs = sub.loc[sub["signal"].ne("") & sub["trade_date"].ge(START)].copy()
        for row in sigs.itertuples(index=False):
            c = {"symbol": symbol, "signal_date": row.trade_date,
                 "pattern": row.signal, "strength": float(row.signal_strength),
                 "signal_close": float(row.p_close), "atr": float(row.atr14)}
            counters["patterns_" + row.signal] += 1
            next_date = next_session.get(row.trade_date)
            if next_date is None:
                daily_future.append(c)
            elif next_date in per_symbol[symbol].index:
                daily_candidates[next_date].append(c)
            else:
                counters["next_session_missing"] += 1
        if k % 500 == 0 or k == len(names):
            print(f"Processed main-board history {k}/{len(names)} symbols", flush=True)
    for date in daily_candidates:
        daily_candidates[date].sort(key=lambda x: (-x["strength"], x["symbol"]))
    print(f"Historical scan: {len(names)} tickers; {sum(len(x) for x in daily_candidates.values())} pending executable-day candidates", flush=True)
    return sessions, per_symbol, daily_candidates, daily_future, counters


def findbar(frame: pd.DataFrame, date: pd.Timestamp):
    if date not in frame.index:
        return None
    bar = frame.loc[date]
    if isinstance(bar, pd.DataFrame):
        raise ValueError("Duplicate date in price series")
    return bar


def exit_position(p, quote: float, reason: str, date, cash: float, trades: list):
    executed = float(quote) * (1 - SLIP)
    proceeds = p["shares"] * executed * (1 - SELL_FEE)
    cash += proceeds
    invested = p["shares"] * p["entry_price"] * (1 + BUY_FEE)
    pnl = proceeds / invested - 1
    trades.append({
        "symbol": p["symbol"], "pattern": p["pattern"],
        "signal_date": str(p["signal_date"].date()),
        "entry_date": str(p["entry_date"].date()), "exit_date": str(date.date()),
        "entry_price_adjusted": round(p["entry_price"], 5),
        "exit_quote_adjusted": round(float(quote), 5),
        "risk_pct": round(p["risk_pct"], 6),
        "net_return": float(pnl), "net_R": float(pnl / p["risk_pct"]),
        "entry_weight": round(p["weight"], 5),
        "net_portfolio_pnl": round(proceeds - invested, 2),
        "exit_reason": reason,
        "hold_sessions": int(p["hold_count"] + 1),
    })
    return cash


def run_portfolio(sessions, price_by_sym, scheduled):
    cash = INITIAL_CASH
    positions = {}
    trades, nav = [], []
    skipped = Counter()
    for index, date in enumerate(sessions):
        if date < START:
            continue
        exited = set()
        # Snapshot the OPEN equity BEFORE any same-session intraday outcome.
        # Intraday exits cannot finance entries executed earlier at the OPEN.
        opening_equity = cash + sum(
            p["shares"] * (
                float(bar["p_open"]) if (bar := findbar(price_by_sym[symbol], date)) is not None
                else p["last_price"]
            )
            for symbol, p in positions.items()
        )
        intraday_proceeds = 0.0
        intraday_slots = 0
        # Sell previously held positions only if actually quoted.
        for symbol in list(positions):
            p = positions[symbol]
            bar = findbar(price_by_sym[symbol], date)
            if bar is None:
                p["hold_count"] += 1
                continue
            op = float(bar["p_open"])
            # Unknown exchange-specific hard-down-limit execution: conservatively defer.
            one_word_down = (abs(float(bar["high"] - bar["low"])) < 1e-8
                             and op / p["last_price"] < 0.905)
            if one_word_down:
                p["last_price"] = float(bar["p_close"])
                p["hold_count"] += 1
                skipped["deferred_locked_limit_down"] += 1
                continue
            reason, quote = None, None
            if p["sell_pending"]:
                reason, quote = "ENTRY_DAY_STOP_TPLUS1", op
            elif op <= p["stop"]:
                reason, quote = "STOP_GAP", op
            elif op >= p["target"]:
                reason, quote = "TARGET_GAP", op
            elif float(bar["p_low"]) <= p["stop"]:
                reason, quote = "STOP", p["stop"]
            elif float(bar["p_high"]) >= p["target"]:
                reason, quote = "TARGET", p["target"]
            elif p["hold_count"] >= MAX_HOLD - 1:
                reason, quote = "TIME_EXIT", float(bar["p_close"])
            if reason:
                previous_cash = cash
                cash = exit_position(p, quote, reason, date, cash, trades)
                # Only overnight-pending exits and price-gap exits are executable
                # at the OPEN; an intraday stop/target/time exit is too late
                # to release buying capacity at today's OPEN.
                if reason not in ("ENTRY_DAY_STOP_TPLUS1", "STOP_GAP", "TARGET_GAP"):
                    intraday_proceeds += cash - previous_cash
                    intraday_slots += 1
                del positions[symbol]
                exited.add(symbol)
            else:
                p["hold_count"] += 1
                p["last_price"] = float(bar["p_close"])
        # New signal was fixed by yesterday's CLOSE, execution is today's OPEN.
        for candidate in scheduled.get(date, []):
            if len(positions) + intraday_slots >= MAX_POSITIONS or cash - intraday_proceeds <= 0:
                break
            symbol = candidate["symbol"]
            if symbol in positions or symbol in exited:
                continue
            bar = findbar(price_by_sym[symbol], date)
            if bar is None:
                skipped["unavailable_entry_bar"] += 1
                continue
            op = float(bar["p_open"])
            gap = op / candidate["signal_close"] - 1
            locked_up = (abs(float(bar["high"] - bar["low"])) < 1e-8 and gap >= 0.095)
            if abs(gap) > 0.03 or locked_up:
                skipped["gap_or_limit"] += 1
                continue
            entry = op * (1 + SLIP)
            risk = 2 * candidate["atr"]
            risk_pct = risk / entry
            if not (0.015 <= risk_pct <= 0.10):
                skipped["atr_risk"] += 1
                continue
            weight = min(0.15, 0.005 / risk_pct)
            notional = min(opening_equity * weight, (cash - intraday_proceeds) / (1 + BUY_FEE))
            if notional < 10_000:
                skipped["small_notional"] += 1
                continue
            shares = notional / entry
            cash -= notional * (1 + BUY_FEE)
            p = {"symbol": symbol, "pattern": candidate["pattern"],
                 "signal_date": candidate["signal_date"], "entry_date": date,
                 "entry_price": entry, "risk_pct": risk_pct, "weight": weight,
                 "shares": shares, "stop": entry - risk, "target": entry + 2 * risk,
                 "last_price": float(bar["p_close"]), "hold_count": 0,
                 "sell_pending": float(bar["p_low"]) <= entry - risk}
            positions[symbol] = p
            skipped["entered"] += 1
        # Mark-to-market using latest observed close; missing bars carry previous mark.
        equity = cash + sum(p["shares"] * p["last_price"] for p in positions.values())
        nav.append({"trade_date": str(date.date()), "nav": equity,
                    "cash": cash, "held": len(positions),
                    "exposure": (equity - cash) / equity if equity > 0 else 0})
    return pd.DataFrame(trades), pd.DataFrame(nav), dict(skipped), len(positions)


def stats(trades: pd.DataFrame, nav: pd.DataFrame):
    if nav.empty:
        raise ValueError("Empty daily NAV series")
    curve = nav["nav"]
    peak = curve.cummax()
    dd = (curve / peak - 1)
    daily = curve.pct_change().fillna(0)
    result = {
        "closed_trades": len(trades), "initial_cash": INITIAL_CASH,
        "final_equity": round(float(curve.iloc[-1]), 2),
        "net_total_return": round(float(curve.iloc[-1] / INITIAL_CASH - 1), 5),
        "daily_nav_max_drawdown": round(float(dd.min()), 5),
        "average_exposure": round(float(nav["exposure"].mean()), 4),
        "worst_nav_day": round(float(daily.min()), 5)
    }
    if len(trades):
        r = trades["net_return"]
        wins, losses = r[r > 0], r[r < 0]
        result.update({
            "trade_win_rate": round(float(r.gt(0).mean()), 4),
            "average_trade_net_return": round(float(r.mean()), 5),
            "average_trade_net_R": round(float(trades["net_R"].mean()), 4),
            "trade_profit_factor": round(float(wins.sum() / -losses.sum()), 4) if len(losses) else None,
            "average_win_loss_ratio": round(float(wins.mean() / -losses.mean()), 4)
              if len(wins) and len(losses) else None,
        })
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    panel = load_panel(args.root)
    source_rows, universe = len(panel), int(panel["vendor_symbol"].nunique())
    print(f"Historical market: {source_rows:,} rows, {universe:,} tickers, end {END.date()}", flush=True)
    sessions, prices, signals, latest, counters = plan_market(panel)
    del panel
    trade, nav, execution, open_positions = run_portfolio(sessions, prices, signals)
    if trade.empty:
        raise RuntimeError("No actual simulated trades; no reliable outcome")
    args.output.mkdir(parents=True, exist_ok=True)
    trade.to_csv(args.output / "trades.csv", index=False, encoding="utf-8-sig")
    nav.to_csv(args.output / "daily_nav.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(latest).to_csv(args.output / "latest_unexecuted_signals.csv", index=False, encoding="utf-8-sig")
    overall = stats(trade, nav)
    periods = {
        "2024_seen_development": ("2024-01-01", "2024-12-31"),
        "2025_2026_seen_diagnostic": ("2025-01-01", "2026-10-09"),
    }
    period_data = {}
    for label, (a, b) in periods.items():
        nav_piece = nav[(nav["trade_date"] >= a) & (nav["trade_date"] <= b)]
        closed = trade[(trade["exit_date"] >= a) & (trade["exit_date"] <= b)]
        st = stats(closed, nav_piece)
        first_nav = float(nav_piece["nav"].iloc[0])
        st["period_return_from_first_nav"] = round(float(nav_piece["nav"].iloc[-1] / first_nav - 1), 5)
        period_data[label] = st
    pattern = {key: {"closed_trades": int(len(part)),
                     "win_rate": round(float(part["net_return"].gt(0).mean()), 4),
                     "avg_net_return": round(float(part["net_return"].mean()), 5)}
               for key, part in trade.groupby("pattern")}
    result = {
        "status": "HISTORICAL_DIAGNOSTIC_NO_ENTRY_AUTHORIZATION",
        "source_as_of_date": "2026-10-09", "scope": "A-share MAIN BOARD only",
        "historical_records": source_rows, "historically_quoted_tickers": universe,
        "signal_basis": "PIT price/volume and prior trading data; no current A-pool selection",
        "research_start": str(START.date()), "warmup": "2023 data",
        "first_unseen_forward_date": "2026-10-12 (when new future data arrives)",
        "time_split": periods, "overall": overall, "by_period": period_data,
        "by_entry_pattern": pattern, "scanned_signals": dict(counters),
        "execution": execution, "positions_still_open": open_positions,
        "latest_day_unexecuted_signals": len(latest),
        "cost_model": {"buy_fee": BUY_FEE, "sell_fee": SELL_FEE, "slippage_per_side": SLIP},
        "portfolio": {"max_concurrent_stocks": MAX_POSITIONS, "max_weight_per_stock": 0.15,
                      "risk_budget_equity_per_entry": 0.005, "stop": "2 ATR14",
                      "target": "4 ATR14", "time_limit_trading_sessions": MAX_HOLD,
                      "A_share_T_plus_1": "Entry-day stop queued to following available open"},
        "critical_limits": [
            "Both historical periods have already been examined; neither is an untouched test set.",
            "Does NOT recreate point-in-time A-state or ownership disclosure membership.",
            "Historical ST name checks and precise daily exchange limit-price checks are not applied.",
            "Main-board universe reflects historical records retained in 2026 Gold; delisting omissions remain possible.",
            "No corporate disclosure negative-news screening; no intraday 15-minute bar simulation.",
            "Adjusted OHLC returns approximate dividends/corporate actions; fees/slippage are assumptions.",
            "Portfolio is 5-sleeve research simulation, not frozen V1 nor brokerage execution."
        ],
    }
    (args.output / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# A股主板逐日滚动筛选技术入场历史诊断",
        "",
        "仅使用日线历史中当日及之前可见的K线与成交量生成信号，从下一交易日开盘模拟。",
        "不使用2026-10-09选出的5只股票回填历史名单。不是冻结V1策略或实盘买入信号。",
        "",
        "## 全样本统计（**2024—2026均为已看历史诊断**）",
        "",
        "| 指标 | 数值 |", "|---|---:|",
    ]
    for k,v in overall.items():
        lines.append(f"| {k} | {v} |")
    lines += ["", "## 历史时间分段", "", "| 分段 | 已平仓数 | 胜率 | 利润因子 | 分段回撤 |",
              "|---|---:|---:|---:|---:|"]
    for k,v in period_data.items():
        lines.append(f"| {k} | {v['closed_trades']} | {v.get('trade_win_rate')} | "
                     f"{v.get('trade_profit_factor')} | {v['daily_nav_max_drawdown']} |")
    lines += ["", "## 研究限制"] + ["- " + x for x in result["critical_limits"]]
    lines += ["", "完整模拟交易见 trades.csv，逐日组合净值见 daily_nav.csv。",
              "2026-10-09当日信号属于待未来验证，保存在latest_unexecuted_signals.csv。",
              "未经未来未见行情前向验证，不得把以上统计转换为'大概率买入'提示。"]
    (args.output / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("MAINBOARD_PIT_SUMMARY=" + json.dumps(overall, ensure_ascii=False), flush=True)
    print("MAINBOARD_PIT_PERIODS=" + json.dumps(period_data, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
