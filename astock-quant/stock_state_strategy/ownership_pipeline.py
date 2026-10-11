"""Build point-in-time ownership features and executable forward labels."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from data_source_paths import resolve_project_path


ROOT = Path(__file__).resolve().parent
COMPONENT_CLASSES = (
    "public_fund",
    "private_fund",
    "insurance",
    "social_security",
    "securities_asset_mgmt",
    "northbound",
    "other_financial",
    "state_strategic",
    "corporate_strategic",
    "large_individual",
    "other_identified",
)
INSTITUTION_CLASSES = (
    "public_fund",
    "private_fund",
    "insurance",
    "social_security",
    "securities_asset_mgmt",
    "northbound",
    "other_financial",
)
_COMPOSITION_BASES = (
    "identified_institution",
    *COMPONENT_CLASSES,
    "dispersed_float_proxy",
    "top10_float",
)
COMPOSITION_FACTORS = (
    *tuple(
        name
        for base in _COMPOSITION_BASES
        for name in (f"{base}_pct", f"{base}_change_pp")
    ),
    "top10_hhi",
    "top10_hhi_change",
    "institution_holder_count",
    "institution_holder_count_change",
)
HOLDER_FACTORS = (
    "holder_num",
    "holder_num_change_rate",
    "average_holding_shares",
    "average_holding_value_cny",
)


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def resolve(path_text: str) -> Path:
    return resolve_project_path(ROOT, path_text)


def normalize_text(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip().upper().replace(" ", "")


def canonical_holder_name(value: object) -> str:
    text = normalize_text(value)
    translation = str.maketrans(
        {
            "－": "-",
            "—": "-",
            "–": "-",
            "﹣": "-",
            "（": "(",
            "）": ")",
            "·": ".",
            "•": ".",
        }
    )
    return text.translate(translation)


def classify_holder(holder_type: object, holder_name: object) -> tuple[str, str]:
    """Return a mutually exclusive public-disclosure class and confidence."""
    holder_type_text = normalize_text(holder_type)
    name = normalize_text(holder_name)

    if "自然人" in holder_type_text:
        return "large_individual", "HIGH"
    if "香港中央结算" in name or "HKSCC" in name:
        return "northbound", "HIGH"
    if "社保" in holder_type_text or "社保基金" in name or "养老金" in name:
        return "social_security", "HIGH"
    if "保险" in holder_type_text or "保险产品" in name or "人寿保险" in name:
        return "insurance", "HIGH"
    if "私募" in name:
        return "private_fund", "HIGH"
    if "开放式投资基金" in holder_type_text:
        return "public_fund", "HIGH"
    if (
        "证券投资基金" in name
        or "交易型开放式指数" in name
        or name.endswith("ETF")
    ) and "私募" not in name:
        return "public_fund", "MEDIUM"
    if (
        "证券公司" in holder_type_text
        or "基金专户" in holder_type_text
        or "资产管理公司" in holder_type_text
        or "券商集合" in holder_type_text
        or "其他金融产品" in holder_type_text
        or "资产管理计划" in name
        or "集合资产管理计划" in name
        or "信托计划" in name
    ):
        return "securities_asset_mgmt", "HIGH"
    if "国资局" in holder_type_text or "中央汇金" in name or "中国证券金融" in name:
        return "state_strategic", "HIGH"
    if (
        "金融机构" in holder_type_text
        or "财务公司" in holder_type_text
        or "公益基金" in holder_type_text
    ):
        return "other_financial", "MEDIUM"
    if any(
        token in holder_type_text
        for token in ("一般企业", "投资公司", "投资、咨询公司", "风险投资公司")
    ):
        return "corporate_strategic", "HIGH"
    return "other_identified", "LOW"


def read_raw_parts(raw_root: Path, endpoint: str) -> pd.DataFrame:
    parts = sorted((raw_root / endpoint).glob("batch_*.parquet"))
    if not parts:
        raise FileNotFoundError(f"No downloaded batches found for {endpoint}")
    return pd.concat((pd.read_parquet(path) for path in parts), ignore_index=True)


def validate_downloads(raw_root: Path, endpoint: str, expected_batches: int) -> None:
    parquet_parts = sorted((raw_root / endpoint).glob("batch_*.parquet"))
    metadata_parts = sorted((raw_root / endpoint).glob("batch_*.json"))
    if len(parquet_parts) != expected_batches or len(metadata_parts) != expected_batches:
        raise RuntimeError(
            f"{endpoint} is incomplete: expected {expected_batches} batches, "
            f"found {len(parquet_parts)} parquet and {len(metadata_parts)} metadata files"
        )
    failures = []
    for path in metadata_parts:
        metadata = json.loads(path.read_text(encoding="utf-8"))
        failures.extend(metadata.get("failures", []))
    if failures:
        raise RuntimeError(
            f"{endpoint} still has {len(failures)} failed symbols; rerun "
            "download_ownership.py before building features"
        )


def build_holder_updates(frame: pd.DataFrame) -> pd.DataFrame:
    holder = frame.copy()
    holder["ann_date"] = pd.to_datetime(holder["ann_date"], errors="coerce")
    holder["holder_report_end"] = pd.to_datetime(holder["end_date"], errors="coerce")
    holder["holder_num"] = pd.to_numeric(holder["holder_num"], errors="coerce")
    holder = holder.dropna(subset=["ts_code", "ann_date", "holder_num"])
    holder = holder[holder["holder_num"] > 0]
    holder = holder.sort_values(
        ["ts_code", "ann_date", "holder_report_end"], kind="stable"
    ).drop_duplicates(["ts_code", "ann_date"], keep="last")
    holder["holder_num_change_rate"] = holder.groupby("ts_code", sort=False)[
        "holder_num"
    ].pct_change(fill_method=None)
    holder["holder_num_change"] = holder.groupby("ts_code", sort=False)[
        "holder_num"
    ].diff()
    return holder[
        [
            "ts_code",
            "ann_date",
            "holder_report_end",
            "holder_num",
            "holder_num_change",
            "holder_num_change_rate",
        ]
    ].reset_index(drop=True)


def build_composition_updates(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    top = frame.copy()
    top["ann_date"] = pd.to_datetime(top["ann_date"], errors="coerce")
    top["composition_report_end"] = pd.to_datetime(top["end_date"], errors="coerce")
    top["hold_float_ratio"] = pd.to_numeric(
        top["hold_float_ratio"], errors="coerce"
    )
    top["hold_amount"] = pd.to_numeric(top["hold_amount"], errors="coerce")
    top = top.dropna(subset=["ts_code", "ann_date", "holder_name"])
    top["holder_name_key"] = top["holder_name"].map(canonical_holder_name)
    top = top.sort_values(
        ["ts_code", "ann_date", "composition_report_end", "holder_name_key"],
        kind="stable",
    ).drop_duplicates(
        ["ts_code", "ann_date", "composition_report_end", "holder_name_key"],
        keep="last",
    )
    classified = top.apply(
        lambda row: classify_holder(row["holder_type"], row["holder_name"]),
        axis=1,
        result_type="expand",
    )
    top[["holder_class", "classification_confidence"]] = classified

    keys = ["ts_code", "ann_date", "composition_report_end"]
    ratio = top.pivot_table(
        index=keys,
        columns="holder_class",
        values="hold_float_ratio",
        aggfunc="sum",
        fill_value=0.0,
    )
    counts = top.pivot_table(
        index=keys,
        columns="holder_class",
        values="holder_name",
        aggfunc="nunique",
        fill_value=0,
    )
    ratio = ratio.reindex(columns=COMPONENT_CLASSES, fill_value=0.0)
    counts = counts.reindex(columns=COMPONENT_CLASSES, fill_value=0)
    ratio.columns = [f"{column}_pct" for column in ratio.columns]
    counts.columns = [f"{column}_count" for column in counts.columns]
    aggregate = pd.concat([ratio, counts], axis=1).reset_index()

    top["high_medium_ratio"] = np.where(
        top["classification_confidence"].isin(["HIGH", "MEDIUM"]),
        top["hold_float_ratio"].fillna(0.0),
        0.0,
    )
    snapshot = top.groupby(keys, observed=True).agg(
        top10_float_pct=("hold_float_ratio", "sum"),
        top10_holder_count=("holder_name", "nunique"),
        top10_hhi_numerator=(
            "hold_float_ratio",
            lambda values: float(np.square(values.fillna(0.0)).sum()),
        ),
        classified_high_medium_pct=("high_medium_ratio", "sum"),
    ).reset_index()
    aggregate = aggregate.merge(snapshot, on=keys, how="left", validate="one_to_one")
    aggregate["top10_hhi"] = np.where(
        aggregate["top10_float_pct"] > 0,
        aggregate["top10_hhi_numerator"]
        / np.square(aggregate["top10_float_pct"]),
        np.nan,
    )
    aggregate["classification_coverage_pct"] = np.where(
        aggregate["top10_float_pct"] > 0,
        100.0
        * aggregate["classified_high_medium_pct"]
        / aggregate["top10_float_pct"],
        np.nan,
    )
    aggregate["identified_institution_pct"] = aggregate[
        [f"{column}_pct" for column in INSTITUTION_CLASSES]
    ].sum(axis=1)
    aggregate["institution_holder_count"] = aggregate[
        [f"{column}_count" for column in INSTITUTION_CLASSES]
    ].sum(axis=1)
    aggregate["dispersed_float_proxy_pct"] = (
        100.0 - aggregate["top10_float_pct"]
    ).clip(0.0, 100.0)
    aggregate["retail_lower_bound_pct"] = 0.0
    aggregate["retail_upper_bound_pct"] = (
        aggregate["dispersed_float_proxy_pct"]
        + aggregate["large_individual_pct"]
    ).clip(0.0, 100.0)
    aggregate["composition_quality_valid"] = (
        aggregate["top10_float_pct"].gt(0.0)
        & aggregate["top10_float_pct"].le(100.01)
        & aggregate["top10_holder_count"].between(1, 30, inclusive="both")
    )

    aggregate = aggregate.sort_values(
        ["ts_code", "ann_date", "composition_report_end"], kind="stable"
    ).drop_duplicates(["ts_code", "ann_date"], keep="last")
    change_columns = [f"{base}_pct" for base in _COMPOSITION_BASES]
    for column in change_columns:
        aggregate[f"{column.removesuffix('_pct')}_change_pp"] = aggregate.groupby(
            "ts_code", sort=False
        )[column].diff()
    aggregate["top10_hhi_change"] = aggregate.groupby("ts_code", sort=False)[
        "top10_hhi"
    ].diff()
    aggregate["institution_holder_count_change"] = aggregate.groupby(
        "ts_code", sort=False
    )["institution_holder_count"].diff()
    return aggregate.reset_index(drop=True), top.reset_index(drop=True)


def build_event_timeline(
    holder: pd.DataFrame, composition: pd.DataFrame
) -> pd.DataFrame:
    keys = pd.concat(
        [holder[["ts_code", "ann_date"]], composition[["ts_code", "ann_date"]]],
        ignore_index=True,
    ).drop_duplicates()
    events = keys.sort_values(["ts_code", "ann_date"], kind="stable").reset_index(
        drop=True
    )
    events["new_holder_disclosure"] = events.set_index(
        ["ts_code", "ann_date"]
    ).index.isin(holder.set_index(["ts_code", "ann_date"]).index)
    events["new_composition_disclosure"] = events.set_index(
        ["ts_code", "ann_date"]
    ).index.isin(composition.set_index(["ts_code", "ann_date"]).index)

    events = events.merge(
        holder, on=["ts_code", "ann_date"], how="left", validate="one_to_one"
    )
    events = events.merge(
        composition,
        on=["ts_code", "ann_date"],
        how="left",
        validate="one_to_one",
    )
    carry_columns = [
        column
        for column in events.columns
        if column
        not in {
            "ts_code",
            "ann_date",
            "new_holder_disclosure",
            "new_composition_disclosure",
        }
    ]
    events[carry_columns] = events.groupby("ts_code", sort=False)[
        carry_columns
    ].ffill()
    events["holder_age_days"] = (
        events["ann_date"] - events["holder_report_end"]
    ).dt.days
    events["composition_age_days"] = (
        events["ann_date"] - events["composition_report_end"]
    ).dt.days
    return events


def date_filter(field: str, start: pd.Timestamp, end: pd.Timestamp):
    return (ds.field(field) >= pa.scalar(start.date(), type=pa.date32())) & (
        ds.field(field) <= pa.scalar(end.date(), type=pa.date32())
    )


def load_market_panel(
    market_root: Path, start: pd.Timestamp, end: pd.Timestamp
) -> pd.DataFrame:
    predicate = date_filter("trade_date", start, end)
    price_columns = [
        "instrument_id",
        "vendor_symbol",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "turnover_cny",
        "is_quality_valid",
    ]
    metric_columns = [
        "instrument_id",
        "trade_date",
        "turnover_rate_pct",
        "free_float_turnover_rate_pct",
        "price_earnings_ttm",
        "price_book",
        "float_shares",
        "free_float_shares",
        "market_cap_cny",
        "float_market_cap_cny",
        "limit_status",
    ]
    prices = ds.dataset(
        market_root / "daily_prices", format="parquet", partitioning="hive"
    ).to_table(columns=price_columns, filter=predicate).to_pandas()
    metrics = ds.dataset(
        market_root / "daily_metrics", format="parquet", partitioning="hive"
    ).to_table(columns=metric_columns, filter=predicate).to_pandas()
    factors = ds.dataset(
        market_root / "adjustment_factors",
        format="parquet",
        partitioning="hive",
    ).to_table(
        columns=["instrument_id", "trade_date", "adjustment_factor"],
        filter=predicate,
    ).to_pandas()
    limits = ds.dataset(
        market_root / "daily_limits", format="parquet", partitioning="hive"
    ).to_table(
        columns=["instrument_id", "trade_date", "up_limit", "down_limit"],
        filter=predicate,
    ).to_pandas()
    panel = prices.merge(
        metrics,
        on=["instrument_id", "trade_date"],
        how="left",
        validate="one_to_one",
    ).merge(
        factors,
        on=["instrument_id", "trade_date"],
        how="left",
        validate="one_to_one",
    ).merge(
        limits,
        on=["instrument_id", "trade_date"],
        how="left",
        validate="one_to_one",
    )
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    panel = panel.sort_values(["vendor_symbol", "trade_date"], kind="stable")
    panel["adjusted_close"] = panel["close"] * panel["adjustment_factor"]
    grouped = panel.groupby("vendor_symbol", sort=False)
    panel["daily_return"] = grouped["adjusted_close"].pct_change(fill_method=None)
    panel["ret_20"] = grouped["adjusted_close"].pct_change(20, fill_method=None)
    panel["ret_60"] = grouped["adjusted_close"].pct_change(60, fill_method=None)
    panel["volatility_20"] = grouped["daily_return"].transform(
        lambda values: values.rolling(20, min_periods=18).std() * np.sqrt(252.0)
    )
    panel["average_turnover_cny_20"] = grouped["turnover_cny"].transform(
        lambda values: values.rolling(20, min_periods=18).mean()
    )
    panel["listing_sessions"] = grouped.cumcount() + 1
    return panel.reset_index(drop=True)


def attach_market_features(
    events: pd.DataFrame, panel: pd.DataFrame, instruments: pd.DataFrame
) -> pd.DataFrame:
    event = events.rename(columns={"ts_code": "vendor_symbol"}).copy()
    event["vendor_symbol"] = event["vendor_symbol"].astype("object")
    event["ann_date"] = pd.to_datetime(event["ann_date"]).astype("datetime64[ns]")
    event["market_cutoff"] = event["ann_date"] - pd.Timedelta(days=1)
    market_columns = [
        "vendor_symbol",
        "trade_date",
        "close",
        "turnover_rate_pct",
        "free_float_turnover_rate_pct",
        "price_earnings_ttm",
        "price_book",
        "float_shares",
        "free_float_shares",
        "market_cap_cny",
        "float_market_cap_cny",
        "ret_20",
        "ret_60",
        "volatility_20",
        "average_turnover_cny_20",
        "listing_sessions",
    ]
    left = event.sort_values(["market_cutoff", "vendor_symbol"], kind="stable")
    right = panel[market_columns].copy()
    right["vendor_symbol"] = right["vendor_symbol"].astype("object")
    right["trade_date"] = pd.to_datetime(right["trade_date"]).astype(
        "datetime64[ns]"
    )
    right = right.sort_values(
        ["trade_date", "vendor_symbol"], kind="stable"
    )
    merged = pd.merge_asof(
        left,
        right,
        left_on="market_cutoff",
        right_on="trade_date",
        by="vendor_symbol",
        direction="backward",
        allow_exact_matches=True,
    )
    instrument_columns = [
        "vendor_symbol",
        "instrument_id",
        "industry_vendor",
        "board",
        "exchange",
        "list_date",
        "delist_date",
    ]
    instrument_frame = instruments[instrument_columns].drop_duplicates(
        "vendor_symbol"
    ).copy()
    instrument_frame["vendor_symbol"] = instrument_frame["vendor_symbol"].astype(
        "object"
    )
    merged = merged.merge(
        instrument_frame,
        on="vendor_symbol",
        how="left",
        validate="many_to_one",
    )
    merged["average_holding_shares"] = merged["float_shares"] / merged[
        "holder_num"
    ]
    merged["average_holding_value_cny"] = merged[
        "float_market_cap_cny"
    ] / merged["holder_num"]
    value_columns = [
        column for column in (f"{base}_pct" for base in _COMPOSITION_BASES) if column in merged
    ]
    for column in value_columns:
        if column in merged:
            value_column = column.replace("_pct", "_value_cny")
            values = (
                merged[column] / 100.0 * merged["float_market_cap_cny"]
            )
            merged[value_column] = values.to_numpy(dtype=float, na_value=np.nan)
            change_column = column.replace("_pct", "_value_change_rate")
            merged[change_column] = np.nan
            composition_rows = merged["new_composition_disclosure"].fillna(False)
            changes = merged.loc[
                composition_rows
            ].groupby("vendor_symbol", sort=False)[
                value_column
            ].pct_change(fill_method=None)
            merged.loc[composition_rows, change_column] = changes.to_numpy(
                dtype=float, na_value=np.nan
            )
    return merged.sort_values(
        ["vendor_symbol", "ann_date"], kind="stable"
    ).reset_index(drop=True)


def is_locked(row: pd.Series, limit_column: str) -> bool:
    limit_value = row[limit_column]
    if not np.isfinite(limit_value):
        provider_status = pd.to_numeric(row.get("limit_status", np.nan), errors="coerce")
        expected = 3 if limit_column == "up_limit" else 6
        return bool(np.isfinite(provider_status) and int(provider_status) == expected)
    values = row[["open", "high", "low", "close"]].to_numpy(dtype=float)
    tolerance = max(1e-6, abs(limit_value) * 1e-5)
    return bool(np.isfinite(values).all() and np.all(np.abs(values - limit_value) <= tolerance))


def attach_forward_labels(
    events: pd.DataFrame,
    panel: pd.DataFrame,
    open_dates: np.ndarray,
    horizons: list[int],
    buy_cost: float,
    sell_cost: float,
) -> pd.DataFrame:
    result = events.copy()
    row_count = len(result)
    outputs: dict[str, np.ndarray] = {}
    for horizon in horizons:
        outputs[f"entry_date_{horizon}"] = np.full(
            row_count, np.datetime64("NaT"), dtype="datetime64[ns]"
        )
        outputs[f"exit_date_{horizon}"] = np.full(
            row_count, np.datetime64("NaT"), dtype="datetime64[ns]"
        )
        outputs[f"future_{horizon}d_net_return"] = np.full(row_count, np.nan)
        outputs[f"exit_delay_sessions_{horizon}"] = np.full(row_count, np.nan)

    panel = panel[panel["is_quality_valid"].fillna(False)].copy()
    panel = panel.sort_values(["vendor_symbol", "trade_date"], kind="stable")
    open_dates = np.asarray(open_dates, dtype="datetime64[ns]")
    event_groups = result.groupby("vendor_symbol", sort=False).indices

    for symbol, bars in panel.groupby("vendor_symbol", sort=False):
        event_positions = event_groups.get(symbol)
        if event_positions is None or bars.empty:
            continue
        bars = bars.sort_values("trade_date", kind="stable").reset_index(drop=True)
        bar_dates = bars["trade_date"].to_numpy(dtype="datetime64[ns]")
        opens = pd.to_numeric(bars["open"], errors="coerce").to_numpy(dtype=float)
        highs = pd.to_numeric(bars["high"], errors="coerce").to_numpy(dtype=float)
        lows = pd.to_numeric(bars["low"], errors="coerce").to_numpy(dtype=float)
        closes = pd.to_numeric(bars["close"], errors="coerce").to_numpy(dtype=float)
        factors = pd.to_numeric(
            bars["adjustment_factor"], errors="coerce"
        ).to_numpy(dtype=float)
        up_limits = pd.to_numeric(bars["up_limit"], errors="coerce").to_numpy(
            dtype=float
        )
        down_limits = pd.to_numeric(
            bars["down_limit"], errors="coerce"
        ).to_numpy(dtype=float)
        provider_status = pd.to_numeric(
            bars.get("limit_status", pd.Series(np.nan, index=bars.index)),
            errors="coerce",
        ).to_numpy(dtype=float)
        flat = (
            np.isclose(opens, highs, atol=0.0051, rtol=0)
            & np.isclose(opens, lows, atol=0.0051, rtol=0)
            & np.isclose(opens, closes, atol=0.0051, rtol=0)
        )
        locked_up = np.where(
            np.isfinite(up_limits),
            flat & np.isclose(opens, up_limits, atol=0.0051, rtol=0),
            provider_status == 3,
        )
        locked_down = np.where(
            np.isfinite(down_limits),
            flat & np.isclose(opens, down_limits, atol=0.0051, rtol=0),
            provider_status == 6,
        )
        bar_indices = np.arange(len(bars), dtype=np.int64)
        next_sellable = np.minimum.accumulate(
            np.where(~locked_down, bar_indices, len(bars))[::-1]
        )[::-1]

        event_positions = np.asarray(event_positions, dtype=np.int64)
        announcements = result.iloc[event_positions]["ann_date"].to_numpy(
            dtype="datetime64[ns]"
        )
        entry_calendar_indices = np.searchsorted(
            open_dates, announcements, side="right"
        )
        valid_entry = entry_calendar_indices < len(open_dates)
        safe_calendar_indices = np.minimum(entry_calendar_indices, len(open_dates) - 1)
        entry_dates = open_dates[safe_calendar_indices]
        entry_bar_indices = np.searchsorted(bar_dates, entry_dates, side="left")
        safe_entry_bar_indices = np.minimum(entry_bar_indices, len(bars) - 1)
        valid_entry &= entry_bar_indices < len(bars)
        valid_entry &= bar_dates[safe_entry_bar_indices] == entry_dates
        valid_entry &= ~locked_up[safe_entry_bar_indices]
        valid_entry &= np.isfinite(opens[safe_entry_bar_indices])
        valid_entry &= opens[safe_entry_bar_indices] > 0
        valid_entry &= np.isfinite(factors[safe_entry_bar_indices])

        for horizon in horizons:
            planned_calendar_indices = entry_calendar_indices + horizon
            valid = valid_entry & (planned_calendar_indices < len(open_dates))
            safe_planned_indices = np.minimum(
                planned_calendar_indices, len(open_dates) - 1
            )
            planned_dates = open_dates[safe_planned_indices]
            raw_exit_indices = np.searchsorted(bar_dates, planned_dates, side="left")
            safe_raw_exit_indices = np.minimum(raw_exit_indices, len(bars) - 1)
            exit_indices = next_sellable[safe_raw_exit_indices]
            valid &= raw_exit_indices < len(bars)
            valid &= exit_indices < len(bars)
            safe_exit_indices = np.minimum(exit_indices, len(bars) - 1)
            valid &= np.isfinite(closes[safe_exit_indices])
            valid &= np.isfinite(factors[safe_exit_indices])
            if not valid.any():
                continue
            result_positions = event_positions[valid]
            valid_entries = safe_entry_bar_indices[valid]
            valid_exits = safe_exit_indices[valid]
            gross = (
                closes[valid_exits]
                * factors[valid_exits]
                / (opens[valid_entries] * factors[valid_entries])
                - 1.0
            )
            net = (1.0 - buy_cost) * (1.0 + gross) * (1.0 - sell_cost) - 1.0
            actual_exit_dates = bar_dates[valid_exits]
            actual_calendar_indices = np.searchsorted(
                open_dates, actual_exit_dates, side="left"
            )
            outputs[f"entry_date_{horizon}"][result_positions] = entry_dates[valid]
            outputs[f"exit_date_{horizon}"][result_positions] = actual_exit_dates
            outputs[f"future_{horizon}d_net_return"][result_positions] = net
            outputs[f"exit_delay_sessions_{horizon}"][result_positions] = (
                actual_calendar_indices - planned_calendar_indices[valid]
            )
    for column, values in outputs.items():
        result[column] = values
    return result


def build_summary(
    events: pd.DataFrame,
    holder: pd.DataFrame,
    composition: pd.DataFrame,
    detail: pd.DataFrame,
    raw_root: Path,
) -> dict:
    failure_count = 0
    batch_count = 0
    for path in raw_root.rglob("batch_*.json"):
        metadata = json.loads(path.read_text(encoding="utf-8"))
        failure_count += len(metadata.get("failures", []))
        batch_count += 1
    summary = {
        "holder_update_rows": int(len(holder)),
        "composition_update_rows": int(len(composition)),
        "top10_detail_rows": int(len(detail)),
        "event_rows": int(len(events)),
        "instrument_count": int(events["vendor_symbol"].nunique()),
        "announcement_start": str(events["ann_date"].min().date()),
        "announcement_end": str(events["ann_date"].max().date()),
        "holder_number_coverage": float(events["holder_num"].notna().mean()),
        "composition_coverage": float(events["top10_float_pct"].notna().mean()),
        "both_source_coverage": float(
            (events["holder_num"].notna() & events["top10_float_pct"].notna()).mean()
        ),
        "composition_quality_valid_rate": float(
            events.loc[
                events["new_composition_disclosure"].fillna(False),
                "composition_quality_valid",
            ]
            .fillna(False)
            .mean()
        ),
        "median_classification_coverage_pct": float(
            events.loc[
                events["new_composition_disclosure"].fillna(False),
                "classification_coverage_pct",
            ].median()
        ),
        "download_batch_count": batch_count,
        "download_failure_count": failure_count,
        "limitations": [
            "Public fund full-portfolio endpoint is unavailable for the current account.",
            "Institution totals cover identified top-10 floating holders, not all institutions.",
            "Dispersed float is a retail proxy, not verified retail ownership.",
        ],
    }
    for column in sorted(
        value for value in events.columns if value.startswith("future_") and value.endswith("d_net_return")
    ):
        summary[f"{column}_valid_count"] = int(events[column].notna().sum())
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", type=Path, default=ROOT / "configs" / "ownership_rebuild.json"
    )
    parser.add_argument("--skip-labels", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(args.config.resolve())
    raw_root = resolve(config["raw_data_dir"])
    processed_root = resolve(config["processed_data_dir"])
    output_root = resolve(config["output_dir"])
    market_root = resolve(config["market_data_root"])
    processed_root.mkdir(parents=True, exist_ok=True)
    output_root.mkdir(parents=True, exist_ok=True)

    instrument_count = pq.ParquetFile(market_root / "instruments.parquet").metadata.num_rows
    expected_batches = (
        instrument_count + int(config["download_batch_size"]) - 1
    ) // int(config["download_batch_size"])
    validate_downloads(raw_root, "holder_number", expected_batches)
    validate_downloads(raw_root, "top10_float", expected_batches)
    holder_raw = read_raw_parts(raw_root, "holder_number")
    composition_raw = read_raw_parts(raw_root, "top10_float")
    holder = build_holder_updates(holder_raw)
    composition, detail = build_composition_updates(composition_raw)
    events = build_event_timeline(holder, composition)

    instruments = pq.read_table(market_root / "instruments.parquet").to_pandas()
    market_start = pd.Timestamp(config["research_start"]) - pd.Timedelta(days=400)
    market_end = pd.Timestamp(config["research_end"])
    panel = load_market_panel(market_root, market_start, market_end)
    events = attach_market_features(events, panel, instruments)
    events = events[
        (events["ann_date"] >= pd.Timestamp(config["research_start"]))
        & (events["ann_date"] <= market_end)
    ].reset_index(drop=True)

    if not args.skip_labels:
        calendar = pq.read_table(market_root / "trading_calendar.parquet").to_pandas()
        calendar["calendar_date"] = pd.to_datetime(calendar["calendar_date"])
        open_dates = np.sort(
            calendar.loc[calendar["is_open"].astype(bool), "calendar_date"]
            .drop_duplicates()
            .to_numpy(dtype="datetime64[ns]")
        )
        events = attach_forward_labels(
            events,
            panel,
            open_dates,
            [int(value) for value in config["horizons"]],
            float(config["buy_cost_rate"]),
            float(config["sell_cost_rate"]),
        )

    events.to_parquet(processed_root / "ownership_events.parquet", index=False)
    detail.to_parquet(processed_root / "classified_top10_float.parquet", index=False)
    summary = build_summary(events, holder, composition, detail, raw_root)
    (output_root / "data_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
