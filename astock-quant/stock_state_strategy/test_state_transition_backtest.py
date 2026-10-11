from __future__ import annotations

import numpy as np
import pandas as pd

from state_transition_backtest import add_stability, add_transitions


def test_stability_counts_current_state_in_last_five_rows() -> None:
    frame = pd.DataFrame(
        {
            "instrument_id": "A",
            "trade_date": pd.bdate_range("2026-01-01", periods=6),
            "raw_state": ["lock", "lock", "markup", "lock", "markup", "markup"],
        }
    )
    result = add_stability(frame)
    assert result["stable_days_5"].tolist() == [1, 2, 1, 3, 2, 3]


def test_transition_uses_previous_defined_state_and_age_limit() -> None:
    frame = pd.DataFrame(
        {
            "instrument_id": "A",
            "trade_date": pd.to_datetime(
                ["2026-01-01", "2026-01-02", "2026-01-05", "2026-03-01"]
            ),
            "defined_state": ["lock", "uncertain", "markup", "distribution"],
        }
    )
    result = add_transitions(frame)
    assert result.loc[2, "transition"] == "lock_to_markup"
    assert result.loc[3, "transition"] == "none"
    assert np.isclose(result.loc[2, "transition_gap_days"], 4)
