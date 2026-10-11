from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import pandas as pd
import tushare as ts


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "market_meta"
DAILY_DATA_DIR = BASE_DIR / "daily_data"
STOCK_LIST_CSV = BASE_DIR / "filtered_stocks.csv"
DEFAULT_OUTPUT = DATA_DIR / "daily_basic.csv"

DAILY_BASIC_FIELDS = ",".join(
    [
        "ts_code",
        "trade_date",
        "close",
        "turnover_rate",
        "turnover_rate_f",
        "volume_ratio",
        "pe",
        "pe_ttm",
        "pb",
        "ps",
        "ps_ttm",
        "dv_ratio",
        "dv_ttm",
        "total_share",
        "float_share",
        "free_share",
        "total_mv",
        "circ_mv",
    ]
)


def _normalize_date(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return pd.Timestamp(text).strftime("%Y%m%d")


def _load_universe(codes_arg: str | None = None) -> set[str]:
    if codes_arg:
        return {code.strip() for code in codes_arg.split(",") if code.strip()}
    meta = pd.read_csv(STOCK_LIST_CSV, encoding="utf-8-sig", usecols=["ts_code"])
    return set(meta["ts_code"].dropna().astype(str))


def _latest_local_trade_date() -> str:
    cache_path = DAILY_DATA_DIR / ".last_dates.json"
    if cache_path.exists():
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        dates = [pd.Timestamp(value) for value in data.values() if value]
        if dates:
            return max(dates).strftime("%Y%m%d")

    candidates: list[pd.Timestamp] = []
    for path in DAILY_DATA_DIR.glob("*.csv"):
        try:
            tail = pd.read_csv(path, usecols=["Date"]).tail(1)
        except Exception:
            continue
        if not tail.empty:
            candidates.append(pd.Timestamp(tail["Date"].iloc[0]))
    if not candidates:
        raise RuntimeError("无法从 daily_data 推断最近交易日。")
    return max(candidates).strftime("%Y%m%d")


def _local_trade_dates(start: str, end: str) -> list[str]:
    reference = DAILY_DATA_DIR / "000001.SZ.csv"
    if not reference.exists():
        reference = next(DAILY_DATA_DIR.glob("*.csv"))
    df = pd.read_csv(reference, usecols=["Date"])
    dates = pd.to_datetime(df["Date"], errors="coerce").dropna()
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    selected = dates[(dates >= start_ts) & (dates <= end_ts)]
    return [value.strftime("%Y%m%d") for value in selected]


def _merge_output(path: Path, new_rows: pd.DataFrame) -> pd.DataFrame:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = []
    if path.exists():
        frames.append(pd.read_csv(path, dtype={"trade_date": str, "ts_code": str}))
    if not new_rows.empty:
        frames.append(new_rows)
    if not frames:
        return pd.DataFrame()
    merged = pd.concat(frames, ignore_index=True)
    merged["trade_date"] = merged["trade_date"].astype(str)
    merged["ts_code"] = merged["ts_code"].astype(str)
    merged = merged.drop_duplicates(["ts_code", "trade_date"], keep="last")
    merged = merged.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)
    merged.to_csv(path, index=False, encoding="utf-8-sig")
    return merged


def collect_daily_basic(
    trade_dates: list[str],
    universe: set[str],
    output_path: Path,
    sleep_sec: float = 0.5,
    dry_run: bool = False,
) -> pd.DataFrame:
    pro = ts.pro_api()
    frames: list[pd.DataFrame] = []
    for i, trade_date in enumerate(trade_dates, start=1):
        print(f"[daily_basic] {i}/{len(trade_dates)} trade_date={trade_date}", flush=True)
        df = pro.daily_basic(trade_date=trade_date, fields=DAILY_BASIC_FIELDS)
        if df is None or df.empty:
            print(f"  empty: {trade_date}", flush=True)
            time.sleep(sleep_sec)
            continue
        df["ts_code"] = df["ts_code"].astype(str)
        df = df[df["ts_code"].isin(universe)].copy()
        if not df.empty:
            frames.append(df)
            print(f"  rows={len(df)}", flush=True)
        time.sleep(sleep_sec)

    result = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if dry_run:
        print(result.head().to_string(index=False) if not result.empty else "no rows")
        return result
    merged = _merge_output(output_path, result)
    print(f"[daily_basic] saved {len(merged)} rows -> {output_path}", flush=True)
    return merged


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="采集 Tushare daily_basic 到 market_meta/daily_basic.csv")
    parser.add_argument("--start", type=str, default=None, help="开始日期，YYYY-MM-DD 或 YYYYMMDD")
    parser.add_argument("--end", type=str, default=None, help="结束日期，YYYY-MM-DD 或 YYYYMMDD")
    parser.add_argument("--latest", action="store_true", help="只采集 daily_data 本地最近交易日")
    parser.add_argument("--codes", type=str, default=None, help="逗号分隔 ts_code；默认 filtered_stocks 全量")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="输出 CSV")
    parser.add_argument("--sleep", type=float, default=0.5, help="每个交易日调用后的等待秒数")
    parser.add_argument("--dry-run", action="store_true", help="只打印不写盘")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    if args.latest or (args.start is None and args.end is None):
        trade_dates = [_latest_local_trade_date()]
    else:
        start = _normalize_date(args.start)
        end = _normalize_date(args.end or args.start)
        if start is None or end is None:
            raise ValueError("--start/--end 日期无效")
        trade_dates = _local_trade_dates(start, end)
    if not trade_dates:
        raise RuntimeError("没有可采集的本地交易日。")

    universe = _load_universe(args.codes)
    collect_daily_basic(
        trade_dates=trade_dates,
        universe=universe,
        output_path=args.output,
        sleep_sec=args.sleep,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
