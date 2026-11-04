"""Retrospective, causally selected expanding-window ETF walk-forward study.

Parameters were explored previously, so these folds are NOT independent
prospective out-of-sample evidence. Selection within each fold only uses
earlier dates, and its boundaries are fixed in the source.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd

# Expanding training period, consecutive nonoverlapping evaluation periods.
FOLDS = (
    ("2019-01-01", "2021-12-31"),
    ("2022-01-01", "2023-12-31"),
    ("2024-01-01", "2026-12-31"),
)
CANDIDATES = (
    "regime_switch_ultralow",
    "regime_switch_lowvol",
    "regime_switch_midvol",
    "regime_switch_gold_vol8",
    "regime_switch_cap75",
)
MAX_HISTORICAL_DRAWDOWN = 0.25


def score(equity: pd.Series) -> dict:
    """Summarize a nonempty net-equity slice with reproducible conventions."""
    if len(equity) < 2 or not (equity > 0).all():
        raise ValueError("Positive net equity with at least two dates required")
    normalized = equity / float(equity.iloc[0])
    daily = normalized.pct_change(fill_method=None).fillna(0.0)
    elapsed_years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1.0 / 365.25)
    sigma = float(daily.std(ddof=1))
    return {
        "total_return": float(normalized.iloc[-1] - 1),
        "cagr": float(normalized.iloc[-1] ** (1 / elapsed_years) - 1),
        "max_drawdown": float((normalized / normalized.cummax() - 1).min()),
        "sharpe_rf0": float(daily.mean() / sigma * np.sqrt(252)) if sigma > 0 else 0.0,
        "sessions": int(len(equity)),
    }


def select_on_history(equities: dict[str, pd.Series], train_end: str, candidates=CANDIDATES):
    """Pick by eligible training Sharpe; never use a date after train_end."""
    training, qualifying = [], []
    for name in candidates:
        if name not in equities:
            raise ValueError(f"Missing candidate: {name}")
        history = equities[name].loc[:pd.Timestamp(train_end)]
        if len(history) < 500:
            row = {"strategy": name, "training_end": train_end,
                   "eligible": False, "reason": "INSUFFICIENT_HISTORY", "sessions": len(history)}
        else:
            row = {"strategy": name, "training_end": train_end, **score(history)}
            eligible = row["max_drawdown"] >= -MAX_HISTORICAL_DRAWDOWN and row["cagr"] > 0
            row["eligible"] = bool(eligible)
            row["reason"] = "ELIGIBLE" if eligible else "FAILED_TRAIN_RISK_OR_RETURN"
            if eligible:
                qualifying.append(row)
        training.append(row)
    qualifying.sort(key=lambda row: (-row["sharpe_rf0"], -row["cagr"], row["strategy"]))
    return (qualifying[0]["strategy"] if qualifying else "CASH"), training


def walk_forward(equities: dict[str, pd.Series], output_dir: Path,
                 folds=FOLDS, candidates=CANDIDATES):
    """Produce fold records, train-score audit, and net-equity stitched test path."""
    dates = next(iter(equities.values())).index
    if not dates.is_monotonic_increasing or dates.has_duplicates:
        raise ValueError("Equity dates must be sorted and unique")
    if any(not dates.equals(equities[name].index) for name in candidates):
        raise ValueError("Candidate equity date indexes are not aligned")
    fold_rows, training_rows, daily_chunks = [], [], []
    previous_test_end = None
    for start_text, end_text in folds:
        start, end = pd.Timestamp(start_text), pd.Timestamp(end_text)
        if previous_test_end is not None and start <= previous_test_end:
            raise ValueError("Test folds must not overlap")
        train_dates = dates[dates < start]
        test_dates = dates[(dates >= start) & (dates <= end)]
        if len(train_dates) < 500 or len(test_dates) < 20:
            raise ValueError("Not enough training or testing sessions")
        train_end = train_dates[-1]
        chosen, train_scores = select_on_history(equities, train_end.isoformat(), candidates)
        for record in train_scores:
            training_rows.append({"test_window_start": start_text, **record})
        if chosen == "CASH":
            test_returns = pd.Series(0.0, index=test_dates)
        else:
            # The prior-day equity value includes the first test-day P&L.
            full_daily = equities[chosen].pct_change(fill_method=None)
            test_returns = full_daily.loc[test_dates]
            if test_returns.isna().any():
                raise ValueError("NaN in test returns")
        test_equity = (1 + test_returns).cumprod()
        test_score = score(pd.concat([
            pd.Series([1.0], index=[train_end]),
            test_equity
        ]))
        fold_rows.append({
            "train_start": dates[0].date().isoformat(),
            "train_end": train_end.date().isoformat(),
            "test_start": test_dates[0].date().isoformat(),
            "test_end": test_dates[-1].date().isoformat(),
            "selected_strategy": chosen,
            **test_score
        })
        daily_chunks.append(pd.DataFrame({
            "date": test_dates, "strategy": chosen, "net_daily_return": test_returns.values
        }))
        previous_test_end = end
    history = pd.concat(daily_chunks, ignore_index=True)
    if history.date.duplicated().any():
        raise ValueError("Walk-forward evaluation dates overlap")
    history["net_equity"] = (1 + history.net_daily_return).cumprod()
    composite_equity = pd.Series(
        np.r_[1.0, history.net_equity.values],
        index=pd.DatetimeIndex([dates[dates < history.date.iloc[0]][-1]]).append(pd.DatetimeIndex(history.date))
    )
    summary = {
        "method": "expanding historical training selection, sequential nonoverlapping holdout periods",
        "max_drawdown_training_filter": MAX_HISTORICAL_DRAWDOWN,
        "selection": "highest Sharpe on past-only training, subject to positive CAGR and drawdown limit; otherwise cash",
        "tested_candidates": list(candidates),
        "fold_count": len(fold_rows),
        "folds": fold_rows,
        "stitched_net_holdout": score(composite_equity),
        "acceptance": "RETROSPECTIVE_EXPLORATORY_NOT_FOR_TRADING",
        "caveat": "Strategies/parameters were already examined using full historical data; post-selection bias remains. Data gap and total-return semantics still require audit."
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(fold_rows).to_csv(output_dir / "walk_forward_folds.csv", index=False)
    pd.DataFrame(training_rows).to_csv(output_dir / "walk_forward_training_scores.csv", index=False)
    history.to_csv(output_dir / "walk_forward_portfolio_equity.csv", index=False)
    (output_dir / "walk_forward_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary
