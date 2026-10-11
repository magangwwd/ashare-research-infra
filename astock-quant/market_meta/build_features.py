from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "market_meta"
DAILY_DATA_DIR = BASE_DIR / "daily_data"
DEFAULT_INPUT = DATA_DIR / "daily_basic.csv"
DEFAULT_OUTPUT = DATA_DIR / "daily_market_features.csv"
DEFAULT_AUDIT_OUTPUT = DATA_DIR / "turnover_audit.csv"

FEATURE_COLUMNS = [
    "market_turnover_rate",
    "market_turnover_rate_f",
    "market_volume_ratio",
    "market_pe_ttm",
    "market_pb",
    "market_ps_ttm",
    "market_dv_ttm",
    "market_total_mv",
    "market_circ_mv",
    "market_total_share",
    "market_float_share",
    "market_free_share",
    "market_log_total_mv",
    "market_log_circ_mv",
    "market_log_float_share",
    "market_log_free_share",
    "market_inv_pe_ttm",
    "market_inv_pb",
    "market_inv_ps_ttm",
    "market_circ_mv_to_total_mv",
]


def _positive_inverse(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    return 1.0 / numeric.where(numeric > 0)


def _log1p_numeric(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    return np.log1p(numeric.where(numeric >= 0))


def build_market_features(input_path: Path, output_path: Path) -> pd.DataFrame:
    df = pd.read_csv(input_path, dtype={"trade_date": str, "ts_code": str})
    if df.empty:
        raise RuntimeError(f"{input_path} 为空。")

    out = pd.DataFrame()
    out["Date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d", errors="coerce").dt.strftime("%Y-%m-%d")
    out["ts_code"] = df["ts_code"].astype(str)
    rename = {
        "turnover_rate": "market_turnover_rate",
        "turnover_rate_f": "market_turnover_rate_f",
        "volume_ratio": "market_volume_ratio",
        "pe_ttm": "market_pe_ttm",
        "pb": "market_pb",
        "ps_ttm": "market_ps_ttm",
        "dv_ttm": "market_dv_ttm",
        "total_mv": "market_total_mv",
        "circ_mv": "market_circ_mv",
        "total_share": "market_total_share",
        "float_share": "market_float_share",
        "free_share": "market_free_share",
    }
    for src, dst in rename.items():
        out[dst] = pd.to_numeric(df[src], errors="coerce") if src in df.columns else np.nan

    out["market_log_total_mv"] = _log1p_numeric(out["market_total_mv"])
    out["market_log_circ_mv"] = _log1p_numeric(out["market_circ_mv"])
    out["market_log_float_share"] = _log1p_numeric(out["market_float_share"])
    out["market_log_free_share"] = _log1p_numeric(out["market_free_share"])
    out["market_inv_pe_ttm"] = _positive_inverse(out["market_pe_ttm"])
    out["market_inv_pb"] = _positive_inverse(out["market_pb"])
    out["market_inv_ps_ttm"] = _positive_inverse(out["market_ps_ttm"])
    out["market_circ_mv_to_total_mv"] = out["market_circ_mv"] / out["market_total_mv"].replace(0, np.nan)

    out = out.dropna(subset=["Date", "ts_code"])
    out = out[["Date", "ts_code"] + FEATURE_COLUMNS]
    out = out.drop_duplicates(["Date", "ts_code"], keep="last").sort_values(["Date", "ts_code"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"[market_features] saved {len(out)} rows -> {output_path}", flush=True)
    return out


def build_turnover_audit(features: pd.DataFrame, output_path: Path, max_files: int | None = None) -> pd.DataFrame:
    if features.empty or "market_turnover_rate" not in features.columns:
        return pd.DataFrame()

    feat = features[["Date", "ts_code", "market_turnover_rate"]].copy()
    feat["Date"] = feat["Date"].astype(str)
    frames: list[pd.DataFrame] = []
    paths = sorted(DAILY_DATA_DIR.glob("*.csv"))
    if max_files is not None:
        paths = paths[:max_files]

    for path in paths:
        ts_code = path.stem
        try:
            local = pd.read_csv(path, usecols=["Date", "TurnoverRate"])
        except Exception:
            continue
        local["ts_code"] = ts_code
        frames.append(local)

    if not frames:
        return pd.DataFrame()
    local_all = pd.concat(frames, ignore_index=True)
    merged = feat.merge(local_all, on=["Date", "ts_code"], how="inner")
    if merged.empty:
        return merged
    merged["turnover_abs_diff"] = (
        pd.to_numeric(merged["TurnoverRate"], errors="coerce")
        - pd.to_numeric(merged["market_turnover_rate"], errors="coerce")
    ).abs()
    merged = merged.sort_values("turnover_abs_diff", ascending=False)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"[turnover_audit] saved {len(merged)} rows -> {output_path}", flush=True)
    return merged


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="从 market_meta/daily_basic.csv 生成模型特征")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--audit-output", type=Path, default=DEFAULT_AUDIT_OUTPUT)
    parser.add_argument("--skip-audit", action="store_true", help="跳过 daily_data TurnoverRate 校验")
    parser.add_argument("--audit-max-files", type=int, default=None, help="仅校验前 N 个 daily_data 文件")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    features = build_market_features(args.input, args.output)
    if not args.skip_audit:
        build_turnover_audit(features, args.audit_output, max_files=args.audit_max_files)


if __name__ == "__main__":
    main()
