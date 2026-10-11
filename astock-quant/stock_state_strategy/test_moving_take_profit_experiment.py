from __future__ import annotations

import numpy as np
import pandas as pd

from moving_take_profit_experiment import simulate_trailing_exit


def market(rows: list[tuple[float, float, float, int]]) -> pd.DataFrame:
    """Build adjusted open/low/close bars indexed by trading-session number."""
    return pd.DataFrame(
        rows,
        columns=["adj_open", "adj_low", "adj_close", "limit_status"],
        index=pd.Index(range(1, len(rows) + 1), name="trade_idx"),
    )


def test_close_activation_only_changes_the_following_session_floor() -> None:
    stock = market(
        [
            (100.0, 99.0, 100.0, 0),
            (100.0, 99.0, 114.0, 0),
            # This bar reaches the +1.5R activation at its close.  Its low must
            # not be tested against a trail that did not exist before the bar.
            (114.0, 99.0, 116.0, 0),
            (116.0, 104.0, 110.0, 0),
        ]
    )
    result = simulate_trailing_exit(stock, signal_idx=0, stop_pct=0.10, horizon=3)
    assert result is not None
    assert result.activation_idx == 3
    assert result.exit_idx == 4
    assert result.reason == "TRAIL_INTRADAY"
    assert np.isclose(result.exit_scale, 106.0)


def test_entry_close_can_activate_trail_for_next_session() -> None:
    stock = market(
        [
            (100.0, 99.0, 116.0, 0),
            (110.0, 105.0, 108.0, 0),
        ]
    )
    result = simulate_trailing_exit(stock, signal_idx=0, stop_pct=0.10, horizon=1)
    assert result is not None
    assert result.activated
    assert result.activation_idx == 1
    assert result.reason == "TRAIL_INTRADAY"
    assert np.isclose(result.exit_scale, 106.0)


def test_hard_stop_gap_has_priority_after_trail_activation() -> None:
    stock = market(
        [
            (100.0, 99.0, 116.0, 0),
            (89.0, 88.0, 89.0, 0),
        ]
    )
    result = simulate_trailing_exit(stock, signal_idx=0, stop_pct=0.10, horizon=1)
    assert result is not None
    assert result.reason == "GAP_STOP"
    assert np.isclose(result.exit_scale, 89.0)


def test_untradeable_limit_down_defers_trailing_exit() -> None:
    stock = market(
        [
            (100.0, 99.0, 116.0, 0),
            (100.0, 100.0, 100.0, 6),
            (104.0, 103.0, 105.0, 0),
        ]
    )
    result = simulate_trailing_exit(stock, signal_idx=0, stop_pct=0.10, horizon=2)
    assert result is not None
    assert result.exit_idx == 3
    assert result.reason == "TRAIL_GAP_OPEN"
    assert np.isclose(result.exit_scale, 104.0)


def test_entry_day_hard_stop_touch_exits_at_next_sellable_open() -> None:
    stock = market(
        [
            (100.0, 89.0, 95.0, 0),
            (94.0, 92.0, 93.0, 0),
        ]
    )
    result = simulate_trailing_exit(stock, signal_idx=0, stop_pct=0.10, horizon=1)
    assert result is not None
    assert result.reason == "ENTRY_DAY_STOP_NEXT_OPEN"
    assert np.isclose(result.exit_scale, 94.0)


def test_distribution_shadow_executes_at_open_before_intraday_trail() -> None:
    stock = market(
        [
            (100.0, 99.0, 116.0, 0),
            (110.0, 104.0, 108.0, 0),
        ]
    )
    result = simulate_trailing_exit(
        stock,
        signal_idx=0,
        stop_pct=0.10,
        horizon=1,
        distribution_indices=np.array([1], dtype="int32"),
        use_distribution_exit=True,
    )
    assert result is not None
    assert result.reason == "DISTRIBUTION_NEXT_OPEN"
    assert np.isclose(result.exit_scale, 110.0)
