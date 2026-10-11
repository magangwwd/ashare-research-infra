from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from torch.utils.data import Dataset


FEATURE_COLUMNS = ["open", "high", "low", "close", "volume", "amount"]
TIME_COLUMNS = ["minute", "hour", "weekday", "day", "month"]


@dataclass(frozen=True)
class AssetRecord:
    ts_code: str
    name: str
    path: str
    split: str
    split_rows: int
    window_count: int
    start_idx_min: int
    start_idx_max: int
    first_window_end_date: str
    last_window_end_date: str


def _read_daily_csv(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )
    df["amount"] = df["close"] * df["volume"]
    df["timestamps"] = df["Date"]
    df["minute"] = df["timestamps"].dt.minute
    df["hour"] = df["timestamps"].dt.hour
    df["weekday"] = df["timestamps"].dt.weekday
    df["day"] = df["timestamps"].dt.day
    df["month"] = df["timestamps"].dt.month
    cols = ["timestamps"] + FEATURE_COLUMNS + TIME_COLUMNS
    out = df[cols].copy()
    out = out.dropna(subset=FEATURE_COLUMNS)
    out = out.sort_values("timestamps").reset_index(drop=True)
    return out


class MultiAssetKlineDataset(Dataset):
    def __init__(
        self,
        manifest_path: str | Path,
        lookback_window: int = 512,
        predict_window: int = 10,
        clip: float = 5.0,
    ) -> None:
        self.manifest_path = Path(manifest_path)
        self.lookback_window = int(lookback_window)
        self.predict_window = int(predict_window)
        self.window = self.lookback_window + self.predict_window + 1
        self.clip = float(clip)

        manifest = pd.read_csv(self.manifest_path)
        manifest = manifest[manifest["window_count"] > 0].copy()
        if manifest.empty:
            raise RuntimeError(f"No windows found in manifest: {self.manifest_path}")

        self.records = [
            AssetRecord(
                ts_code=str(row.ts_code),
                name=str(row.name),
                path=str(row.path),
                split=str(row.split),
                split_rows=int(row.split_rows),
                window_count=int(row.window_count),
                start_idx_min=int(row.start_idx_min),
                start_idx_max=int(row.start_idx_max),
                first_window_end_date=str(row.first_window_end_date),
                last_window_end_date=str(row.last_window_end_date),
            )
            for row in manifest.itertuples(index=False)
        ]
        self.cumulative_windows = np.cumsum([record.window_count for record in self.records]).tolist()
        self.asset_cache: dict[str, pd.DataFrame] = {}

    def __len__(self) -> int:
        return int(self.cumulative_windows[-1])

    def _load_asset_split(self, record: AssetRecord) -> pd.DataFrame:
        cache_key = record.ts_code
        if cache_key in self.asset_cache:
            return self.asset_cache[cache_key]

        df = _read_daily_csv(record.path)
        self.asset_cache[cache_key] = df
        return df

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        asset_idx = bisect_right(self.cumulative_windows, idx)
        prev_cum = 0 if asset_idx == 0 else self.cumulative_windows[asset_idx - 1]
        local_idx = idx - prev_cum
        record = self.records[asset_idx]

        asset_df = self._load_asset_split(record)
        start_idx = record.start_idx_min + local_idx
        if start_idx > record.start_idx_max:
            raise IndexError(f"Sample index {idx} exceeds split range for {record.ts_code}.")
        window_df = asset_df.iloc[start_idx : start_idx + self.window]

        x_df = window_df.iloc[: self.lookback_window]
        y_df = window_df.iloc[self.lookback_window : self.lookback_window + self.predict_window]

        x = x_df[FEATURE_COLUMNS].to_numpy(dtype=np.float32)
        x_stamp = x_df[TIME_COLUMNS].to_numpy(dtype=np.float32)
        y = y_df[FEATURE_COLUMNS].to_numpy(dtype=np.float32)
        y_stamp = y_df[TIME_COLUMNS].to_numpy(dtype=np.float32)

        x_mean = np.mean(x, axis=0)
        x_std = np.std(x, axis=0)
        x = (x - x_mean) / (x_std + 1e-5)
        x = np.clip(x, -self.clip, self.clip)
        y = (y - x_mean) / (x_std + 1e-5)
        y = np.clip(y, -self.clip, self.clip)

        return (
            torch.from_numpy(x),
            torch.from_numpy(x_stamp),
            torch.from_numpy(y),
            torch.from_numpy(y_stamp),
        )


def summarize_manifest(manifest_path: str | Path) -> dict[str, int]:
    manifest = pd.read_csv(manifest_path)
    return {
        "assets": int(len(manifest)),
        "rows": int(manifest["split_rows"].sum()),
        "windows": int(manifest["window_count"].sum()),
    }
