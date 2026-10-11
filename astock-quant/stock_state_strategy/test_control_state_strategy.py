from __future__ import annotations

import numpy as np
import pandas as pd

from control_state_strategy import _future_rolling, compute_symbol_behavior


def sample_bars(last_close: float = 40.0) -> pd.DataFrame:
    dates = pd.bdate_range("2020-01-01", periods=260)
    close = np.linspace(10.0, 35.0, len(dates))
    close[-1] = last_close
    return pd.DataFrame(
        {
            "vendor_symbol": "000001.SZ",
            "trade_date": dates,
            "close": close,
            "adjustment_factor": 1.0,
            "turnover_cny": np.linspace(1e8, 2e8, len(dates)),
            "turnover_rate_pct": np.linspace(1.0, 2.0, len(dates)),
        }
    )


def test_future_rolling_excludes_current_session() -> None:
    values = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    result = _future_rolling(values, 3, "mean")
    assert np.isclose(result.iloc[0], 3.0)
    assert np.isclose(result.iloc[2], 5.0)


def test_historical_feature_does_not_use_later_prices() -> None:
    baseline = sample_bars(40.0)
    changed = baseline.copy()
    cutoff = 230
    changed.loc[cutoff + 1 :, "close"] *= 3.0
    first = compute_symbol_behavior(baseline)
    second = compute_symbol_behavior(changed)
    historical = [
        "price_position_250",
        "trend_efficiency_20",
        "volume_ratio_5_60",
        "turnover_ratio_5_60",
        "amihud_20",
    ]
    np.testing.assert_allclose(
        first.loc[cutoff, historical].to_numpy(dtype=float),
        second.loc[cutoff, historical].to_numpy(dtype=float),
        equal_nan=True,
    )


def test_price_position_is_bounded() -> None:
    calculated = compute_symbol_behavior(sample_bars())
    valid = calculated["price_position_250"].dropna()
    assert valid.between(0.0, 1.0).all()
