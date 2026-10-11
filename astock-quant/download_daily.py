"""
使用 yfinance 批量下载 filtered_stocks.csv 中 A 股主板股票的日线数据。

用法:
    python download_daily.py           # 下载全部股票
    python download_daily.py --test    # 仅下载前 10 只（测试用）
"""

import yfinance as yf
import pandas as pd
import time
import os
import sys
import argparse

# ── 配置 ──────────────────────────────────────────────────────────────
START_DATE  = "2020-01-01"  # 下载起始日期
END_DATE    = None          # None 表示下载到今天
BATCH_SIZE  = 50            # 每批下载只数
SLEEP_SEC   = 2.5           # 批次间延时（秒）
OUTPUT_DIR  = "daily_data"  # 输出目录（相对脚本所在目录）
SKIP_EXISTS = True          # True: 已有文件的股票跳过不重复下载
MAX_RETRY   = 2             # 单批失败后最多重试次数
PRICE_COLUMNS = ["Close", "High", "Low", "Open", "Volume"]
TURNOVER_COLUMN = "TurnoverRate"  # 百分比数值，如 0.47 表示 0.47%
DAILY_COLUMNS = PRICE_COLUMNS + [TURNOVER_COLUMN]
# ─────────────────────────────────────────────────────────────────────


def ts_to_yf(ts_code: str) -> str:
    """tushare 代码转 yfinance 代码。
    000001.SZ → 000001.SZ（深交所不变）
    600000.SH → 600000.SS（上交所后缀改 SS）
    """
    code, market = ts_code.split(".")
    return f"{code}.SS" if market == "SH" else f"{code}.SZ"


def yf_to_ts(yf_code: str) -> str:
    """yfinance 代码转回 tushare 代码（用于文件命名）。"""
    code, market = yf_code.split(".")
    return f"{code}.SH" if market == "SS" else f"{code}.SZ"


def normalize_daily_frame(df: pd.DataFrame) -> pd.DataFrame:
    """统一写盘列，预留 TurnoverRate 字段由 backfill_turnover_rate.py 补齐。"""
    result = df.copy()
    if isinstance(result.columns, pd.MultiIndex):
        for level in range(result.columns.nlevels):
            level_values = {str(v) for v in result.columns.get_level_values(level)}
            if {"Close", "High", "Low", "Open", "Volume"}.issubset(level_values):
                result.columns = result.columns.get_level_values(level)
                break
    rename_map = {
        col: str(col).strip().title()
        for col in result.columns
        if str(col).strip().lower() in {"close", "high", "low", "open", "volume"}
    }
    result = result.rename(columns=rename_map)
    for col in DAILY_COLUMNS:
        if col not in result.columns:
            result[col] = pd.NA
    result = result[DAILY_COLUMNS]
    result.index.name = "Date"
    return result


def download_batch(
    batch_yf: list[str],
    batch_ts: list[str],
    start: str,
    end,
    output_dir: str,
    skip_exists: bool,
) -> tuple[list[str], list[str]]:
    """下载一批股票，返回 (成功列表, 失败列表)，均为 ts_code 格式。"""

    # 若 skip_exists，过滤掉已下载的
    to_download_yf, to_download_ts = [], []
    skipped = []
    for yf_code, ts_code in zip(batch_yf, batch_ts):
        path = os.path.join(output_dir, f"{ts_code}.csv")
        if skip_exists and os.path.exists(path):
            skipped.append(ts_code)
        else:
            to_download_yf.append(yf_code)
            to_download_ts.append(ts_code)

    if not to_download_yf:
        return skipped, []

    # 批量下载
    tickers_str = " ".join(to_download_yf)
    try:
        raw = yf.download(
            tickers_str,
            start=start,
            end=end,
            auto_adjust=True,
            progress=False,
            threads=False,   # 避免内部线程引发频率问题
        )
    except Exception as e:
        print(f"  [错误] 批量下载失败: {e}")
        return skipped, to_download_ts  # 整批算失败

    if raw.empty:
        return skipped, to_download_ts

    succeeded, failed = list(skipped), []

    # 单只 ticker 时 raw 没有多级列，需统一处理
    if len(to_download_yf) == 1:
        df = normalize_daily_frame(raw.copy())
        df.index.name = "Date"
        if not df.empty:
            path = os.path.join(output_dir, f"{to_download_ts[0]}.csv")
            df.to_csv(path)
            succeeded.append(to_download_ts[0])
        else:
            failed.append(to_download_ts[0])
        return succeeded, failed

    # 多只 ticker：raw 列为 MultiIndex (field, ticker)
    for yf_code, ts_code in zip(to_download_yf, to_download_ts):
        try:
            df = raw.xs(yf_code, level=1, axis=1).dropna(how="all")
        except KeyError:
            failed.append(ts_code)
            continue

        if df.empty:
            failed.append(ts_code)
            continue

        df = normalize_daily_frame(df)
        path = os.path.join(output_dir, f"{ts_code}.csv")
        df.to_csv(path)
        succeeded.append(ts_code)

    return succeeded, failed


def main():
    parser = argparse.ArgumentParser(description="yfinance A 股日线下载器")
    parser.add_argument("--test", action="store_true", help="仅下载前 10 只（测试）")
    args = parser.parse_args()

    # 切换工作目录到脚本所在目录
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)

    # 读取股票列表
    csv_path = "filtered_stocks.csv"
    if not os.path.exists(csv_path):
        print(f"[错误] 找不到 {csv_path}，请先运行 screen_stocks.py")
        sys.exit(1)

    df = pd.read_csv(csv_path, encoding="utf-8-sig")
    ts_codes = df["ts_code"].dropna().tolist()
    if args.test:
        ts_codes = ts_codes[:10]
        print(f"[测试模式] 仅下载前 10 只股票")

    yf_codes = [ts_to_yf(c) for c in ts_codes]

    # 创建输出目录
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    total = len(ts_codes)
    all_failed: list[str] = []
    succeeded_count = 0

    print(f"共 {total} 只股票，每批 {BATCH_SIZE} 只，批次间延时 {SLEEP_SEC}s")
    print(f"下载区间: {START_DATE} ~ {END_DATE or '今天'}")
    print(f"输出目录: {os.path.abspath(OUTPUT_DIR)}")
    print("-" * 50)

    for i in range(0, total, BATCH_SIZE):
        batch_ts = ts_codes[i: i + BATCH_SIZE]
        batch_yf = yf_codes[i: i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE

        print(f"批次 {batch_num}/{total_batches}  ({i+1}–{min(i+BATCH_SIZE, total)}/{total}) ...", end=" ", flush=True)

        # 带重试
        ok, fail = [], batch_ts
        for attempt in range(1 + MAX_RETRY):
            if not fail:
                break
            if attempt > 0:
                print(f"  [重试 {attempt}] {len(fail)} 只 ...", end=" ", flush=True)
                time.sleep(SLEEP_SEC)
            ok_cur, fail = download_batch(
                [ts_to_yf(c) for c in fail], fail,
                START_DATE, END_DATE, OUTPUT_DIR, SKIP_EXISTS
            )
            ok.extend(ok_cur)

        succeeded_count += len(ok)
        all_failed.extend(fail)
        print(f"成功 {len(ok)}  失败 {len(fail)}")

        # 批次间延时（最后一批不等）
        if i + BATCH_SIZE < total:
            time.sleep(SLEEP_SEC)

    # 汇总
    print("-" * 50)
    print(f"下载完成：成功 {succeeded_count}/{total}  失败 {len(all_failed)}")
    if all_failed:
        fail_path = "download_failed.txt"
        with open(fail_path, "w", encoding="utf-8") as f:
            f.write("\n".join(all_failed))
        print(f"失败列表已保存至: {fail_path}")


if __name__ == "__main__":
    main()
