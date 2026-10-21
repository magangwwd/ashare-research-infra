"""Independent single-ETF self-financing cash/share execution backtest.

Signals: monthly close, effective next available trading session's OPEN.
Cash and ETF shares are carried unchanged between actual rebalance orders.
Missing opens block execution; missing closes may be marked using the prior
observed close for valuation only. Fees and slippage total 0.10% per leg notional.
This research model does not claim exchange-perfect execution or verified
dividend-adjusted total returns.
"""
from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd

from etf_single_rotation.experiments.run_strategy_experiments import (
    ROOT, STOCKS, GOLD, build_monthly_targets, FEE_SLIPPAGE_PER_SIDE,
)
from etf_single_rotation.experiments.walk_forward import walk_forward, CANDIDATES

OUT = ROOT / "results" / "self_financing"
STRATEGIES = (
    "trend_filter", "volatility_control", "cross_asset_defensive",
    "regime_switch", "regime_switch_lowvol", "regime_switch_ultralow",
    "regime_dd_guard15", "regime_dd_guard20", "regime_switch_midvol",
    "regime_switch_cap75", "regime_switch_gold_vol8",
    "regime_switch_gold_cash",
)


def target_for_date(weights: pd.Series):
    valid = weights.fillna(0.0)
    if valid.lt(-1e-10).any() or float(valid.sum()) > 1.0 + 1e-9:
        raise ValueError("Bad weight target")
    invested = valid[valid > 1e-10]
    if len(invested) > 1:
        raise ValueError("Only one ETF may be held")
    if invested.empty:
        return None, 0.0
    return str(invested.index[0]), float(invested.iloc[0])


def quote(opens: pd.Series, name: str):
    if name is None:
        return None
    value = opens.get(name, np.nan)
    if pd.isna(value) or not np.isfinite(float(value)) or float(value) <= 0:
        return None
    return float(value)


def execute_at_open(cash, shares, held, target, target_weight, opens,
                    date, fee_rate=FEE_SLIPPAGE_PER_SIDE):
    """Atomic, cash-funded portfolio trade at open; no daily synthetic rebalance.

    Returns (cash, shares, held, trade_rows, blocked_reason).
    The same-name target adjusts just the needed notional, without
    unrealistically selling and buying the entire position.
    """
    if fee_rate < 0 or fee_rate >= 1:
        raise ValueError("Invalid turnover cost rate")
    if not (0 <= target_weight <= 1.0 + 1e-10):
        raise ValueError("Invalid target weight")
    if target_weight < 1e-10:
        target = None
        target_weight = 0.0
    existing_price = quote(opens, held)
    desired_price = quote(opens, target)
    if held is not None and existing_price is None:
        return cash, shares, held, [], "HELD_ETF_NO_TRADABLE_OPEN"
    if target is not None and desired_price is None:
        return cash, shares, held, [], "TARGET_ETF_NO_TRADABLE_OPEN"
    if cash < -1e-8 or shares < -1e-8:
        raise ValueError("Portfolio cannot hold negative cash or shares")
    cash = float(cash)
    shares = float(shares)
    before = cash + (shares * existing_price if held is not None else 0.0)
    if before <= 0:
        raise ValueError("Portfolio net asset value must stay positive")
    trades = []

    def record(side, symbol, price, notional):
        if notional <= 1e-12:
            return
        trades.append({
            "date": pd.Timestamp(date).date().isoformat(),
            "side": side, "symbol": symbol, "price": price,
            "notional": notional,
            "fee_slippage": notional * fee_rate,
            "shares": notional / price,
        })

    if held != target:
        if held is not None:
            value = shares * existing_price
            cash += value * (1.0 - fee_rate)
            record("SELL", held, existing_price, value)
            shares = 0.0
            held = None
        if target is not None:
            # B/(V-f*B)=w, with the traded notional B and fee f*B.
            bought = target_weight * cash / (1 + target_weight * fee_rate)
            if bought > 0:
                cash -= bought * (1 + fee_rate)
                shares = bought / desired_price
                record("BUY", target, desired_price, bought)
                held = target
    elif target is not None:
        value = shares * existing_price
        nav = cash + value
        current_weight = value / nav
        if target_weight > current_weight + 1e-12:
            amount = (target_weight * nav - value) / (1 + target_weight * fee_rate)
            if amount > cash / (1 + fee_rate) + 1e-9:
                raise ValueError("Insufficient cash for planned buy")
            cash -= amount * (1 + fee_rate)
            shares += amount / existing_price
            record("BUY", held, existing_price, amount)
        elif target_weight < current_weight - 1e-12:
            amount = (value - target_weight * nav) / (1 - target_weight * fee_rate)
            if amount > value + 1e-9:
                raise ValueError("Attempt to oversell position")
            cash += amount * (1 - fee_rate)
            shares -= amount / existing_price
            record("SELL", held, existing_price, amount)
        if shares <= 1e-12:
            shares = 0.0
            held = None
    cost = sum(t["fee_slippage"] for t in trades)
    after = cash + (shares * desired_price if held is not None else 0.0)
    if abs((before - after) - cost) > 1e-8 * max(1.0, before):
        raise AssertionError("Self-financing cash/share conservation violated")
    if cash < -1e-8 or shares < -1e-8:
        raise AssertionError("Negative cash or ETF shares")
    return max(cash, 0.0), max(shares, 0.0), held, trades, None


def simulate(closes: pd.DataFrame, opens: pd.DataFrame, target_weights: pd.DataFrame,
             strategy: str, fee_rate=FEE_SLIPPAGE_PER_SIDE):
    if not closes.index.equals(opens.index) or not closes.index.equals(target_weights.index):
        raise ValueError("Trading and target dates do not align")
    if list(closes.columns) != list(opens.columns) or list(closes.columns) != list(target_weights.columns):
        raise ValueError("ETF columns do not align")
    mark_closes = closes.ffill()
    guard_threshold = (0.15 if strategy == "regime_dd_guard15" else
                       0.20 if strategy == "regime_dd_guard20" else None)
    cash, shares, held = 1.0, 0.0, None
    last_applied = (None, 0.0)
    prev_equity, peak_equity = 1.0, 1.0
    cooldown, guard_armed = 0, True
    ledger, trades = [], []
    for i, date in enumerate(closes.index):
        previous_dd = prev_equity / peak_equity - 1.0
        if guard_threshold is not None and i > 0:
            if guard_armed and previous_dd <= -guard_threshold:
                cooldown = 20
                guard_armed = False
            elif not guard_armed and previous_dd > -(guard_threshold * 0.5):
                guard_armed = True
        target, fraction = target_for_date(target_weights.iloc[i])
        guard_cash = cooldown > 0
        if guard_cash:
            target, fraction = None, 0.0
        key = (target, round(fraction, 12))
        blocked = None
        orders = []
        trade_requested = key != last_applied
        if trade_requested:
            cash, shares, held, orders, blocked = execute_at_open(
                cash, shares, held, target, fraction, opens.iloc[i], date, fee_rate
            )
            if blocked is None:
                last_applied = key
                trades.extend(orders)
        if cooldown > 0:
            cooldown -= 1

        close_mark = None
        if held is not None:
            close_mark = mark_closes.at[date, held]
            if pd.isna(close_mark) or not np.isfinite(float(close_mark)) or close_mark <= 0:
                raise ValueError(f"Missing valuation close for held ETF {held} at {date}")
            close_mark = float(close_mark)
        equity = cash + (shares * close_mark if held is not None else 0.0)
        if equity <= 0:
            raise AssertionError("Nonpositive mark-to-market equity")
        exposure = (shares * close_mark / equity) if held is not None else 0.0
        if exposure > 1 + 1e-8:
            raise AssertionError("Unleveraged portfolio exposure exceeded 1")
        ledger.append({
            "date": date, "cash": cash,
            "held_symbol": held or "CASH", "shares": shares,
            "equity": equity, "close_mark": close_mark,
            "close_exposure": exposure,
            "daily_net_return": equity / prev_equity - 1.0,
            "daily_transaction_cost": sum(o["fee_slippage"] for o in orders),
            "trade_requested": bool(trade_requested),
            "trade_executed": bool(orders),
            "blocked_reason": blocked or "",
            "drawdown_from_peak": equity / max(peak_equity, equity) - 1.0,
            "cooldown_remaining": cooldown,
        })
        prev_equity = equity
        peak_equity = max(peak_equity, equity)
    ledger = pd.DataFrame(ledger)
    all_trades = pd.DataFrame(trades, columns=[
        "date", "side", "symbol", "price", "notional", "fee_slippage", "shares"
    ])
    return ledger, all_trades


def performance(ledger: pd.DataFrame, trades: pd.DataFrame, strategy: str):
    date = pd.to_datetime(ledger.date)
    years = max((date.iloc[-1] - date.iloc[0]).days / 365.25, 1 / 365.25)
    equity = ledger.equity.astype(float)
    daily = ledger.daily_net_return.astype(float)
    sigma = float(daily.std(ddof=1))
    return {
        "strategy": strategy,
        "data_start": date.iloc[0].date().isoformat(),
        "data_end": date.iloc[-1].date().isoformat(),
        "total_return": float(equity.iloc[-1] - 1),
        "cagr": float(equity.iloc[-1] ** (1 / years) - 1),
        "max_drawdown": float((equity / equity.cummax() - 1).min()),
        "sharpe_rf0": float(daily.mean() / sigma * np.sqrt(252)) if sigma > 0 else 0.0,
        "executed_trade_legs": int(len(trades)),
        "traded_notional_total": float(trades.notional.sum()) if not trades.empty else 0.0,
        "fees_slippage_total": float(trades.fee_slippage.sum()) if not trades.empty else 0.0,
        "blocked_trade_sessions": int((ledger.blocked_reason != "").sum()),
        "cash_days_pct": float((ledger.held_symbol == "CASH").mean()),
        "research_status": "PRELIMINARY_NOT_FOR_TRADING",
    }


def executable_walk_forward(closes: pd.DataFrame, opens: pd.DataFrame,
                            targets_by_strategy: dict[str, pd.DataFrame],
                            training_equities: dict[str, pd.Series],
                            folds=None, candidates=None,
                            fee_rate=FEE_SLIPPAGE_PER_SIDE,
                            output_dir=None):
    """Replay a genuinely shared cash/share ledger across strategy switches.

    The earlier retrospective report stitched different stand-alone net-return
    curves and did not trade at fold boundaries. Here selected strategy targets
    are applied to ONE portfolio; the first fold and every switch use next-open
    fills and include sell/buy turnover costs.
    """
    from etf_single_rotation.experiments.walk_forward import (
        FOLDS, CANDIDATES, select_on_history, score
    )
    folds = FOLDS if folds is None else folds
    candidates = CANDIDATES if candidates is None else candidates
    dates = closes.index
    if not (dates.equals(opens.index) and dates.is_monotonic_increasing):
        raise ValueError("Chronological close/open indexes must match")
    combined = pd.DataFrame(0.0, index=dates, columns=closes.columns)
    fold_records, train_records = [], []
    all_test_dates = []
    last_end = None
    for window_start, window_end in folds:
        start, end = pd.Timestamp(window_start), pd.Timestamp(window_end)
        if last_end is not None and start <= last_end:
            raise ValueError("Walk-forward windows cannot overlap")
        train_dates = dates[dates < start]
        test_dates = dates[(dates >= start) & (dates <= end)]
        if len(train_dates) < 500 or len(test_dates) < 20:
            raise ValueError("Insufficient fold history")
        train_end = train_dates[-1].date().isoformat()
        selected, scores = select_on_history(
            training_equities, train_end, candidates=candidates
        )
        train_records += [{"window_start": window_start, **x} for x in scores]
        if selected != "CASH":
            target = targets_by_strategy[selected]
            if not target.index.equals(dates) or list(target.columns) != list(closes.columns):
                raise ValueError("Walk-forward target price-panel mismatch")
            combined.loc[test_dates, :] = target.loc[test_dates, :].to_numpy()
        fold_records.append({
            "train_end": train_end, "test_start": test_dates[0].date().isoformat(),
            "test_end": test_dates[-1].date().isoformat(),
            "selected_strategy": selected,
            "sessions": int(len(test_dates))
        })
        all_test_dates.extend(test_dates)
        last_end = end
    if len(set(all_test_dates)) != len(all_test_dates):
        raise ValueError("Overlapping test dates")
    ledger, trades = simulate(
        closes, opens, combined, "executable_walk_forward", fee_rate=fee_rate
    )
    dated = ledger.set_index("date")
    first_date = pd.Timestamp(all_test_dates[0])
    base_date = dates[dates < first_date][-1]
    date_list = pd.DatetimeIndex(all_test_dates)
    if not dated.index.is_unique or dated.loc[date_list].isna().all().any():
        raise AssertionError("Missing walk-forward portfolio values")
    test_equity = pd.concat([
        pd.Series([dated.at[base_date, "equity"]], index=[base_date]),
        dated.loc[date_list, "equity"]
    ])
    summary = score(test_equity)
    for row in fold_records:
        start = pd.Timestamp(row["test_start"])
        end = pd.Timestamp(row["test_end"])
        prev_day = dates[dates < start][-1]
        slice_equity = pd.concat([
            pd.Series([dated.at[prev_day, "equity"]], index=[prev_day]),
            dated.loc[start:end, "equity"]
        ])
        row.update(score(slice_equity))
    traded = trades.copy()
    if not traded.empty:
        traded["date"] = pd.to_datetime(traded["date"])
        traded = traded[traded.date >= first_date].copy()
    report = {
        "method": "past-only training selection with executable, continuously self-financing single-ETF portfolio",
        "fold_count": len(fold_records),
        "folds": fold_records,
        "stitched_executable_holdout": summary,
        "executed_trade_legs": int(len(traded)),
        "charged_fee_slippage": float(traded.fee_slippage.sum()) if not traded.empty else 0.0,
        "fixed_fee_slippage_per_side": fee_rate,
        "acceptance": "RETROSPECTIVE_EXPLORATORY_NOT_FOR_TRADING",
        "limitations": [
            "Candidates and hyperparameters were reviewed on the historical sample, so this is not virgin prospective OOS.",
            "Historical price adjustment/dividend semantics and five ETF/date gaps remain under review.",
            "The day-after close to next-open execution remains a simplifying tradability model."
        ],
    }
    output_dir = OUT if output_dir is None else Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(fold_records).to_csv(output_dir / "executable_walk_forward_folds.csv", index=False)
    pd.DataFrame(train_records).to_csv(output_dir / "executable_walk_forward_training_scores.csv", index=False)
    dated.loc[date_list].reset_index().to_csv(
        output_dir / "executable_walk_forward_ledger.csv", index=False
    )
    traded.to_csv(output_dir / "executable_walk_forward_trades.csv", index=False)
    (output_dir / "executable_walk_forward_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main():
    source = ROOT / "data" / "prices.csv"
    if not source.exists():
        raise SystemExit("Real prices.csv is required. Never synthesize trading data.")
    raw = pd.read_csv(source, parse_dates=["date"])
    closes = raw.pivot(index="date", columns="symbol", values="close").sort_index()
    opens = raw.pivot(index="date", columns="symbol", values="open").sort_index()
    if raw.duplicated(["date", "symbol"]).any():
        raise SystemExit("Duplicated ETF/date in real price panel")
    if (raw[["open", "close"]] <= 0).any().any():
        raise SystemExit("Nonpositive ETF prices")
    if closes.empty:
        raise SystemExit("No real ETF price rows")
    OUT.mkdir(parents=True, exist_ok=True)
    summary_rows, ledger_rows, trade_rows, curve_rows, histories = [], [], [], [], {}
    targets_by_strategy = {}
    from etf_single_rotation.experiments.run_strategy_experiments import run_one
    old_rows = []
    for strategy in STRATEGIES:
        targets, _ = build_monthly_targets(closes.ffill(), strategy)
        targets_by_strategy[strategy] = targets
        ledger, trades = simulate(closes, opens, targets, strategy)
        summary = performance(ledger, trades, strategy)
        summary_rows.append(summary)
        ledger_rows.append(ledger.assign(strategy=strategy))
        trade_rows.append(trades.assign(strategy=strategy))
        curve_rows.append(pd.DataFrame({"date": ledger.date, "strategy": strategy,
                                        "equity": ledger.equity}))
        histories[strategy] = pd.Series(ledger.equity.values,
                                       index=pd.DatetimeIndex(ledger.date))
        older, _, _, _ = run_one(closes.ffill(), strategy)
        old_rows.append({
            "strategy": strategy,
            "old_exploratory_cagr": older["cagr"],
            "old_exploratory_drawdown": older["max_drawdown"],
            "self_financing_cagr": summary["cagr"],
            "self_financing_drawdown": summary["max_drawdown"],
            "cagr_change_percentage_points": 100 * (summary["cagr"] - older["cagr"]),
            "drawdown_change_percentage_points": 100 * (
                summary["max_drawdown"] - older["max_drawdown"]
            ),
        })
        print("SELF_FINANCING " + json.dumps(summary, ensure_ascii=False))
    pd.DataFrame(summary_rows).to_csv(OUT / "self_financing_strategy_comparison.csv", index=False)
    pd.concat(ledger_rows, ignore_index=True).to_csv(OUT / "self_financing_daily_ledger.csv", index=False)
    pd.concat(trade_rows, ignore_index=True).to_csv(OUT / "self_financing_trades.csv", index=False)
    pd.concat(curve_rows, ignore_index=True).to_csv(OUT / "self_financing_equity_curves.csv", index=False)
    pd.DataFrame(old_rows).to_csv(OUT / "self_financing_vs_exploratory.csv", index=False)
    report = walk_forward(histories, OUT, candidates=CANDIDATES)
    print("SELF_FINANCING_WALK_FORWARD " + json.dumps(report, ensure_ascii=False))
    executed_report = executable_walk_forward(closes, opens, targets_by_strategy, histories)
    print("EXECUTABLE_WALK_FORWARD " + json.dumps(executed_report, ensure_ascii=False))
    quality_path = ROOT / "results" / "data_quality_summary.json"
    quality = json.loads(quality_path.read_text(encoding="utf-8")) if quality_path.exists() else {}
    metadata = {
        "model": "self-financing single ETF + cash, shares carried across days",
        "execution_timing": "signal after month-end close; next session actual available ETF open",
        "fee_slippage": FEE_SLIPPAGE_PER_SIDE,
        "real_data_quality": quality.get("acceptance", "NOT_AUDITED"),
        "maximum_historical_drawdown_target": 0.25,
        "strategies": list(STRATEGIES),
        "walk_forward": "retrospective expanding-window, parameter selection bias remains",
        "acceptance": "NOT_FOR_TRADING",
        "limitations": [
            "No actual broker execution or settlement has been confirmed.",
            "Tushare adjustment factors do not independently establish total-return dividend semantics.",
            "Data gaps require verification; missing execution opens block trading.",
            "Execution at next observed open with a fixed fee/slippage is an approximation.",
            "Previously inspected full-sample parameter choices bias retrospective walk-forward results.",
            "Historical drawdown under 25% does not guarantee a future drawdown cap."
        ],
    }
    (OUT / "self_financing_manifest.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print("SELF_FINANCING_MANIFEST " + json.dumps(metadata, ensure_ascii=False))


if __name__ == "__main__":
    main()
