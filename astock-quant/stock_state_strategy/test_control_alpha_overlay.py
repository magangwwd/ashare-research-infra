from __future__ import annotations

import numpy as np
import pandas as pd

from control_alpha_overlay import select_cohorts


def test_overlay_keeps_equal_count_and_applies_control_filter() -> None:
    size = 20
    frame = pd.DataFrame(
        {
            "ann_date": pd.Timestamp("2024-01-01"),
            "vendor_symbol": [f"{value:06d}.SZ" for value in range(size)],
            "prediction_controls_only": np.arange(size, dtype=float),
            "discovery_selected_behavior_score": np.arange(size, 0, -1, dtype=float),
            "future_20d_net_return": np.linspace(-0.1, 0.1, size),
        }
    )
    selected = select_cohorts(frame, minimum=20)
    counts = selected.groupby("strategy").size()
    assert counts["alpha_top20"] == counts["alpha_top20_with_control_top50"] == 4
    overlay = selected[selected["strategy"] == "alpha_top20_with_control_top50"]
    assert overlay["discovery_selected_behavior_score"].min() >= frame[
        "discovery_selected_behavior_score"
    ].median()
