"""
用 akshare 为 daily_data/*.csv 补齐 TurnoverRate（换手率）字段。

字段约定：
    Date,Close,High,Low,Open,Volume,TurnoverRate

TurnoverRate 使用东方财富接口返回的百分比数值，例如 0.47 表示 0.47%。
如果本地行情行 `Volume == 0` 且换手率缺失，则按 0 处理。

用法:
    python backfill_turnover_rate.py --code 000001.SZ
    python backfill_turnover_rate.py --test
    python backfill_turnover_rate.py --limit 100 --sleep 0.8
    python backfill_turnover_rate.py --offset 100 --limit 100 --sleep 0.8
    python backfill_turnover_rate.py --start 2026-04-01 --end 2026-05-05 --code 000001.SZ
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import requests


DAILY_DATA_DIR = Path("daily_data")
STOCK_LIST_CSV = Path("filtered_stocks.csv")
FAILED_LOG = Path("turnover_failed.txt")

PRICE_COLUMNS = ["Close", "High", "Low", "Open", "Volume"]
TURNOVER_COLUMN = "TurnoverRate"
DAILY_COLUMNS = PRICE_COLUMNS + [TURNOVER_COLUMN]
EASTMONEY_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"


def normalize_date_arg(value: str | None) -> str | None:
    if value is None:
        return None
    return value.replace("-", "")


def ts_code_to_symbol(ts_code: str) -> str:
    return ts_code.split(".")[0]


def read_daily_csv(path: Path) -> pd.DataFrame:
    """读取本地 OHLCV CSV，兼容旧 yfinance 多行表头。"""
    with path.open("r", encoding="utf-8") as f:
        first_line = f.readline()

    if first_line.startswith("Price,"):
        df = pd.read_csv(
            path,
            skiprows=3,
            header=None,
            names=["Date", *PRICE_COLUMNS],
            parse_dates=["Date"],
            index_col="Date",
        )
    else:
        df = pd.read_csv(path, parse_dates=["Date"], index_col="Date")

    df.index = pd.to_datetime(df.index)
    df.index.name = "Date"
    df.sort_index(inplace=True)
    df = df[~df.index.duplicated(keep="last")]
    return df


def ensure_daily_schema(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    for col in DAILY_COLUMNS:
        if col not in result.columns:
            result[col] = pd.NA
    extras = [col for col in result.columns if col not in DAILY_COLUMNS]
    return result[DAILY_COLUMNS + extras]


def fill_zero_volume_turnover(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    result = df.copy()
    volume = pd.to_numeric(result.get("Volume"), errors="coerce")
    turnover = pd.to_numeric(result[TURNOVER_COLUMN], errors="coerce")
    mask = volume.eq(0) & turnover.isna()
    count = int(mask.sum())
    if count:
        result.loc[mask, TURNOVER_COLUMN] = 0.0
    return result, count


def get_codes(args: argparse.Namespace) -> list[str]:
    if args.code:
        codes = args.code
    elif STOCK_LIST_CSV.exists():
        meta = pd.read_csv(STOCK_LIST_CSV, encoding="utf-8-sig")
        codes = meta["ts_code"].dropna().astype(str).tolist()
    else:
        codes = sorted(path.stem for path in DAILY_DATA_DIR.glob("*.csv"))

    if args.test:
        codes = codes[:10]
    if args.only_missing:
        codes = [code for code in codes if needs_turnover_backfill(code)]
    if args.offset:
        codes = codes[args.offset :]
    if args.limit is not None:
        codes = codes[: args.limit]
    return codes


def needs_turnover_backfill(ts_code: str) -> bool:
    path = DAILY_DATA_DIR / f"{ts_code}.csv"
    if not path.exists():
        return False
    try:
        header = pd.read_csv(path, nrows=0).columns
        if TURNOVER_COLUMN not in header:
            return True
        series = pd.read_csv(path, usecols=[TURNOVER_COLUMN])[TURNOVER_COLUMN]
        if series.empty:
            return True
        non_na = int(series.notna().sum())
        if non_na == 0:
            return True
        # 日常增量后最常见的是尾部新增行缺 TurnoverRate；历史零星空洞不反复重试。
        return bool(series.tail(5).isna().any())
    except Exception:
        return True


def fetch_turnover(ts_code: str, start: str, end: str) -> pd.DataFrame:
    """直接读取东方财富历史 K 线接口中的换手率字段。"""
    symbol = ts_code_to_symbol(ts_code)
    market_code = 1 if symbol.startswith("6") else 0
    params = {
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f116",
        "ut": "7eea3edcaed734bea9cbfc24409ed989",
        "klt": "101",
        "fqt": "0",
        "secid": f"{market_code}.{symbol}",
        "beg": start,
        "end": end,
    }
    response = requests.get(EASTMONEY_KLINE_URL, params=params, timeout=20)
    response.raise_for_status()
    data = response.json().get("data") or {}
    klines = data.get("klines") or []
    if not klines:
        return pd.DataFrame(columns=[TURNOVER_COLUMN])

    rows: list[tuple[str, str]] = []
    for item in klines:
        parts = item.split(",")
        if len(parts) >= 11:
            rows.append((parts[0], parts[10]))
    result = pd.DataFrame(rows, columns=["日期", TURNOVER_COLUMN])
    result["Date"] = pd.to_datetime(result["日期"], errors="coerce")
    result[TURNOVER_COLUMN] = pd.to_numeric(result[TURNOVER_COLUMN], errors="coerce")
    result = result.dropna(subset=["Date"]).set_index("Date")[[TURNOVER_COLUMN]]
    result.sort_index(inplace=True)
    result = result[~result.index.duplicated(keep="last")]
    return result


def process_code(args: argparse.Namespace, ts_code: str) -> tuple[str, bool, str]:
    ok = False
    message = ""
    for attempt in range(args.max_retry + 1):
        try:
            ok, message = backfill_one(
                ts_code=ts_code,
                start_arg=args.start,
                end_arg=args.end,
                overwrite=args.overwrite,
                dry_run=args.dry_run,
                fill_zero_only=args.fill_zero_volume_only,
            )
            if ok or attempt >= args.max_retry:
                break
            time.sleep(args.sleep)
        except Exception as exc:  # pragma: no cover - CLI resilience
            message = f"{type(exc).__name__}: {exc}"
            if attempt < args.max_retry:
                time.sleep(args.sleep)
    return ts_code, ok, message


def backfill_one(
    ts_code: str,
    start_arg: str | None,
    end_arg: str | None,
    overwrite: bool,
    dry_run: bool,
    fill_zero_only: bool = False,
) -> tuple[bool, str]:
    path = DAILY_DATA_DIR / f"{ts_code}.csv"
    if not path.exists():
        return False, "missing daily csv"

    daily = ensure_daily_schema(read_daily_csv(path))
    if daily.empty:
        return False, "empty daily csv"

    if fill_zero_only:
        daily, zero_filled = fill_zero_volume_turnover(daily)
        if not dry_run and zero_filled:
            daily.to_csv(path)
        non_na = int(daily[TURNOVER_COLUMN].notna().sum())
        return True, f"zero_volume_filled={zero_filled} non_na={non_na}/{len(daily)}"

    if start_arg or end_arg or overwrite:
        start = normalize_date_arg(start_arg) or daily.index.min().strftime("%Y%m%d")
        end = normalize_date_arg(end_arg) or daily.index.max().strftime("%Y%m%d")
    else:
        missing_dates = daily.index[daily[TURNOVER_COLUMN].isna()]
        if missing_dates.empty:
            non_na = int(daily[TURNOVER_COLUMN].notna().sum())
            return True, f"already complete non_na={non_na}/{len(daily)}"
        start = missing_dates.min().strftime("%Y%m%d")
        end = missing_dates.max().strftime("%Y%m%d")
    turnover = fetch_turnover(ts_code, start, end)
    if turnover.empty:
        return False, "empty turnover data"

    before_non_na = int(daily[TURNOVER_COLUMN].notna().sum())
    common_index = daily.index.intersection(turnover.index)
    if common_index.empty:
        return False, "no overlapping trade dates"

    if overwrite:
        daily.loc[common_index, TURNOVER_COLUMN] = turnover.loc[common_index, TURNOVER_COLUMN]
    else:
        missing_mask = daily.loc[common_index, TURNOVER_COLUMN].isna()
        fill_index = common_index[missing_mask.to_numpy()]
        daily.loc[fill_index, TURNOVER_COLUMN] = turnover.loc[fill_index, TURNOVER_COLUMN]

    daily, zero_filled = fill_zero_volume_turnover(daily)
    after_non_na = int(daily[TURNOVER_COLUMN].notna().sum())
    filled = after_non_na - before_non_na
    if not dry_run:
        daily.to_csv(path)
    return True, f"matched={len(common_index)} filled={filled} zero_volume_filled={zero_filled} non_na={after_non_na}/{len(daily)}"


def main() -> int:
    parser = argparse.ArgumentParser(description="为 daily_data CSV 补齐 TurnoverRate 换手率字段")
    parser.add_argument("--code", action="append", help="指定股票代码，可重复传入，如 --code 000001.SZ")
    parser.add_argument("--test", action="store_true", help="仅处理 filtered_stocks.csv 前 10 只")
    parser.add_argument("--limit", type=int, default=None, help="最多处理 N 只")
    parser.add_argument("--offset", type=int, default=0, help="跳过前 N 只，便于分批续跑")
    parser.add_argument("--only-missing", action=argparse.BooleanOptionalAction, default=True,
                        help="仅处理缺失或不完整 TurnoverRate 的股票，默认启用")
    parser.add_argument("--start", type=str, default=None, help="起始日期 YYYY-MM-DD 或 YYYYMMDD，默认本地 CSV 首日")
    parser.add_argument("--end", type=str, default=None, help="结束日期 YYYY-MM-DD 或 YYYYMMDD，默认本地 CSV 尾日")
    parser.add_argument("--sleep", type=float, default=0.8, help="每只股票之间等待秒数，默认 0.8")
    parser.add_argument("--workers", type=int, default=1, help="并发 worker 数，默认 1；建议 2-4")
    parser.add_argument("--max-retry", type=int, default=2, help="失败重试次数，默认 2")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已有 TurnoverRate，否则只填空值")
    parser.add_argument("--fill-zero-volume-only", action="store_true",
                        help="只将 Volume=0 且 TurnoverRate 缺失的行填为 0，不访问网络")
    parser.add_argument("--dry-run", action="store_true", help="只测试下载和匹配，不写 CSV")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    os.chdir(script_dir)

    codes = get_codes(args)
    if not codes:
        print("[错误] 未找到待处理股票")
        return 1

    print(f"准备补齐 TurnoverRate：{len(codes)} 只，dry_run={args.dry_run}, overwrite={args.overwrite}")
    succeeded: list[str] = []
    failed: list[str] = []

    if args.workers <= 1:
        for i, ts_code in enumerate(codes, start=1):
            ts_code, ok, message = process_code(args, ts_code)
            if ok:
                succeeded.append(ts_code)
                status = "成功"
            else:
                failed.append(f"{ts_code}\t{message}")
                status = "失败"
            print(f"[{i}/{len(codes)}] {ts_code} {status}: {message}", flush=True)

            if i < len(codes):
                time.sleep(args.sleep)
    else:
        max_workers = max(1, args.workers)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {executor.submit(process_code, args, ts_code): ts_code for ts_code in codes}
            for i, future in enumerate(as_completed(future_map), start=1):
                ts_code, ok, message = future.result()
                if ok:
                    succeeded.append(ts_code)
                    status = "成功"
                else:
                    failed.append(f"{ts_code}\t{message}")
                    status = "失败"
                print(f"[{i}/{len(codes)}] {ts_code} {status}: {message}", flush=True)

    print("-" * 50)
    print(f"完成：成功 {len(succeeded)}/{len(codes)}  失败 {len(failed)}")
    if failed and not args.dry_run:
        FAILED_LOG.write_text("\n".join(failed), encoding="utf-8")
        print(f"失败列表已保存至: {FAILED_LOG}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
