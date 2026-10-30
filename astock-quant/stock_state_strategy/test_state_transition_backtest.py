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


def test_interleaved_symbols_keep_their_own_stability_and_previous_state() -> None:
    frame = pd.DataFrame({
        "instrument_id": ["B", "A", "B", "A", "B", "A"],
        "trade_date": pd.to_datetime(["2026-01-01", "2026-01-01", "2026-01-02",
                                      "2026-01-02", "2026-01-05", "2026-01-05"]),
        "raw_state": ["distribution", "lock", "distribution", "lock", "lock", "markup"],
        "defined_state": ["distribution", "lock", "distribution", "lock", "lock", "markup"],
    }, index=[20, 30, 40, 50, 60, 70])
    result = add_transitions(add_stability(frame))
    a = result[result["instrument_id"].eq("A")]
    b = result[result["instrument_id"].eq("B")]
    assert a["stable_days_5"].tolist() == [1, 2, 1]
    assert b["stable_days_5"].tolist() == [1, 2, 1]
    assert a["transition"].iloc[-1] == "lock_to_markup"
    assert b["transition"].iloc[-1] == "distribution_to_lock"
    assert a["previous_defined_state"].iloc[-1] == "lock"
    assert b["previous_defined_state"].iloc[-1] == "distribution"


def test_transitions_do_not_depend_on_row_labels_or_input_order() -> None:
    frame = pd.DataFrame({"instrument_id": ["A", "B", "A", "B"],
        "trade_date": pd.to_datetime(["2026-01-01", "2026-01-01", "2026-01-02", "2026-01-02"]),
        "defined_state": ["lock", "distribution", "markup", "lock"]})
    expected = add_transitions(frame).reset_index(drop=True)
    shuffled = frame.iloc[[3, 1, 2, 0]].copy();shuffled.index = [99, 99, 8, 8]
    actual = add_transitions(shuffled).reset_index(drop=True)
    pd.testing.assert_frame_equal(actual, expected)
