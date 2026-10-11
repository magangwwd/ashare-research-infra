"""
A 股日线数据增量更新脚本。

读取 filtered_stocks.csv 中的股票列表，检测每只股票 CSV 末尾日期，
只下载新增数据并追加，避免重复下载历史数据。

用法:
    python update_daily.py                      # 日常增量更新
    python update_daily.py --test               # 仅前 10 只（测试）
    python update_daily.py --start 2026-03-01   # 强制从指定日期重新下载
    python update_daily.py --end 2026-04-03     # 指定结束日期
    python update_daily.py --batch-size 20 --sleep 3.0
"""

import os
import sys
import time
import json
import argparse
from collections import defaultdict
from datetime import date, datetime, time as clock_time, timedelta

import yfinance as yf
import pandas as pd

from download_daily import ts_to_yf, yf_to_ts  # 复用代码转换函数

# ── 配置 ──────────────────────────────────────────────────────────────
BATCH_SIZE     = 30                        # 每批股票数
SLEEP_SEC      = 2.0                       # 批次间延时（秒）
FALLBACK_START = "2020-01-01"              # 无本地文件时的全量回填起点
OUTPUT_DIR     = "daily_data"
CACHE_FILE     = "daily_data/.last_dates.json"  # 扫描结果缓存，避免重复读云盘文件
FAILED_LOG     = "update_failed.txt"
MAX_RETRY      = 2
PRICE_COLUMNS  = ["Close", "High", "Low", "Open", "Volume"]
TURNOVER_COLUMN = "TurnoverRate"           # 百分比数值，如 0.47 表示 0.47%
DAILY_COLUMNS  = PRICE_COLUMNS + [TURNOVER_COLUMN]
INTRADAY_GUARD_START = clock_time(9, 15)
INTRADAY_GUARD_END = clock_time(15, 5)
# ─────────────────────────────────────────────────────────────────────


def is_intraday_guard_window(now: datetime | None = None) -> bool:
    """保守拦截 A 股工作日盘中窗口，避免把未收盘行情写入日线。"""
    current = now or datetime.now()
    return (
        current.weekday() < 5
        and INTRADAY_GUARD_START <= current.time() <= INTRADAY_GUARD_END
    )


def enforce_intraday_guard(force: bool = False, now: datetime | None = None) -> None:
    if force or not is_intraday_guard_window(now):
        return
    current = now or datetime.now()
    raise RuntimeError(
        "当前处于 A 股交易时段保护窗口 "
        f"({INTRADAY_GUARD_START.strftime('%H:%M')}-{INTRADAY_GUARD_END.strftime('%H:%M')})，"
        f"本地时间 {current.strftime('%Y-%m-%d %H:%M')}。"
        "为避免写入未收盘日线，已拒绝运行；确认只做历史回填或接受风险时请加 --force。"
    )


def get_last_date(csv_path: str) -> str | None:
    """只读文件末尾约 200 字节，提取最后一行的日期（第一列）。
    文件不存在、为空或只有 header 时返回 None。
    """
    if not os.path.exists(csv_path):
        return None
    try:
        with open(csv_path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return None
            f.seek(max(0, size - 200))
            tail = f.read().decode("utf-8", errors="ignore")
        lines = [l.strip() for l in tail.splitlines() if l.strip()]
        if len(lines) < 2:  # 只有 header 或空文件
            return None
        last_line = lines[-1]
        date_str = last_line.split(",")[0].strip()
        # 验证格式：YYYY-MM-DD
        date.fromisoformat(date_str)
        return date_str
    except (ValueError, OSError):
        return None


def load_cache(cache_file: str) -> dict[str, str]:
    """加载扫描缓存，返回 {ts_code: last_date}。文件不存在时返回空字典。"""
    if not os.path.exists(cache_file):
        return {}
    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_cache(cache_file: str, last_dates: dict[str, str | None]) -> None:
    """将 last_dates 写入缓存文件，跳过值为 None 的条目。"""
    data = {k: v for k, v in last_dates.items() if v is not None}
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def scan_last_dates(
    ts_codes: list[str],
    output_dir: str,
    cache: dict[str, str],
) -> dict[str, str | None]:
    """扫描各股票 CSV 的最新日期。

    以本地 CSV 文件尾部日期为准，缓存仅用于一致性统计。
    这样即使 .last_dates.json 落后或超前，也不会导致重复追加。
    """
    result: dict[str, str | None] = {}
    total = len(ts_codes)
    bar_width = 40

    cache_match = 0
    cache_missing = 0
    cache_stale = 0
    cache_ahead = 0
    cache_orphan = 0
    file_missing = 0

    print(f"  读取 {total} 只股票的真实文件末尾日期，并校验缓存 ...")
    for i, ts_code in enumerate(ts_codes):
        path = os.path.join(output_dir, f"{ts_code}.csv")
        file_last = get_last_date(path)
        cache_last = cache.get(ts_code)
        result[ts_code] = file_last

        if file_last is None:
            file_missing += 1

        if cache_last is None:
            cache_missing += 1
        elif file_last is None:
            cache_orphan += 1
        elif cache_last == file_last:
            cache_match += 1
        elif cache_last < file_last:
            cache_stale += 1
        else:
            cache_ahead += 1

        if (i + 1) % 50 == 0 or (i + 1) == total:
            filled = int(bar_width * (i + 1) / total)
            bar = "#" * filled + "-" * (bar_width - filled)
            print(f"\r  [{bar}] {i+1}/{total}", end="", flush=True)
    print()
    print(
        "  缓存校验："
        f"一致 {cache_match}  缺失 {cache_missing}  "
        f"过期 {cache_stale}  超前 {cache_ahead}  "
        f"有缓存但无文件 {cache_orphan}"
    )
    if file_missing:
        print(f"  文件缺失/空文件：{file_missing} 只，将按 {FALLBACK_START} 回填")

    return result


def read_price_csv(csv_path: str) -> pd.DataFrame:
    """读取本地 OHLCV/TurnoverRate CSV，兼容 yfinance 单股票多级表头格式。"""
    with open(csv_path, encoding="utf-8") as f:
        first_line = f.readline()

    if first_line.startswith("Price,"):
        df = pd.read_csv(
            csv_path,
            skiprows=3,
            header=None,
            names=["Date", "Close", "High", "Low", "Open", "Volume"],
            parse_dates=["Date"],
            index_col="Date",
        )
    else:
        df = pd.read_csv(csv_path, parse_dates=["Date"], index_col="Date")

    df.index.name = "Date"
    return df


def normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """将行情数据统一为 Date 索引 + OHLCV/TurnoverRate 单层列。"""
    result = df.copy()
    if isinstance(result.columns, pd.MultiIndex):
        for level in range(result.columns.nlevels):
            level_values = {str(v) for v in result.columns.get_level_values(level)}
            if {"Close", "High", "Low", "Open", "Volume"}.issubset(level_values):
                result.columns = result.columns.get_level_values(level)
                break

    rename_map = {
        col: {
            "close": "Close",
            "high": "High",
            "low": "Low",
            "open": "Open",
            "volume": "Volume",
            "turnoverrate": TURNOVER_COLUMN,
            "turnover_rate": TURNOVER_COLUMN,
            "turnover": TURNOVER_COLUMN,
            "换手率": TURNOVER_COLUMN,
        }[str(col).strip().lower()]
        for col in result.columns
        if str(col).strip().lower()
        in {"close", "high", "low", "open", "volume", "turnoverrate", "turnover_rate", "turnover", "换手率"}
    }
    result = result.rename(columns=rename_map)
    result = result[[c for c in DAILY_COLUMNS if c in result.columns]]
    result.index = pd.to_datetime(result.index)
    result.index.name = "Date"
    result.sort_index(inplace=True)
    result = result[~result.index.duplicated(keep="last")]
    return result


def validate_ohlcv(df: pd.DataFrame, context: str = "") -> None:
    """拒绝缺列、非正价格或不满足 Low <= Open/Close <= High 的行情。"""
    missing_columns = [col for col in PRICE_COLUMNS if col not in df.columns]
    if missing_columns:
        raise ValueError(f"{context} 缺少字段: {', '.join(missing_columns)}")

    numeric = df[PRICE_COLUMNS].apply(pd.to_numeric, errors="coerce")
    prices = numeric[["Open", "High", "Low", "Close"]]
    tolerance = prices.abs().max(axis=1).fillna(0) * 1e-6 + 1e-8
    invalid = (
        prices.isna().any(axis=1)
        | (prices <= 0).any(axis=1)
        | numeric["Volume"].isna()
        | (numeric["Volume"] < 0)
        | (numeric["High"] + tolerance < numeric["Low"])
        | (numeric["Open"] > numeric["High"] + tolerance)
        | (numeric["Open"] < numeric["Low"] - tolerance)
        | (numeric["Close"] > numeric["High"] + tolerance)
        | (numeric["Close"] < numeric["Low"] - tolerance)
    )
    if invalid.any():
        dates = ", ".join(
            pd.Timestamp(value).strftime("%Y-%m-%d")
            for value in numeric.index[invalid][:5]
        )
        raise ValueError(f"{context} OHLC 校验失败: {dates}")


def read_csv_columns(csv_path: str) -> list[str]:
    """读取 CSV 表头字段。旧 yfinance 多行表头无法判断时返回空列表。"""
    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            header = f.readline().strip()
    except OSError:
        return []
    if not header or header.startswith("Price,"):
        return []
    return [col.strip() for col in header.split(",")]


def ensure_daily_schema(df: pd.DataFrame, columns: list[str] | None = None) -> pd.DataFrame:
    """确保写盘字段包含 TurnoverRate，并按既有表头或标准表头对齐。"""
    result = df.copy()
    target = columns or ["Date", *DAILY_COLUMNS]
    data_columns = [col for col in target if col != "Date"]
    for col in data_columns:
        if col not in result.columns:
            result[col] = pd.NA
    extras = [col for col in result.columns if col not in data_columns]
    return result[data_columns + extras]


def group_by_start_date(
    last_dates: dict[str, str | None],
    fallback_start: str,
    force_start: str | None = None,
) -> dict[str, list[str]]:
    """按 start_date 分组。
    - 正常：start = last_date + 1 天
    - 无文件：start = fallback_start
    - force_start 指定时：所有股票统一使用该起始日期
    """
    groups: dict[str, list[str]] = defaultdict(list)
    for ts_code, last_date in last_dates.items():
        if force_start:
            start = force_start
        elif last_date is None:
            start = fallback_start
        else:
            next_day = date.fromisoformat(last_date) + timedelta(days=1)
            start = next_day.isoformat()
        groups[start].append(ts_code)
    return dict(groups)


def fetch_incremental_batch(
    batch_yf: list[str],
    batch_ts: list[str],
    start: str,
    end: str | None,
) -> dict[str, pd.DataFrame]:
    """下载一批股票的增量数据，返回 {ts_code: df}。下载失败的不入字典。"""
    tickers_str = " ".join(batch_yf)
    try:
        raw = yf.download(
            tickers_str,
            start=start,
            end=end,
            auto_adjust=True,
            progress=False,
            threads=False,
        )
    except Exception as e:
        print(f"  [错误] 批量下载失败: {e}")
        return {}

    if raw is None or raw.empty:
        return {}

    result: dict[str, pd.DataFrame] = {}

    # 单只 ticker：raw 无 MultiIndex
    if len(batch_yf) == 1:
        df = raw.copy()
        df.index.name = "Date"
        if not df.empty:
            result[batch_ts[0]] = df
        return result

    # 多只 ticker：MultiIndex (字段, ticker)
    for yf_code, ts_code in zip(batch_yf, batch_ts):
        try:
            df = raw.xs(yf_code, level=1, axis=1).dropna(how="all")
        except KeyError:
            continue
        if df.empty:
            continue
        df.index.name = "Date"
        result[ts_code] = df

    return result


def append_to_csv(
    ts_code: str,
    new_df: pd.DataFrame,
    output_dir: str,
    last_date: str | None,
) -> int:
    """将 new_df 追加到现有 CSV，返回实际追加的新行数。

    快速路径：无重叠时直接 append 模式写新行（~1ms/只）。
    慢速路径：有重叠时 read + concat + 去重 + 全量写回。
    """
    path = os.path.join(output_dir, f"{ts_code}.csv")
    new_df = normalize_ohlcv(new_df)

    if new_df.empty:
        return 0
    validate_ohlcv(new_df, f"{ts_code} 下载数据")

    if not os.path.exists(path):
        # 新增股票：直接全量写入，保持标准 Date,Close,High,Low,Open,Volume,TurnoverRate 格式
        ensure_daily_schema(new_df).to_csv(path)
        return len(new_df)

    # 写入前重新读取真实文件尾日；last_date 只作为调用方上下文，不再作为事实来源。
    file_last_date = get_last_date(path)
    if last_date is not None and file_last_date != last_date:
        print(
            f"\n    [缓存校准] {ts_code}: cache={last_date} file={file_last_date}",
            end=" ",
            flush=True,
        )

    # 判断是否有重叠（new_df 最早日期 <= 文件真实末尾日期说明有重叠）
    min_new = str(new_df.index.min())[:10]
    has_overlap = file_last_date is not None and min_new <= file_last_date

    if not has_overlap:
        # 快速路径：直接追加，跳过 header
        header_columns = read_csv_columns(path)
        if not header_columns or TURNOVER_COLUMN not in header_columns:
            has_overlap = True
        else:
            append_df = ensure_daily_schema(new_df, header_columns)
            with open(path, "a", newline="") as f:
                append_df.to_csv(f, header=False)
            return len(new_df)

    if not has_overlap:
        # 保留这个分支只作防御；正常快速路径已在上方返回。
        with open(path, "a", newline="") as f:
            new_df.to_csv(f, header=False)
        return len(new_df)
    else:
        # 慢速路径：读取 + 合并 + 去重
        existing = normalize_ohlcv(read_price_csv(path))
        existing_turnover = (
            existing[TURNOVER_COLUMN].copy()
            if TURNOVER_COLUMN in existing.columns
            else pd.Series(dtype=float)
        )
        combined = pd.concat([existing, new_df])
        # yfinance 复权因子可能回溯更新，重叠日期保留最新下载值。
        combined = combined[~combined.index.duplicated(keep="last")]
        combined.sort_index(inplace=True)
        combined.index.name = "Date"
        if not existing_turnover.empty:
            if TURNOVER_COLUMN not in combined.columns:
                combined[TURNOVER_COLUMN] = pd.NA
            prior_turnover = existing_turnover.reindex(combined.index)
            combined[TURNOVER_COLUMN] = combined[TURNOVER_COLUMN].where(
                combined[TURNOVER_COLUMN].notna(),
                prior_turnover,
            )
        refreshed_rows = combined.loc[combined.index.intersection(new_df.index)]
        validate_ohlcv(refreshed_rows, f"{ts_code} 合并数据")
        ensure_daily_schema(combined).to_csv(path)
        return len(combined) - len(existing)


def update_group(
    start_date: str,
    ts_codes: list[str],
    last_dates: dict[str, str | None],
    output_dir: str,
    batch_size: int,
    sleep_sec: float,
    max_retry: int,
    end_date: str | None,
    group_label: str,
) -> tuple[list[str], list[str]]:
    """处理同一 start_date 的所有股票，分批下载并追加。"""
    all_succeeded: list[str] = []
    all_failed: list[str] = []
    total = len(ts_codes)
    total_batches = (total + batch_size - 1) // batch_size

    for i in range(0, total, batch_size):
        batch_ts = ts_codes[i: i + batch_size]
        batch_yf = [ts_to_yf(c) for c in batch_ts]
        batch_num = i // batch_size + 1

        print(
            f"  {group_label} 批次 {batch_num}/{total_batches}"
            f"  ({i+1}–{min(i+batch_size, total)}/{total}) ...",
            end=" ", flush=True,
        )

        # 带重试
        remaining_ts = list(batch_ts)
        batch_succeeded: list[str] = []

        for attempt in range(1 + max_retry):
            if not remaining_ts:
                break
            if attempt > 0:
                print(f"\n    [重试 {attempt}] {len(remaining_ts)} 只 ...", end=" ", flush=True)
                time.sleep(sleep_sec)

            remaining_yf = [ts_to_yf(c) for c in remaining_ts]
            fetched = fetch_incremental_batch(remaining_yf, remaining_ts, start_date, end_date)

            next_remaining: list[str] = []
            for ts_code in remaining_ts:
                if ts_code not in fetched:
                    next_remaining.append(ts_code)
                    continue
                try:
                    added = append_to_csv(
                        ts_code,
                        fetched[ts_code],
                        output_dir,
                        last_dates.get(ts_code),
                    )
                except (OSError, ValueError) as exc:
                    print(f"\n    [数据校验失败] {exc}", end=" ", flush=True)
                    next_remaining.append(ts_code)
                    continue
                path = os.path.join(output_dir, f"{ts_code}.csv")
                last_dates[ts_code] = get_last_date(path)
                batch_succeeded.append(ts_code)
                _ = added  # 行数统计暂不输出

            remaining_ts = next_remaining

        all_succeeded.extend(batch_succeeded)
        all_failed.extend(remaining_ts)
        print(f"成功 {len(batch_succeeded)}  失败 {len(remaining_ts)}")

        # 批次间延时（最后一批不等）
        if i + batch_size < total:
            time.sleep(sleep_sec)

    return all_succeeded, all_failed


def main():
    parser = argparse.ArgumentParser(description="A 股日线数据增量更新")
    parser.add_argument("--test", action="store_true", help="仅处理前 10 只（测试）")
    parser.add_argument("--start", type=str, default=None,
                        help="强制所有股票从该日期重新下载（格式 YYYY-MM-DD）")
    parser.add_argument("--end", type=str, default=None,
                        help="下载结束日期（格式 YYYY-MM-DD，默认今天）")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE,
                        help=f"每批股票数（默认 {BATCH_SIZE}）")
    parser.add_argument("--sleep", type=float, default=SLEEP_SEC,
                        help=f"批次间延时秒数（默认 {SLEEP_SEC}）")
    parser.add_argument("--force", action="store_true",
                        help="覆盖盘中运行保护；仅确认不会写入未收盘数据时使用")
    args = parser.parse_args()

    # 切换工作目录到脚本所在目录
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)
    try:
        enforce_intraday_guard(force=args.force)
    except RuntimeError as exc:
        print(f"[错误] {exc}")
        sys.exit(2)

    # 读取股票列表
    csv_path = "filtered_stocks.csv"
    if not os.path.exists(csv_path):
        print(f"[错误] 找不到 {csv_path}，请先运行 screen_stocks.py")
        sys.exit(1)

    df = pd.read_csv(csv_path, encoding="utf-8-sig")
    ts_codes = df["ts_code"].dropna().tolist()
    if args.test:
        ts_codes = ts_codes[:10]
        print("[测试模式] 仅处理前 10 只股票")

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 加载缓存，扫描各文件末尾日期
    cache = load_cache(CACHE_FILE)
    hit = sum(1 for c in ts_codes if c in cache)
    print(f"扫描末尾日期：缓存命中 {hit}/{len(ts_codes)} 只 ...")
    t0 = time.time()
    last_dates = scan_last_dates(ts_codes, OUTPUT_DIR, cache)
    print(f"  完成（{time.time()-t0:.1f}s）")

    new_count = sum(1 for v in last_dates.values() if v is None)
    existing_count = len(ts_codes) - new_count
    print(f"  已有文件：{existing_count} 只，新增股票（无文件）：{new_count} 只")
    if not args.test:
        save_cache(CACHE_FILE, last_dates)
        print(f"  缓存已按真实文件尾日校准 → {CACHE_FILE}")

    # 按 start_date 分组
    groups = group_by_start_date(last_dates, FALLBACK_START, args.start)

    # 打印分组统计
    today_str = date.today().isoformat()
    end_str = args.end or today_str
    end_note = "（排他）" if args.end else "（默认）"
    print(f"\n下载区间上限: {end_str} {end_note}")
    print(f"分组统计（共 {len(groups)} 个不同起始日期）：")
    for start, codes in sorted(groups.items()):
        print(f"  start={start}  →  {len(codes)} 只")

    # 检查是否有需要更新的股票：
    # - 显式 --end 使用 yfinance 的排他上限 [start, end)，因此 start < end 才需要下载。
    # - 默认 end=None 时保持日常模式，start <= today 即尝试更新。
    if args.end:
        groups_to_update = {s: c for s, c in groups.items() if s < end_str}
    else:
        groups_to_update = {s: c for s, c in groups.items() if s <= end_str}
    if not groups_to_update:
        print("\n所有股票数据已是最新，无需更新。")
        return

    print(f"\n开始增量更新，批次大小 {args.batch_size}，延时 {args.sleep}s")
    print("-" * 50)

    all_succeeded: list[str] = []
    all_failed: list[str] = []

    for start_date, codes in sorted(groups_to_update.items()):
        label = f"[start={start_date}, {len(codes)}只]"
        print(f"{label}")
        ok, fail = update_group(
            start_date=start_date,
            ts_codes=codes,
            last_dates=last_dates,
            output_dir=OUTPUT_DIR,
            batch_size=args.batch_size,
            sleep_sec=args.sleep,
            max_retry=MAX_RETRY,
            end_date=args.end,
            group_label=label,
        )
        all_succeeded.extend(ok)
        all_failed.extend(fail)

    # 汇总
    print("-" * 50)
    print(f"更新完成：成功 {len(all_succeeded)}/{len(ts_codes)}  失败 {len(all_failed)}")
    if all_failed:
        with open(FAILED_LOG, "w", encoding="utf-8") as f:
            f.write("\n".join(all_failed))
        print(f"失败列表已保存至: {FAILED_LOG}")

    # 将成功更新的股票按真实 CSV 尾日写回缓存，避免 yfinance end 排他日期造成缓存超前。
    if all_succeeded and not args.test:
        for ts_code in all_succeeded:
            path = os.path.join(OUTPUT_DIR, f"{ts_code}.csv")
            last_dates[ts_code] = get_last_date(path)
        save_cache(CACHE_FILE, last_dates)
        print(f"缓存已按真实文件尾日更新（{len(all_succeeded)} 只 → {CACHE_FILE}）")


if __name__ == "__main__":
    main()
