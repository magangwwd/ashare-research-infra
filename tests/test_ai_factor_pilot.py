from datetime import date

import numpy as np
import pandas as pd

from research_engine.ai_factor_pilot import (
    AI_FEATURES,
    add_ai_candidate_features,
    attach_five_session_label,
)


def _panel(periods=70):
    dates = list(pd.bdate_range("2026-01-01", periods=periods).date)
    frame = pd.DataFrame({
        "security_id": ["A"] * periods,
        "trade_date": dates,
        "adj_close": [100.0 + 0.4 * i + np.sin(i) for i in range(periods)],
        "adj_open": [101.0 + 0.4 * i for i in range(periods)],
        "vol": [1000.0 + 20 * (i % 5) for i in range(periods)],
    })
    return frame, dates


def test_candidate_features_do_not_see_future_data():
    market, _ = _panel()
    before = add_ai_candidate_features(market)
    changed = market.copy()
    changed.loc[changed.index[-1], ["adj_close", "vol"]] = [9999.0, 9999.0]
    after = add_ai_candidate_features(changed)
    pd.testing.assert_frame_equal(before.iloc[:-1][AI_FEATURES], after.iloc[:-1][AI_FEATURES])
    assert before[AI_FEATURES].notna().any().all()


def test_label_uses_global_session_open_t_plus_1_and_t_plus_6():
    market, dates = _panel(periods=12)
    panel = attach_five_session_label(market, dates)
    first = panel.loc[panel["trade_date"] == dates[0]].iloc[0]
    assert first["entry_date"] == dates[1]
    assert first["exit_date"] == dates[6]
    assert first["forward_return_5s"] == market.loc[6, "adj_open"] / market.loc[1, "adj_open"] - 1
    assert pd.isna(panel.loc[panel["trade_date"] == dates[-1], "label_up_5s"].iloc[0])


def test_missing_exact_exit_open_must_not_shift_to_later_quote():
    market, dates = _panel(periods=12)
    market = market.loc[market["trade_date"] != dates[6]].copy()
    panel = attach_five_session_label(market, dates)
    row = panel.loc[panel["trade_date"] == dates[0]].iloc[0]
    assert pd.isna(row["forward_return_5s"])
    assert pd.isna(row["label_up_5s"])
