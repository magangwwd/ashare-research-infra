#!/usr/bin/env python3
"""Leakage-safe BTC market-regime classifier and conservative strategy router.

Research only. No exchange connection and no order placement.

The router answers the primary research question:
"Given only information known at a completed daily close, which strategy family is appropriate for the NEXT trading session?"

Regimes:
  TREND_UP      -> V3 trend-following candidate
  RANGE         -> Conservative Half-Martingale Grid candidate
  TREND_DOWN    -> CASH / no new long exposure
  HIGH_VOL      -> CASH / risk-off
  RECOVERY      -> V3 with defensive cap
  TRANSITION    -> CASH until confirmation

Thresholds are frozen in a separate config and are not optimized on the
current sample.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class RegimeFeatures:
    date: str
    close: float
    trend_score: float
    ret30: float
    ret90: float
    ret180: float
    er60: float
    vol60: float
    vol_percentile: float | None
    atr14_pct: float
    ema200: float
    ema200_gap: float
    ema200_slope30: float
    range_pos120: float
    range_width120_pct: float


def _ema_series(values, period):
    alpha = 2.0 / (period + 1.0)
    out = []
    state = None
    for value in values:
        state = value if state is None else alpha * value + (1.0 - alpha) * state
        out.append(state)
    return out


def _percentile_rank(history, value):
    if not history:
        return None
    return sum(x <= value for x in history) / len(history)


def load_daily(path):
    rows = []
    with Path(path).open(newline="", encoding="utf8") as f:
        for r in csv.DictReader(f):
            v = {
                "date": date.fromisoformat(r["date"]),
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": float(r["volume"]),
            }
            if not all(math.isfinite(v[k]) for k in ("open", "high", "low", "close", "volume")):
                raise ValueError("non-finite OHLCV")
            if not (0 < v["low"] <= min(v["open"], v["close"])
                    <= max(v["open"], v["close"]) <= v["high"] and v["volume"] >= 0):
                raise ValueError(f"invalid OHLCV {v['date']}")
            if rows and (v["date"] - rows[-1]["date"]).days != 1:
                raise ValueError("non-contiguous or duplicate daily bars")
            rows.append(v)
    if len(rows) < 220:
        raise ValueError("regime router needs at least 220 daily bars")
    return rows


def compute_features(rows, volatility_rank_window=730):
    closes = [r["close"] for r in rows]
    ema200 = _ema_series(closes, 200)
    vol60_series = [None] * len(rows)
    atr14_series = [None] * len(rows)
    er60_series = [None] * len(rows)

    for i in range(len(rows)):
        if i >= 60:
            returns = [math.log(closes[k] / closes[k - 1]) for k in range(i - 59, i + 1)]
            vol60_series[i] = statistics.stdev(returns) * math.sqrt(365.0)
            path = sum(abs(closes[k] - closes[k - 1]) for k in range(i - 59, i + 1))
            er60_series[i] = abs(closes[i] - closes[i - 60]) / path if path > 0 else 0.0
        if i >= 14:
            trs = []
            for k in range(i - 13, i + 1):
                prev = closes[k - 1] if k else closes[k]
                trs.append(max(
                    rows[k]["high"] - rows[k]["low"],
                    abs(rows[k]["high"] - prev),
                    abs(rows[k]["low"] - prev),
                ))
            atr14_series[i] = statistics.mean(trs) / closes[i]

    features = []
    for i in range(len(rows)):
        if i < 180 or vol60_series[i] is None or atr14_series[i] is None:
            continue
        prior_vols = [
            v for v in vol60_series[max(0, i - volatility_rank_window):i]
            if v is not None and math.isfinite(v)
        ]
        close = closes[i]
        r30 = close / closes[i - 30] - 1.0
        r90 = close / closes[i - 90] - 1.0
        r180 = close / closes[i - 180] - 1.0
        score = sum(r > 0 for r in (r30, r90, r180)) / 3.0
        ema_gap = close / ema200[i] - 1.0
        ema_slope30 = ema200[i] / ema200[i - 30] - 1.0
        window = closes[i - 119:i + 1]
        lo, hi = min(window), max(window)
        width = hi - lo
        features.append(RegimeFeatures(
            date=rows[i]["date"].isoformat(),
            close=close,
            trend_score=score,
            ret30=r30,
            ret90=r90,
            ret180=r180,
            er60=er60_series[i],
            vol60=vol60_series[i],
            vol_percentile=_percentile_rank(prior_vols, vol60_series[i]),
            atr14_pct=atr14_series[i],
            ema200=ema200[i],
            ema200_gap=ema_gap,
            ema200_slope30=ema_slope30,
            range_pos120=(close - lo) / width if width > 0 else 0.5,
            range_width120_pct=width / close if close > 0 else 0.0,
        ))
    return features


def classify(f: RegimeFeatures, cfg):
    if (
        (f.vol_percentile is not None and f.vol_percentile >= cfg["high_vol_percentile"])
        or f.atr14_pct >= cfg["high_vol_atr_pct"]
    ):
        return "HIGH_VOL"

    if (
        f.trend_score >= cfg["trend_score_up"]
        and f.er60 >= cfg["trend_er_min"]
        and f.ema200_gap >= cfg["up_ema_gap_min"]
        and f.ema200_slope30 > 0
    ):
        return "TREND_UP"

    if (
        f.trend_score <= cfg["trend_score_down"]
        and f.er60 >= cfg["trend_er_min"]
        and f.ema200_gap <= cfg["down_ema_gap_max"]
        and f.ema200_slope30 < 0
    ):
        return "TREND_DOWN"

    if (
        f.er60 <= cfg["range_er_max"]
        and (f.vol_percentile is None or f.vol_percentile <= cfg["range_vol_percentile_max"])
        and abs(f.ema200_gap) <= cfg["range_ema_gap_max"]
    ):
        return "RANGE"

    if (
        f.ema200_gap > 0
        and f.trend_score >= cfg["recovery_trend_score_min"]
    ):
        return "RECOVERY"

    return "TRANSITION"


def recommendation(regime, f: RegimeFeatures, cfg):
    if regime == "TREND_UP":
        return {
            "strategy": "V3",
            "target_cap": cfg["trend_cap"],
            "allow_new_grid": False,
            "action": "TREND_FOLLOW",
        }
    if regime == "RANGE":
        spacing = max(
            cfg["grid_min_pct"] * f.close,
            min(cfg["grid_atr_multiple"] * f.atr14_pct * f.close,
                cfg["grid_max_pct"] * f.close),
        )
        return {
            "strategy": "HMG",
            "target_cap": cfg["grid_account_cap"],
            "allow_new_grid": True,
            "grid_spacing_usdt": round(spacing, 2),
            "grid_spacing_pct": round(spacing / f.close, 6),
            "max_layers": cfg["grid_max_layers"],
            "size_multipliers": cfg["grid_size_multipliers"],
            "basket_take_profit_pct": cfg["grid_basket_tp_pct"],
            "action": "CONSERVATIVE_MEAN_REVERSION",
        }
    if regime == "RECOVERY":
        return {
            "strategy": "V3_DEFENSIVE",
            "target_cap": cfg["recovery_cap"],
            "allow_new_grid": False,
            "action": "RECOVERY_WITH_REDUCED_RISK",
        }
    if regime == "HIGH_VOL":
        return {
            "strategy": "CASH",
            "target_cap": 0.0,
            "allow_new_grid": False,
            "action": "RISK_OFF",
        }
    if regime == "TREND_DOWN":
        return {
            "strategy": "CASH",
            "target_cap": 0.0,
            "allow_new_grid": False,
            "action": "NO_LONGS",
        }
    return {
        "strategy": "CASH",
        "target_cap": cfg["transition_cap"],
        "allow_new_grid": False,
        "action": "WAIT_FOR_CONFIRMATION",
    }


def smooth_regimes(features, cfg):
    """Causal state machine: emergency entry is immediate; ordinary changes
    need confirmation so the system does not churn strategies every day."""
    if not features:
        return []

    confirmed = None
    candidate = None
    candidate_count = 0
    days_since_switch = 10_000
    out = []

    for f in features:
        raw = classify(f, cfg)
        emergency = raw in {"HIGH_VOL", "TREND_DOWN"}

        if confirmed is None:
            confirmed = raw
            candidate = None
            candidate_count = 0
            days_since_switch = 0
        elif raw == confirmed:
            candidate = None
            candidate_count = 0
        elif emergency:
            confirmed = raw
            candidate = None
            candidate_count = 0
            days_since_switch = 0
        else:
            if raw != candidate:
                candidate = raw
                candidate_count = 1
            else:
                candidate_count += 1
            min_hold = (
                cfg["emergency_min_hold_days"]
                if confirmed in {"HIGH_VOL", "TREND_DOWN"}
                else cfg["min_hold_days"]
            )
            need = (
                cfg["emergency_exit_confirm_days"]
                if confirmed in {"HIGH_VOL", "TREND_DOWN"}
                else cfg["confirm_days"]
            )
            if candidate_count >= need and days_since_switch >= min_hold:
                confirmed = candidate
                candidate = None
                candidate_count = 0
                days_since_switch = 0

        out.append({
            "date": f.date,
            "raw_regime": raw,
            "confirmed_regime": confirmed,
            "strategy": recommendation(confirmed, f, cfg)["strategy"],
        })
        days_since_switch += 1

    return out


def load_config(path):
    cfg = json.loads(Path(path).read_text(encoding="utf8"))
    required = {
        "high_vol_percentile", "high_vol_atr_pct",
        "trend_score_up", "trend_score_down", "trend_er_min",
        "up_ema_gap_min", "down_ema_gap_max",
        "range_er_max", "range_vol_percentile_max", "range_ema_gap_max",
        "recovery_trend_score_min", "trend_cap", "recovery_cap",
        "transition_cap", "grid_account_cap", "grid_atr_multiple",
        "grid_min_pct", "grid_max_pct", "grid_max_layers",
        "grid_size_multipliers", "grid_basket_tp_pct",
        "confirm_days", "min_hold_days",
        "emergency_min_hold_days", "emergency_exit_confirm_days",
    }
    missing = sorted(required - set(cfg))
    if missing:
        raise ValueError("missing config keys: " + ", ".join(missing))
    return cfg


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--config", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    rows = load_daily(args.input)
    cfg = load_config(args.config)
    features = compute_features(rows)
    states = smooth_regimes(features, cfg)
    if not states:
        raise ValueError("no regime observations")

    latest = features[-1]
    latest_state = states[-1]
    latest_rec = recommendation(latest_state["confirmed_regime"], latest, cfg)
    result = {
        "research_only": True,
        "live_trading": False,
        "data_start": rows[0]["date"].isoformat(),
        "data_end": rows[-1]["date"].isoformat(),
        "latest_features": asdict(latest),
        "latest_state": latest_state,
        "latest_recommendation": latest_rec,
        "method": {
            "decision_timing": "completed UTC daily close -> next session",
            "strategy_switch_confirmation": cfg["confirm_days"],
            "risk_off_precedence": True,
            "future_data_used": False,
        },
    }
    dest = Path(args.out)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "latest_regime.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf8"
    )
    with (dest / "regime_history.csv").open("w", newline="", encoding="utf8") as f:
        writer = csv.DictWriter(f, fieldnames=list(states[0]))
        writer.writeheader()
        writer.writerows(states)
    with (dest / "feature_history.csv").open("w", newline="", encoding="utf8") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(features[0])))
        writer.writeheader()
        for row in features:
            writer.writerow(asdict(row))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
