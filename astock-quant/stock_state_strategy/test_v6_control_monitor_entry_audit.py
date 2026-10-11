from __future__ import annotations

import numpy as np
import pandas as pd

from v6_control_monitor_entry_audit import (
    attach_control_pool,
    build_control_updates,
    trade_statistics,
)


def test_control_updates_select_exact_top_twenty_percent() -> None:
    events = pd.DataFrame(
        {
            "ann_date": pd.Timestamp("2024-01-01"),
            "vendor_symbol": [f"{value:06d}.SZ" for value in range(20)],
            "discovery_selected_behavior_score": np.arange(20, dtype=float),
        }
    )
    instruments = pd.DataFrame(
        {
            "vendor_symbol": events["vendor_symbol"],
            "instrument_id": [f"ID{value}" for value in range(20)],
        }
    )
    updates = build_control_updates(events, instruments, minimum_cohort=20)
    assert updates["control_top20"].sum() == 4
    assert set(updates.loc[updates["control_top20"], "vendor_symbol"]) == {
        "000016.SZ",
        "000017.SZ",
        "000018.SZ",
        "000019.SZ",
    }


def test_monitor_uses_latest_disclosure_and_expires() -> None:
    samples = pd.DataFrame(
        {
            "instrument_id": ["A", "A", "A", "A"],
            "signal_date": pd.to_datetime(
                ["2024-01-09", "2024-01-10", "2024-02-01", "2024-07-01"]
            ),
        }
    )
    updates = pd.DataFrame(
        {
            "instrument_id": ["A", "A"],
            "ann_date": pd.to_datetime(["2024-01-10", "2024-02-01"]),
            "control_top20": [True, False],
            "discovery_selected_behavior_score": [0.8, 0.2],
            "control_rank_percentile": [0.9, 0.1],
        }
    )
    result = attach_control_pool(samples, updates, maximum_age_days=120)
    assert result["control_pool_top20"].tolist() == [False, True, False, False]


def test_trade_statistics_profit_factor() -> None:
    frame = pd.DataFrame(
        {
            "v5_net_return": [0.1, -0.05, 0.03],
            "v5_net_r": [1.0, -0.5, 0.3],
            "v5_tail_loss_r": [0.2, 0.7, 0.1],
        }
    )
    stats = trade_statistics(frame)
    assert np.isclose(stats["profit_factor"], 2.6)
    assert np.isclose(stats["payoff_ratio"], 1.3)
