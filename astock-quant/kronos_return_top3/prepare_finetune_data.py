from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from TechnicalPatternScanner.utils import load_one

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent
STOCK_LIST_CSV = REPO_ROOT / "filtered_stocks.csv"
DEFAULT_OUTPUT_DIR = PACKAGE_DIR / "finetune_data"


@dataclass(frozen=True)
class PrepareConfig:
    lookback_window: int = 512
    predict_window: int = 10
    train_end: str = "2023-12-31"
    val_end: str = "2024-12-31"
    sample_size: int | None = None
    sample_seed: int = 42
    output_dir: Path = DEFAULT_OUTPUT_DIR
    exp_name: str = "daily_data_kronos_base_lb512_pred10"
    pretrained_tokenizer: str = "NeoQuasar/Kronos-Tokenizer-base"
    pretrained_predictor: str = "NeoQuasar/Kronos-base"


def _window_count(row_count: int, lookback_window: int, predict_window: int) -> int:
    return max(0, int(row_count) - (lookback_window + predict_window + 1) + 1)


def _split_window_ranges(
    df: pd.DataFrame,
    lookback_window: int,
    predict_window: int,
    train_end: str,
    val_end: str,
) -> dict[str, dict[str, object]]:
    window = lookback_window + predict_window + 1
    if len(df) < window:
        return {
            "train": {"window_count": 0},
            "val": {"window_count": 0},
            "test": {"window_count": 0},
        }

    train_end_ts = pd.Timestamp(train_end)
    val_end_ts = pd.Timestamp(val_end)
    end_dates = pd.Series(df.index[window - 1 :], index=range(len(df) - window + 1))

    def _pack(mask: pd.Series) -> dict[str, object]:
        starts = end_dates.index[mask].tolist()
        if not starts:
            return {"window_count": 0}
        return {
            "window_count": len(starts),
            "start_idx_min": int(starts[0]),
            "start_idx_max": int(starts[-1]),
            "first_window_end_date": str(pd.Timestamp(end_dates.loc[starts[0]]).date()),
            "last_window_end_date": str(pd.Timestamp(end_dates.loc[starts[-1]]).date()),
        }

    return {
        "train": _pack(end_dates <= train_end_ts),
        "val": _pack((end_dates > train_end_ts) & (end_dates <= val_end_ts)),
        "test": _pack(end_dates > val_end_ts),
    }


def build_asset_catalog(config: PrepareConfig) -> pd.DataFrame:
    meta = pd.read_csv(STOCK_LIST_CSV, encoding="utf-8-sig")
    available_codes = {path.stem for path in (REPO_ROOT / "daily_data").glob("*.csv")}
    meta = meta[meta["ts_code"].isin(available_codes)].copy()
    if meta.empty:
        raise RuntimeError("No overlap between filtered_stocks.csv and daily_data/.")

    if config.sample_size is not None and config.sample_size > 0:
        meta = meta.sample(frac=1.0, random_state=config.sample_seed).reset_index(drop=True)
    else:
        meta = meta.sort_values("ts_code").reset_index(drop=True)

    min_rows = config.lookback_window + config.predict_window + 1
    rows: list[dict] = []
    for row in meta.itertuples(index=False):
        ts_code = str(row.ts_code)
        df = load_one(ts_code)
        if df is None or len(df) < min_rows:
            continue

        split_ranges = _split_window_ranges(
            df=df,
            lookback_window=config.lookback_window,
            predict_window=config.predict_window,
            train_end=config.train_end,
            val_end=config.val_end,
        )
        train_windows = int(split_ranges["train"]["window_count"])
        val_windows = int(split_ranges["val"]["window_count"])
        test_windows = int(split_ranges["test"]["window_count"])

        if train_windows <= 0:
            continue

        rows.append(
            {
                "ts_code": ts_code,
                "name": getattr(row, "name", ""),
                "path": str((REPO_ROOT / "daily_data" / f"{ts_code}.csv").resolve()),
                "first_date": str(df.index.min().date()),
                "last_date": str(df.index.max().date()),
                "total_rows": int(len(df)),
                "total_windows": _window_count(len(df), config.lookback_window, config.predict_window),
                "train_rows": int((df.index <= pd.Timestamp(config.train_end)).sum()),
                "train_windows": train_windows,
                "train_start_idx_min": int(split_ranges["train"].get("start_idx_min", -1)),
                "train_start_idx_max": int(split_ranges["train"].get("start_idx_max", -1)),
                "train_first_window_end_date": split_ranges["train"].get("first_window_end_date", ""),
                "train_last_window_end_date": split_ranges["train"].get("last_window_end_date", ""),
                "val_rows": int(((df.index > pd.Timestamp(config.train_end)) & (df.index <= pd.Timestamp(config.val_end))).sum()),
                "val_windows": val_windows,
                "val_start_idx_min": int(split_ranges["val"].get("start_idx_min", -1)),
                "val_start_idx_max": int(split_ranges["val"].get("start_idx_max", -1)),
                "val_first_window_end_date": split_ranges["val"].get("first_window_end_date", ""),
                "val_last_window_end_date": split_ranges["val"].get("last_window_end_date", ""),
                "test_rows": int((df.index > pd.Timestamp(config.val_end)).sum()),
                "test_windows": test_windows,
                "test_start_idx_min": int(split_ranges["test"].get("start_idx_min", -1)),
                "test_start_idx_max": int(split_ranges["test"].get("start_idx_max", -1)),
                "test_first_window_end_date": split_ranges["test"].get("first_window_end_date", ""),
                "test_last_window_end_date": split_ranges["test"].get("last_window_end_date", ""),
            }
        )

        if config.sample_size is not None and len(rows) >= config.sample_size:
            break

    if not rows:
        raise RuntimeError("No eligible assets found for fine-tuning dataset creation.")

    return pd.DataFrame(rows).sort_values("ts_code").reset_index(drop=True)


def build_split_manifest(catalog: pd.DataFrame, split: str) -> pd.DataFrame:
    split_windows_col = f"{split}_windows"
    manifest = catalog[catalog[split_windows_col] > 0].copy()
    manifest["split"] = split
    manifest["split_rows"] = manifest[f"{split}_rows"]
    manifest["window_count"] = manifest[split_windows_col]
    manifest["start_idx_min"] = manifest[f"{split}_start_idx_min"]
    manifest["start_idx_max"] = manifest[f"{split}_start_idx_max"]
    manifest["first_window_end_date"] = manifest[f"{split}_first_window_end_date"]
    manifest["last_window_end_date"] = manifest[f"{split}_last_window_end_date"]
    return manifest[
        [
            "ts_code",
            "name",
            "path",
            "split",
            "split_rows",
            "window_count",
            "start_idx_min",
            "start_idx_max",
            "first_window_end_date",
            "last_window_end_date",
        ]
    ].reset_index(drop=True)


def write_config_template(config: PrepareConfig, output_dir: Path) -> Path:
    config_path = output_dir / "kronos_base_daily_data.yaml"
    text = f"""# Local multi-asset Kronos fine-tune dataset config
data:
  dataset_root: "{output_dir.as_posix()}"
  train_manifest_path: "{(output_dir / 'train_manifest.csv').as_posix()}"
  val_manifest_path: "{(output_dir / 'val_manifest.csv').as_posix()}"
  test_manifest_path: "{(output_dir / 'test_manifest.csv').as_posix()}"
  lookback_window: {config.lookback_window}
  predict_window: {config.predict_window}
  max_context: {config.lookback_window}
  train_end: "{config.train_end}"
  val_end: "{config.val_end}"

model_paths:
  pretrained_tokenizer: "{config.pretrained_tokenizer}"
  pretrained_predictor: "{config.pretrained_predictor}"
  exp_name: "{config.exp_name}"

notes:
  dataset_style: "multi_asset_manifest"
  source_data: "repo daily_data/*.csv"
  expected_loader: "kronos_return_top3.finetune_dataset.MultiAssetKlineDataset"
"""
    config_path.write_text(text, encoding="utf-8")
    return config_path


def run_prepare(config: PrepareConfig) -> dict[str, object]:
    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    catalog = build_asset_catalog(config)
    train_manifest = build_split_manifest(catalog, "train")
    val_manifest = build_split_manifest(catalog, "val")
    test_manifest = build_split_manifest(catalog, "test")

    catalog_path = output_dir / "asset_catalog.csv"
    train_path = output_dir / "train_manifest.csv"
    val_path = output_dir / "val_manifest.csv"
    test_path = output_dir / "test_manifest.csv"

    catalog.to_csv(catalog_path, index=False, encoding="utf-8-sig")
    train_manifest.to_csv(train_path, index=False, encoding="utf-8-sig")
    val_manifest.to_csv(val_path, index=False, encoding="utf-8-sig")
    test_manifest.to_csv(test_path, index=False, encoding="utf-8-sig")

    summary = {
        "created_at": pd.Timestamp.now().isoformat(),
        "lookback_window": config.lookback_window,
        "predict_window": config.predict_window,
        "train_end": config.train_end,
        "val_end": config.val_end,
        "asset_count": int(len(catalog)),
        "train_asset_count": int(len(train_manifest)),
        "val_asset_count": int(len(val_manifest)),
        "test_asset_count": int(len(test_manifest)),
        "train_windows": int(train_manifest["window_count"].sum()) if not train_manifest.empty else 0,
        "val_windows": int(val_manifest["window_count"].sum()) if not val_manifest.empty else 0,
        "test_windows": int(test_manifest["window_count"].sum()) if not test_manifest.empty else 0,
        "pretrained_tokenizer": config.pretrained_tokenizer,
        "pretrained_predictor": config.pretrained_predictor,
    }
    summary_path = output_dir / "dataset_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    config_path = write_config_template(config, output_dir)

    return {
        "catalog": catalog,
        "train_manifest": train_manifest,
        "val_manifest": val_manifest,
        "test_manifest": test_manifest,
        "summary": summary,
        "config_path": config_path,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare a local Kronos fine-tuning dataset from repo daily_data/")
    parser.add_argument("--lookback-window", type=int, default=512, help="Historical points per window")
    parser.add_argument("--predict-window", type=int, default=10, help="Future points per window")
    parser.add_argument("--train-end", type=str, default="2023-12-31", help="Train split end date")
    parser.add_argument("--val-end", type=str, default="2024-12-31", help="Validation split end date")
    parser.add_argument("--sample-size", type=int, default=0, help="Optional asset sample size; <=0 means all")
    parser.add_argument("--sample-seed", type=int, default=42, help="Sampling seed")
    parser.add_argument("--output-dir", type=str, default=str(DEFAULT_OUTPUT_DIR), help="Output directory")
    parser.add_argument("--exp-name", type=str, default="daily_data_kronos_base_lb512_pred10", help="Experiment name for generated config")
    return parser


def parse_args() -> PrepareConfig:
    args = build_arg_parser().parse_args()
    sample_size = None if args.sample_size <= 0 else args.sample_size
    return PrepareConfig(
        lookback_window=args.lookback_window,
        predict_window=args.predict_window,
        train_end=args.train_end,
        val_end=args.val_end,
        sample_size=sample_size,
        sample_seed=args.sample_seed,
        output_dir=Path(args.output_dir),
        exp_name=args.exp_name,
    )


def main() -> None:
    config = parse_args()
    result = run_prepare(config)
    summary = result["summary"]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"[kronos] config template saved: {result['config_path']}")


if __name__ == "__main__":
    main()
