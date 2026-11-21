"""Read-only bridge from Task 01 production Clean data into Task 02."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from data_pipeline.manifest import read_dataset, read_json


class CleanResearchData:
    """Read only an accepted Task 01 production snapshot.

    The recovery checkpoint and staging directories are deliberately not valid
    research inputs.  Consumers enter through data/current only.
    """

    def __init__(self, root: str | Path = "."):
        self.root = Path(root).resolve()
        self.current = self.root / "data/current"
        if not self.current.exists():
            raise FileNotFoundError("data/current does not exist; no accepted production is available")
        acceptance = read_json(self.current / "acceptance.json")
        if not acceptance.get("passed"):
            raise ValueError("data/current is not an accepted Task 01 production snapshot")
        self.acceptance = acceptance

    def load(self, dataset: str) -> pd.DataFrame:
        return read_dataset(self.current, "clean", dataset)

    def market(self, start: date | None = None, end: date | None = None) -> pd.DataFrame:
        daily = self.load("daily")
        factor = self.load("adj_factor")
        basic = self.load("daily_basic")
        keys = ["security_id", "trade_date"]
        if any(frame.empty for frame in (daily, factor, basic)):
            raise ValueError("daily, adj_factor and daily_basic must all be present")
        # Fail closed: inner joins must not silently erase historical securities.
        # A genuinely suspended day absent from all three sources is not fabricated;
        # a day present in one source but missing in another is an integrity gap.
        key_sets = {}
        for name, source in (("daily", daily), ("adj_factor", factor), ("daily_basic", basic)):
            if source.duplicated(keys).any():
                raise ValueError(f"{name}: duplicate security/date keys in accepted Clean")
            key_sets[name] = pd.MultiIndex.from_frame(source[keys])
        if not (key_sets["daily"].equals(key_sets["adj_factor"]) and
                key_sets["daily"].equals(key_sets["daily_basic"])):
            # Index.equals depends on ordering; compare unique key sets instead.
            missing = {name: len(key_sets["daily"].difference(index))
                       for name, index in key_sets.items() if name != "daily"}
            extra = {name: len(index.difference(key_sets["daily"]))
                     for name, index in key_sets.items() if name != "daily"}
            if any(missing.values()) or any(extra.values()):
                raise ValueError(f"Clean core key coverage mismatch; missing={missing}, extra={extra}")
        frame = daily.merge(
            factor[keys + ["adj_factor"]],
            on=keys, how="inner", validate="one_to_one",
        )
        basic_cols = [c for c in basic.columns if c not in ("ts_code", "schema_version")]
        frame = frame.merge(
            basic[basic_cols], on=keys, how="inner", validate="one_to_one",
            suffixes=("", "_basic"),
        )
        if start is not None:
            frame = frame.loc[frame["trade_date"] >= start]
        if end is not None:
            frame = frame.loc[frame["trade_date"] <= end]
        return frame.sort_values(["trade_date", "security_id"], kind="stable").reset_index(drop=True)

    def master(self) -> pd.DataFrame:
        return self.load("security_master")

    def calendar(self) -> pd.DataFrame:
        return self.load("trade_cal")
