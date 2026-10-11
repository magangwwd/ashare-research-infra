"""
fetch_quarterly_latest.py
拉取每只股票最新一期季报数据（不限年报/季报），写入 financial_data/fina_indicator_q.csv。
scorer.py 会用这份数据覆盖当期单点指标（ROE / 资产负债率 / 流动比率 / 净利润率），
而 CAGR 等趋势指标仍使用年报（fina_indicator.csv）。

运行方式：
    python fetch_quarterly_latest.py
    python fetch_quarterly_latest.py --workers 3   # 调并发数
"""
import argparse
import concurrent.futures
import sys
import time
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import tushare as ts

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "financial_data"
STOCK_LIST_CSV = BASE_DIR / "filtered_stocks.csv"
OUT_FILE = DATA_DIR / "fina_indicator_q.csv"

FIELDS = "ts_code,end_date,ann_date,roe,debt_to_assets,current_ratio,netprofit_margin"
DEFAULT_WORKERS = 4
SLEEP_SEC = 1.5

pro = ts.pro_api()
_write_lock = threading.Lock()


def _safe_float(val):
    try:
        import math
        v = float(val)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _fetch_latest_quarter(ts_code: str) -> dict | None:
    """返回该股票最新一期（任意报告期）的财务指标，优先最近披露的季报。"""
    start = (datetime.today() - timedelta(days=3 * 365)).strftime("%Y%m%d")
    try:
        df = pro.fina_indicator(
            ts_code=ts_code,
            start_date=start,
            fields=FIELDS,
        )
        if df is None or df.empty:
            return None
        df = df.dropna(subset=["end_date"])
        df = df[df["end_date"].astype(str).str.match(r"^\d{8}$")]
        if df.empty:
            return None
        # 按 ann_date 降序（最新披露），ann_date 相同时按 end_date 降序
        df = df.sort_values(
            ["ann_date", "end_date"],
            ascending=[False, False],
            na_position="last",
        )
        row = df.iloc[0].to_dict()
        # 只保留有实际数值的行
        useful = [_safe_float(row.get(c)) for c in
                  ("roe", "debt_to_assets", "current_ratio", "netprofit_margin")]
        if all(v is None for v in useful):
            return None
        return row
    except Exception as e:
        print(f"  [warn] {ts_code}: {e}", file=sys.stderr)
        return None


def _worker(ts_code: str) -> dict | None:
    result = _fetch_latest_quarter(ts_code)
    time.sleep(SLEEP_SEC)
    return result


def _append(rows: list[dict]) -> None:
    df = pd.DataFrame(rows)
    with _write_lock:
        header = not OUT_FILE.exists()
        df.to_csv(OUT_FILE, mode="a", index=False, header=header, encoding="utf-8")


def _load_done() -> set:
    if not OUT_FILE.exists():
        return set()
    try:
        df = pd.read_csv(OUT_FILE, usecols=["ts_code"], encoding="utf-8")
        return set(df["ts_code"].dropna().unique())
    except Exception:
        return set()


def fetch_all(workers: int = DEFAULT_WORKERS) -> None:
    DATA_DIR.mkdir(exist_ok=True)

    meta = pd.read_csv(STOCK_LIST_CSV, encoding="utf-8-sig")
    codes = meta["ts_code"].dropna().tolist()

    done = _load_done()
    todo = [c for c in codes if c not in done]
    print(f"共 {len(codes)} 只  已完成 {len(done)}  待拉取 {len(todo)}")

    if not todo:
        print("全部已完成，无需重新拉取。")
        return

    total = len(todo)
    batch: list[dict] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_worker, c): c for c in todo}
        for i, fut in enumerate(concurrent.futures.as_completed(futures)):
            code = futures[fut]
            try:
                row = fut.result()
                if row:
                    batch.append(row)
                else:
                    batch.append({"ts_code": code, "end_date": "NA"})
            except Exception as e:
                print(f"  [error] {code}: {e}", file=sys.stderr)
                batch.append({"ts_code": code, "end_date": "NA"})

            if len(batch) >= 50:
                _append(batch)
                batch.clear()

            if (i + 1) % 200 == 0 or (i + 1) == total:
                print(f"  进度：{i + 1}/{total}")

    if batch:
        _append(batch)

    # 汇报最新期分布
    if OUT_FILE.exists():
        df = pd.read_csv(OUT_FILE, encoding="utf-8")
        valid = df[df["end_date"].astype(str) != "NA"]["end_date"]
        print("\n最新报告期分布（Top 10）：")
        print(valid.value_counts().head(10).to_string())

    print("完成。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    args = parser.parse_args()
    fetch_all(workers=args.workers)
