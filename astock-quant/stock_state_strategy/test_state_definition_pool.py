from __future__ import annotations

import numpy as np
import pandas as pd

from state_definition_pool import (
    STATE_COMPONENTS,
    _calendar_flag_count,
    _calendar_observation_count,
    compute_symbol_features,
    weighted_state_score,
)


def test_calendar_counts_include_suspension_gaps() -> None:
    indices = np.array([0, 1, 2, 5, 6, 8], dtype="int32")
    counts = _calendar_observation_count(indices, 5)
    np.testing.assert_array_equal(counts, [1, 2, 3, 3, 3, 3])
    flags = np.array([False, True, False, True, False, True])
    flag_counts = _calendar_flag_count(indices, flags, 5)
    np.testing.assert_array_equal(flag_counts, [0, 1, 1, 2, 1, 2])


def test_weighted_state_score_penalizes_missing_coverage() -> None:
    components = STATE_COMPONENTS["markup"]
    frame = pd.DataFrame({name: [0.8] for name, _ in components})
    score, coverage, agreement = weighted_state_score(frame, components)
    assert np.isclose(score.iloc[0], 80.0)
    assert np.isclose(coverage.iloc[0], 1.0)
    assert np.isclose(agreement.iloc[0], 1.0)
    frame.loc[0, components[0][0]] = np.nan
    _, reduced_coverage, _ = weighted_state_score(frame, components)
    assert reduced_coverage.iloc[0] < 0.80


def _sample_bars() -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-01", periods=300)
    close = np.linspace(10.0, 15.0, len(dates))
    return pd.DataFrame(
        {
            "instrument_id": "CN.XSHE.000001",
            "vendor_symbol": "000001.SZ",
            "trade_date": dates,
            "trade_idx": np.arange(len(dates), dtype="int32"),
            "open": close * 0.995,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "turnover_cny": np.linspace(1e8, 2e8, len(dates)),
            "turnover_rate_pct": np.linspace(1.0, 1.5, len(dates)),
            "float_market_cap_cny": 1e10,
            "adjustment_factor": 1.0,
            "up_limit": close * 1.1,
            "down_limit": close * 0.9,
        }
    )


def test_historical_features_do_not_use_future_rows() -> None:
    bars = _sample_bars()
    changed = bars.copy()
    cutoff = 270
    changed.loc[cutoff + 1 :, ["open", "high", "low", "close"]] *= 3.0
    first = compute_symbol_features(bars)
    second = compute_symbol_features(changed)
    columns = [
        "price_position_250",
        "ret_20_behavior",
        "volume_ratio_5_60",
        "turnover_ratio_5_60",
        "upper_shadow_mean_20",
    ]
    np.testing.assert_allclose(
        first.loc[cutoff, columns].to_numpy(dtype=float),
        second.loc[cutoff, columns].to_numpy(dtype=float),
        equal_nan=True,
    )
