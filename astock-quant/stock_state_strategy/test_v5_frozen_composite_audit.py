from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from v5_frozen_composite_audit import (
    FROZEN_COMPOSITES,
    deterministic_top_indices,
    directed_composite_score,
    validate_frozen_specs,
)


def stability_frame() -> pd.DataFrame:
    rows = [
        {
            "target": target,
            "feature": feature,
            "direction": direction,
            "validation_pass": True,
            "directed_ic_2025": -999.0,
            "holdout_consistent": False,
        }
        for target, items in FROZEN_COMPOSITES.items()
        for feature, direction in items
    ]
    rows.append(
        {
            "target": "net_r",
            "feature": "ret_60",
            "direction": "HIGH",
            "validation_pass": False,
            "directed_ic_2025": 999.0,
            "holdout_consistent": True,
        }
    )
    return pd.DataFrame(rows)


def test_frozen_spec_matches_validation_passed_rows() -> None:
    validate_frozen_specs(stability_frame())


def test_holdout_fields_cannot_change_frozen_spec() -> None:
    first = stability_frame()
    second = first.copy()
    second["directed_ic_2025"] *= -1
    second["holdout_consistent"] = ~second["holdout_consistent"]
    validate_frozen_specs(first)
    validate_frozen_specs(second)


def test_frozen_spec_rejects_validation_drift() -> None:
    frame = stability_frame()
    frame.loc[frame["feature"] == "ret_60", "validation_pass"] = True
    with pytest.raises(RuntimeError, match="does not match"):
        validate_frozen_specs(frame)


def test_directed_equal_weight_score() -> None:
    group = pd.DataFrame(
        {
            "low_feature": [1.0, 2.0, 3.0, 4.0],
            "high_feature": [4.0, 3.0, 2.0, 1.0],
        }
    )
    score = directed_composite_score(
        group,
        (("low_feature", "LOW"), ("high_feature", "HIGH")),
    )
    assert np.all(np.diff(score) < 0)
    assert score[0] == pytest.approx(0.875)
    assert score[-1] == pytest.approx(0.125)


def test_top_selection_has_exact_size_and_stable_ties() -> None:
    scores = np.array([1.0, 1.0, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1])
    ids = np.array(["B", "A", "C", "D", "E", "F", "G", "H", "I", "J"])
    selected = deterministic_top_indices(scores, ids, fraction=0.20)
    assert selected.tolist() == [1, 0]
