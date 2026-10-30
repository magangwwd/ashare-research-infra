"""Announcement-aware name/ST states shared by frozen V1 calculation paths."""
from __future__ import annotations

import numpy as np
import pandas as pd


def historical_name_states(panel: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
    """Return known/ST flags without inferring a normal state from missing data.

    A record must be both effective and announced by the decision date. Among
    visible active records, the latest effective date wins; conflicting or null
    flags at that date stay unknown. Output preserves positional/index alignment.
    """
    required = {"instrument_id", "effective_date", "end_date", "announcement_date", "is_st"}
    missing = required.difference(history.columns)
    if missing:
        raise ValueError("name_history lacks required PIT fields: " + ", ".join(sorted(missing)))
    if not {"instrument_id", "trade_date"}.issubset(panel.columns):
        raise ValueError("price panel lacks instrument_id/trade_date")

    records = history.copy()
    for column in ("effective_date", "end_date", "announcement_date"):
        records[column] = pd.to_datetime(records[column], errors="coerce")
    groups = {key: group for key, group in records.groupby("instrument_id", sort=False)}
    known = np.zeros(len(panel), dtype=bool)
    is_st = np.zeros(len(panel), dtype=bool)
    for key, positions in panel.groupby("instrument_id", sort=False).indices.items():
        group = groups.get(key)
        if group is None:
            continue
        pos = np.asarray(positions, dtype=np.int64)
        dates = pd.to_datetime(panel.iloc[pos]["trade_date"], errors="coerce").to_numpy(dtype="datetime64[ns]")
        latest = np.full(len(pos), np.datetime64("NaT", "ns"), dtype="datetime64[ns]")
        local_known = np.zeros(len(pos), dtype=bool)
        local_st = np.zeros(len(pos), dtype=bool)
        for row in group.itertuples(index=False):
            if pd.isna(row.effective_date) or pd.isna(row.announcement_date):
                continue
            begin = np.datetime64(row.effective_date, "ns")
            announcement = np.datetime64(row.announcement_date, "ns")
            visible = (dates >= begin) & (dates >= announcement)
            if pd.notna(row.end_date):
                visible &= dates <= np.datetime64(row.end_date, "ns")
            flag_known = isinstance(row.is_st, (bool, np.bool_))
            flag = bool(row.is_st) if flag_known else False
            newer = visible & (np.isnat(latest) | (begin > latest))
            tied = visible & ~newer & (begin == latest)
            # A second equal-date record cannot make an unknown state safe.
            local_known[tied] &= flag_known & (local_st[tied] == flag)
            local_known[newer] = flag_known
            local_st[newer] = flag
            latest[newer] = begin
        known[pos] = local_known
        is_st[pos] = local_st & local_known
    return pd.DataFrame({"historical_state_known": known, "historical_is_st": is_st}, index=panel.index)
