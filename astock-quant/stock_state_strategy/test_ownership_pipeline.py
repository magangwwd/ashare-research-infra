from __future__ import annotations

import numpy as np
import pandas as pd

from ownership_pipeline import (
    attach_forward_labels,
    build_composition_updates,
    build_event_timeline,
    build_holder_updates,
    classify_holder,
)


def test_holder_classifier_uses_mutually_exclusive_classes() -> None:
    assert classify_holder("自然人", "张三") == ("large_individual", "HIGH")
    assert classify_holder("一般企业", "香港中央结算有限公司")[0] == "northbound"
    assert classify_holder("开放式投资基金", "某证券投资基金")[0] == "public_fund"
    assert classify_holder("其他金融产品", "某私募证券投资基金")[0] == "private_fund"
    assert classify_holder("保险投资组合", "某保险产品")[0] == "insurance"


def test_point_in_time_updates_and_changes() -> None:
    holder_raw = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000001.SZ"],
            "ann_date": ["20210110", "20210410"],
            "end_date": ["20201231", "20210331"],
            "holder_num": [1000, 800],
        }
    )
    top_raw = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"] * 4,
            "ann_date": ["20210110", "20210110", "20210410", "20210410"],
            "end_date": ["20201231", "20201231", "20210331", "20210331"],
            "holder_name": ["某基金", "张三", "某基金", "张三"],
            "hold_amount": [100, 50, 120, 40],
            "hold_ratio": [10, 5, 12, 4],
            "hold_float_ratio": [10, 5, 12, 4],
            "hold_change": [0, 0, 20, -10],
            "holder_type": ["开放式投资基金", "自然人"] * 2,
        }
    )
    holder = build_holder_updates(holder_raw)
    composition, _ = build_composition_updates(top_raw)
    events = build_event_timeline(holder, composition)

    assert np.isclose(holder.iloc[1]["holder_num_change_rate"], -0.2)
    assert np.isclose(composition.iloc[0]["identified_institution_pct"], 10.0)
    assert np.isclose(composition.iloc[1]["identified_institution_change_pp"], 2.0)
    assert np.isclose(composition.iloc[1]["dispersed_float_proxy_pct"], 84.0)
    assert events["new_holder_disclosure"].all()
    assert events["new_composition_disclosure"].all()


def test_fullwidth_separator_duplicate_is_removed() -> None:
    top_raw = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000001.SZ"],
            "ann_date": ["20210110", "20210110"],
            "end_date": ["20201231", "20201231"],
            "holder_name": ["某基金-银行-一号计划", "某基金－银行－一号计划"],
            "hold_amount": [100, 100],
            "hold_ratio": [10, 10],
            "hold_float_ratio": [10, 10],
            "hold_change": [0, 0],
            "holder_type": ["开放式投资基金", "开放式投资基金"],
        }
    )
    composition, detail = build_composition_updates(top_raw)
    assert len(detail) == 1
    assert composition.loc[0, "top10_float_pct"] == 10
    assert bool(composition.loc[0, "composition_quality_valid"])


def test_event_timeline_never_backfills_future_disclosure() -> None:
    holder = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "ann_date": pd.to_datetime(["2021-04-10"]),
            "holder_report_end": pd.to_datetime(["2021-03-31"]),
            "holder_num": [800],
            "holder_num_change": [np.nan],
            "holder_num_change_rate": [np.nan],
        }
    )
    composition = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "ann_date": pd.to_datetime(["2021-01-10"]),
            "composition_report_end": pd.to_datetime(["2020-12-31"]),
            "top10_float_pct": [20.0],
        }
    )
    events = build_event_timeline(holder, composition)
    assert pd.isna(events.iloc[0]["holder_num"])
    assert events.iloc[1]["holder_num"] == 800
    assert events.iloc[1]["top10_float_pct"] == 20.0


def test_forward_label_enters_after_announcement_and_applies_costs() -> None:
    dates = pd.to_datetime(["2021-01-04", "2021-01-05", "2021-01-06"])
    events = pd.DataFrame(
        {"vendor_symbol": ["000001.SZ"], "ann_date": pd.to_datetime(["2021-01-04"])}
    )
    panel = pd.DataFrame(
        {
            "vendor_symbol": ["000001.SZ"] * 3,
            "trade_date": dates,
            "open": [10.0, 10.0, 11.0],
            "high": [10.0, 10.5, 11.5],
            "low": [10.0, 9.9, 10.8],
            "close": [10.0, 10.2, 11.0],
            "adjustment_factor": [1.0, 1.0, 1.0],
            "up_limit": [11.0, 11.0, 11.22],
            "down_limit": [9.0, 9.0, 9.18],
            "is_quality_valid": [True, True, True],
        }
    )
    labelled = attach_forward_labels(
        events,
        panel,
        dates.to_numpy(dtype="datetime64[ns]"),
        [1],
        0.001,
        0.002,
    )
    expected = (1 - 0.001) * 1.1 * (1 - 0.002) - 1
    assert labelled.loc[0, "entry_date_1"] == pd.Timestamp("2021-01-05")
    assert labelled.loc[0, "exit_date_1"] == pd.Timestamp("2021-01-06")
    assert np.isclose(labelled.loc[0, "future_1d_net_return"], expected)


def test_one_price_limit_up_is_not_buyable() -> None:
    dates = pd.to_datetime(["2021-01-04", "2021-01-05", "2021-01-06"])
    events = pd.DataFrame(
        {"vendor_symbol": ["000001.SZ"], "ann_date": pd.to_datetime(["2021-01-04"])}
    )
    panel = pd.DataFrame(
        {
            "vendor_symbol": ["000001.SZ"] * 3,
            "trade_date": dates,
            "open": [10.0, 11.0, 11.5],
            "high": [10.0, 11.0, 12.0],
            "low": [10.0, 11.0, 11.0],
            "close": [10.0, 11.0, 11.8],
            "adjustment_factor": [1.0, 1.0, 1.0],
            "up_limit": [11.0, 11.0, 12.1],
            "down_limit": [9.0, 9.0, 9.9],
            "is_quality_valid": [True, True, True],
        }
    )
    labelled = attach_forward_labels(
        events, panel, dates.to_numpy(dtype="datetime64[ns]"), [1], 0.0, 0.0
    )
    assert pd.isna(labelled.loc[0, "future_1d_net_return"])


def test_one_price_limit_down_delays_exit() -> None:
    dates = pd.to_datetime(
        ["2021-01-04", "2021-01-05", "2021-01-06", "2021-01-07"]
    )
    events = pd.DataFrame(
        {"vendor_symbol": ["000001.SZ"], "ann_date": pd.to_datetime(["2021-01-04"])}
    )
    panel = pd.DataFrame(
        {
            "vendor_symbol": ["000001.SZ"] * 4,
            "trade_date": dates,
            "open": [10.0, 10.0, 9.0, 8.5],
            "high": [10.0, 10.2, 9.0, 8.8],
            "low": [10.0, 9.9, 9.0, 8.0],
            "close": [10.0, 10.1, 9.0, 8.5],
            "adjustment_factor": [1.0] * 4,
            "up_limit": [11.0, 11.0, 11.11, 9.9],
            "down_limit": [9.0, 9.0, 9.0, 8.1],
            "limit_status": [0, 1, 6, 4],
            "is_quality_valid": [True] * 4,
        }
    )
    labelled = attach_forward_labels(
        events, panel, dates.to_numpy(dtype="datetime64[ns]"), [1], 0.0, 0.0
    )
    assert labelled.loc[0, "exit_date_1"] == pd.Timestamp("2021-01-07")
    assert labelled.loc[0, "exit_delay_sessions_1"] == 1
    assert np.isclose(labelled.loc[0, "future_1d_net_return"], -0.15)
