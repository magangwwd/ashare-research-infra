"""Isolated preregistered five-session AI-factor pilot (research only).

No Task01 writes, live signals, trading, or automatic model optimization.
Inputs must be an accepted Task01 Clean snapshot with Task02 research readiness.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .data import CleanResearchData
from .factors import add_engineering_factors
from .prices import add_adjusted_prices
from .readiness import readiness_report
from .universe import filter_historical_universe
from .walkforward import walk_forward_splits

HORIZON = 5
PURGE_SESSIONS = HORIZON + 1  # t+1 entry, t+6 exit
BASE_FEATURES = ["momentum_5d", "momentum_20d", "momentum_60d", "volatility_20d", "volume_ratio_20d"]
AI_FEATURES = ["path_efficiency_20d", "volume_signed_pressure_10d", "range_recovery_20d"]


def add_ai_candidate_features(market: pd.DataFrame) -> pd.DataFrame:
    """Three frozen hypotheses; each depends only on current/past data."""
    frame = market.sort_values(["security_id", "trade_date"], kind="stable").copy()
    g = frame.groupby("security_id", sort=False)
    ret = g["adj_close"].pct_change(fill_method=None)
    abs_path = ret.abs().groupby(frame["security_id"], sort=False).transform(
        lambda s: s.rolling(20, min_periods=20).sum()
    )
    net = g["adj_close"].pct_change(20, fill_method=None).abs()
    frame["path_efficiency_20d"] = net.div(abs_path.replace(0, np.nan))

    volume_mean = g["vol"].transform(lambda s: s.rolling(20, min_periods=20).mean())
    volume_ratio = frame["vol"].div(volume_mean.replace(0, np.nan))
    signed_pressure = np.sign(ret) * np.log1p(volume_ratio.clip(lower=0))
    frame["volume_signed_pressure_10d"] = signed_pressure.groupby(
        frame["security_id"], sort=False
    ).transform(lambda s: s.rolling(10, min_periods=10).mean())

    low = g["adj_close"].transform(lambda s: s.rolling(20, min_periods=20).min())
    high = g["adj_close"].transform(lambda s: s.rolling(20, min_periods=20).max())
    frame["range_recovery_20d"] = (frame["adj_close"] - low).div((high - low).replace(0, np.nan))
    return frame.sort_values(["trade_date", "security_id"], kind="stable").reset_index(drop=True)


def attach_five_session_label(frame: pd.DataFrame, session_dates: list) -> pd.DataFrame:
    """Exact global-session label: buy at t+1 open, sell at t+6 open.

    Missing either quote -> missing target, never shift to a later quote.
    This is an executable-price proxy; limit locks and impact are not modeled.
    """
    dates = sorted(set(pd.to_datetime(session_dates).date))
    mapping = {day: (dates[i + 1], dates[i + 6]) for i, day in enumerate(dates[:-6])}
    result = frame.copy()
    result["trade_date"] = pd.to_datetime(result["trade_date"]).dt.date
    result["entry_date"] = result["trade_date"].map(lambda d: mapping.get(d, (None, None))[0])
    result["exit_date"] = result["trade_date"].map(lambda d: mapping.get(d, (None, None))[1])
    opens = result[["security_id", "trade_date", "adj_open"]].drop_duplicates(["security_id", "trade_date"])
    result = result.merge(opens.rename(columns={"trade_date": "entry_date", "adj_open": "entry_open"}),
                          on=["security_id", "entry_date"], how="left", validate="many_to_one")
    result = result.merge(opens.rename(columns={"trade_date": "exit_date", "adj_open": "exit_open"}),
                          on=["security_id", "exit_date"], how="left", validate="many_to_one")
    result["forward_return_5s"] = result["exit_open"].div(result["entry_open"].replace(0, np.nan)) - 1
    result.loc[(result["entry_open"] <= 0) | (result["exit_open"] <= 0), "forward_return_5s"] = np.nan
    result["label_up_5s"] = (result["forward_return_5s"] > 0).astype(float)
    result.loc[result["forward_return_5s"].isna(), "label_up_5s"] = np.nan
    return result


def prepare_panel(data: CleanResearchData) -> pd.DataFrame:
    market = data.market()
    master = data.master()
    calendar = data.calendar()
    sessions = sorted(set(calendar.loc[
        calendar["exchange"].isin(["SSE", "SZSE"]) & calendar["is_open"].eq(1), "calendar_date"
    ].tolist()))
    panel = add_adjusted_prices(market)
    panel = add_engineering_factors(panel, momentum_windows=(5, 20, 60))
    panel = add_ai_candidate_features(panel)
    panel = attach_five_session_label(panel, sessions)
    panel = filter_historical_universe(panel, master, min_listing_days=250)
    return panel


def _rank_ic(frame: pd.DataFrame, score: str) -> float | None:
    values = []
    for _, group in frame.groupby("trade_date"):
        subset = group[[score, "forward_return_5s"]].dropna()
        if len(subset) >= 10 and subset[score].nunique() > 1 and subset["forward_return_5s"].nunique() > 1:
            values.append(subset[score].corr(subset["forward_return_5s"], method="spearman"))
    return float(np.mean(values)) if values else None


def compare_models(panel: pd.DataFrame, train_sessions: int = 500,
                   validation_sessions: int = 100, test_sessions: int = 100) -> dict:
    """Train fixed LightGBM models; compare paired OOS scores on identical rows.

    Validation is reported but never used to search features, thresholds or params.
    Test dates are distinct; PURGE_SESSIONS protects labels at split boundaries.
    """
    from lightgbm import LGBMClassifier  # optional, research-only dependency

    cols = BASE_FEATURES + AI_FEATURES
    usable = panel.dropna(subset=["label_up_5s", "forward_return_5s", *cols]).copy()
    dates = sorted(usable["trade_date"].unique())
    splits = walk_forward_splits(dates, train_sessions=train_sessions,
                                 validation_sessions=validation_sessions,
                                 test_sessions=test_sessions, step_sessions=test_sessions)
    if not splits:
        raise ValueError("not enough date coverage for chronological train/validation/test splits")
    outputs = []
    for fold, split in enumerate(splits, start=1):
        train_dates = set(split.train[:-PURGE_SESSIONS])
        valid_dates = set(split.validation[:-PURGE_SESSIONS])
        test_dates = set(split.test)
        train = usable.loc[usable["trade_date"].isin(train_dates)]
        valid = usable.loc[usable["trade_date"].isin(valid_dates)]
        test = usable.loc[usable["trade_date"].isin(test_dates)]
        if train["label_up_5s"].nunique() != 2 or min(len(train), len(valid), len(test)) < 50:
            raise ValueError(f"fold {fold}: insufficient class diversity or eligible samples")
        for arm, features in (("A_existing", BASE_FEATURES), ("B_AI_plus_existing", cols)):
            model = LGBMClassifier(n_estimators=120, learning_rate=0.05, num_leaves=31,
                                   min_child_samples=40, random_state=42, n_jobs=2, verbosity=-1)
            model.fit(train[features], train["label_up_5s"].astype(int))
            for split_name, section in (("validation", valid), ("test", test)):
                scored = section[["trade_date", "forward_return_5s", "label_up_5s"]].copy()
                scored["prob_up"] = model.predict_proba(section[features])[:, 1]
                brier = float(((scored["prob_up"] - scored["label_up_5s"]) ** 2).mean())
                outputs.append({"fold": fold, "arm": arm, "split": split_name,
                                "n": len(scored), "brier": brier, "mean_rank_ic": _rank_ic(scored, "prob_up")})
    return {"status": "exploratory_research_not_trade_ready", "horizon": HORIZON,
            "frozen_ai_features": AI_FEATURES, "results": outputs,
            "limitations": ["not an executed portfolio backtest", "missing historical daily ST/limit/suspension execution guards may bias results",
                            "no trading profit claims from model accuracy metrics"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="A-share AI factor controlled comparison")
    parser.add_argument("--root", default=".")
    parser.add_argument("--output", default="metadata/ai_factor_pilot_v1.json")
    args = parser.parse_args()
    output = Path(args.output)
    if not output.is_absolute():
        output = Path(args.root) / output
    try:
        data = CleanResearchData(args.root)
        readiness = readiness_report(data)
        if not readiness["levels"]["research"]["ready"]:
            report = {"status": "blocked_insufficient_contiguous_history", "readiness": readiness}
            code = 2
        else:
            report = compare_models(prepare_panel(data))
            report["data_as_of"] = data.acceptance.get("as_of")
            report["readiness"] = readiness
            code = 0
    except Exception as exc:
        report = {"status": "blocked_error", "error": f"{type(exc).__name__}: {exc}"}
        code = 2
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, default=str))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
