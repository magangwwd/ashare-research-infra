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


def test_clean_market_rejects_silent_key_loss():
    from research_engine.data import CleanResearchData

    dates = [date(2026, 1, 5), date(2026, 1, 6)]
    daily = pd.DataFrame({"security_id": ["A", "A"], "trade_date": dates, "close": [10., 11.]})
    factor = pd.DataFrame({"security_id": ["A"], "trade_date": [dates[0]], "adj_factor": [1.]})
    basic = pd.DataFrame({"security_id": ["A", "A"], "trade_date": dates, "turnover_rate": [1., 1.]})
    source = {"daily": daily, "adj_factor": factor, "daily_basic": basic}
    data = object.__new__(CleanResearchData)
    data.load = lambda name: source[name]

    import pytest
    with pytest.raises(ValueError, match="core key coverage mismatch"):
        data.market()


def test_clean_market_allows_matching_keys_even_without_suspended_day():
    from research_engine.data import CleanResearchData

    day = date(2026, 1, 5)
    daily = pd.DataFrame({"security_id": ["A"], "trade_date": [day], "close": [10.]})
    factor = pd.DataFrame({"security_id": ["A"], "trade_date": [day], "adj_factor": [1.]})
    basic = pd.DataFrame({"security_id": ["A"], "trade_date": [day], "turnover_rate": [1.]})
    source = {"daily": daily, "adj_factor": factor, "daily_basic": basic}
    data = object.__new__(CleanResearchData)
    data.load = lambda name: source[name]
    assert len(data.market()) == 1
