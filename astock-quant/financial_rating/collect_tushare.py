"""
采集 tushare Pro 财务数据：利润表 / 财务指标 / 现金流 / 分红送股 / 审计意见
- 每类数据独立存档至 financial_data/{category}.csv
- 支持中断续传：跳过已采集的股票（按 ts_code 去重追加）
- 并发模式：ThreadPoolExecutor(max_workers=WORKERS) + 限速
"""

import os
import time
import threading
import concurrent.futures
from datetime import datetime, timedelta

import pandas as pd
import tushare as ts

from .config import DATA_DIR, STOCK_LIST_CSV, YEARS, WORKERS, SLEEP_SEC

pro = ts.pro_api()
_write_lock = threading.Lock()


def _start_date(years: int) -> str:
    d = datetime.today() - timedelta(days=years * 365 + 30)
    return d.strftime("%Y%m%d")


def _year_end_rows(df: pd.DataFrame, years: int) -> pd.DataFrame:
    """只保留年报行（end_date 以 1231 结尾），取最近 years 期。"""
    if df is None or df.empty:
        return pd.DataFrame()
    mask = df["end_date"].astype(str).str.endswith("1231")
    return df[mask].head(years)


def _load_existing(path: str, key_col: str = "ts_code") -> set:
    """返回已存档文件中已有的 ts_code 集合（用于续传）。"""
    if not os.path.exists(path):
        return set()
    try:
        df = pd.read_csv(path, encoding="utf-8-sig", usecols=[key_col])
        return set(df[key_col].dropna().unique())
    except Exception:
        return set()


def _append_rows(path: str, rows: list[dict]) -> None:
    """线程安全地将一批 rows 追加写入 CSV（纯 UTF-8，无 BOM，避免多次追加产生混合编码）。"""
    if not rows:
        return
    df = pd.DataFrame(rows)
    with _write_lock:
        header = not os.path.exists(path)
        df.to_csv(path, mode="a", index=False, header=header, encoding="utf-8")


# ── 各类数据采集函数 ───────────────────────────────────────────────────

def _fetch_income(ts_code: str, start: str, years: int) -> list[dict]:
    try:
        df = pro.income(
            ts_code=ts_code,
            start_date=start,
            fields="ts_code,end_date,total_revenue,n_income_attr_p",
        )
        df = _year_end_rows(df, years)
        return df.to_dict("records") if not df.empty else []
    except Exception:
        return []


def _fetch_fina_indicator(ts_code: str, start: str, years: int) -> list[dict]:
    try:
        df = pro.fina_indicator(
            ts_code=ts_code,
            start_date=start,
            fields="ts_code,end_date,roe,debt_to_assets,current_ratio,netprofit_margin,ocfps,grossprofit_margin",
        )
        df = _year_end_rows(df, years)
        return df.to_dict("records") if not df.empty else []
    except Exception:
        return []


def _fetch_cashflow(ts_code: str, start: str, years: int) -> list[dict]:
    try:
        df = pro.cashflow(
            ts_code=ts_code,
            start_date=start,
            fields="ts_code,end_date,n_cashflow_act",
        )
        df = _year_end_rows(df, years)
        return df.to_dict("records") if not df.empty else []
    except Exception:
        return []


def _fetch_dividend(ts_code: str, years: int) -> list[dict]:
    """分红送股不按 end_date 而是按 ex_date 过滤近 years 年。"""
    try:
        df = pro.dividend(
            ts_code=ts_code,
            fields="ts_code,div_proc,stk_div,stk_bo_rate,cash_div_tax,ex_date,ann_date",
        )
        if df is None or df.empty:
            return []
        df = df[df["div_proc"] == "实施"]
        cutoff = (datetime.today() - timedelta(days=years * 365 + 30)).strftime("%Y%m%d")
        df = df[df["ex_date"].fillna("") >= cutoff]
        return df.to_dict("records")
    except Exception:
        return []


def _fetch_audit(ts_code: str, start: str, years: int) -> list[dict]:
    try:
        df = pro.fina_audit(
            ts_code=ts_code,
            start_date=start,
            fields="ts_code,end_date,audit_result",
        )
        df = _year_end_rows(df, years)
        return df.to_dict("records") if not df.empty else []
    except Exception:
        return []


def _fetch_holder_number(ts_code: str) -> list[dict]:
    """股东人数（季度），取最近4期，用于计算散户参与度变化趋势。"""
    try:
        df = pro.stk_holdernumber(
            ts_code=ts_code,
            fields="ts_code,ann_date,end_date,holder_num",
        )
        if df is None or df.empty:
            return [{"ts_code": ts_code, "end_date": "NA"}]
        df = df.sort_values("end_date", ascending=False)
        df = df.drop_duplicates(subset=["end_date"])
        return df.head(4).to_dict("records")
    except Exception:
        return [{"ts_code": ts_code, "end_date": "NA"}]


def _fetch_top_floatholders(ts_code: str) -> list[dict]:
    """前十大流通股东（最新一期），用于计算机构持股比例。"""
    try:
        df = pro.top10_floatholders(
            ts_code=ts_code,
            fields="ts_code,ann_date,end_date,holder_name,hold_ratio,holder_type",
        )
        if df is None or df.empty:
            return [{"ts_code": ts_code, "end_date": "NA"}]
        df = df.sort_values("end_date", ascending=False)
        latest = df["end_date"].iloc[0]
        return df[df["end_date"] == latest].to_dict("records")
    except Exception:
        return [{"ts_code": ts_code, "end_date": "NA"}]


def _fetch_valuation(ts_code: str) -> list[dict]:
    try:
        df = pro.daily_basic(
            ts_code=ts_code,
            fields="ts_code,trade_date,pe_ttm,pb,ps_ttm,dv_ttm,total_mv,circ_mv",
        )
        if df is None or df.empty:
            return [{"ts_code": ts_code, "trade_date": "NA"}]
        df = df.sort_values("trade_date", ascending=False)
        valid = df.dropna(subset=["pe_ttm"])
        row = valid.head(1) if not valid.empty else df.head(1)
        return row.to_dict("records")
    except Exception:
        return [{"ts_code": ts_code, "trade_date": "NA"}]


def _fetch_repurchase(ts_code: str, years: int) -> list[dict]:
    """回购记录，不支持所有 tushare 套餐，失败时静默跳过。"""
    try:
        df = pro.repurchase(
            ts_code=ts_code,
            fields="ts_code,ann_date,end_date,proc,exp_amount,amount",
        )
        if df is None or df.empty:
            return []
        df = df[df["proc"].fillna("").str.contains("完成|实施")]
        cutoff = (datetime.today() - timedelta(days=years * 365 + 30)).strftime("%Y%m%d")
        df = df[df["ann_date"].fillna("") >= cutoff]
        return df.to_dict("records")
    except Exception:
        return []


# ── 单股全量采集 ─────────────────────────────────────────────────────

def _collect_one(ts_code: str, start: str, years: int,
                 paths: dict, done_sets: dict) -> None:
    """采集单只股票的所有类型数据并追加写入对应文件。"""
    collectors = {
        "income":         lambda: _fetch_income(ts_code, start, years),
        "fina_indicator": lambda: _fetch_fina_indicator(ts_code, start, years),
        "cashflow_detail": lambda: _fetch_cashflow(ts_code, start, years),
        "dividend":       lambda: _fetch_dividend(ts_code, years),
        "audit":          lambda: _fetch_audit(ts_code, start, years),
        "repurchase":     lambda: _fetch_repurchase(ts_code, years),
        "valuation":        lambda: _fetch_valuation(ts_code),
        "holder_number":    lambda: _fetch_holder_number(ts_code),
        "top_floatholders": lambda: _fetch_top_floatholders(ts_code),
    }

    for cat, fn in collectors.items():
        if ts_code not in done_sets.get(cat, set()):
            rows = fn()
            if rows:
                _append_rows(paths[cat], rows)
            else:
                # 写占位行确保续传时不重复查（用字符串避免dtype被推断为float）
                _append_rows(paths[cat], [{"ts_code": ts_code, "end_date": "NA"}])

    time.sleep(SLEEP_SEC)


# ── 主入口 ───────────────────────────────────────────────────────────

def collect_all(ts_codes: list[str] | None = None, workers: int = WORKERS,
                years: int = YEARS) -> None:
    """
    批量采集所有股票的财务数据。
    支持中断续传：已存在于存档文件中的股票将被跳过。
    """
    os.makedirs(DATA_DIR, exist_ok=True)

    if ts_codes is None:
        meta = pd.read_csv(STOCK_LIST_CSV, encoding="utf-8-sig")
        ts_codes = meta["ts_code"].dropna().tolist()

    categories = ["income", "fina_indicator", "cashflow_detail",
                  "dividend", "audit", "repurchase", "valuation",
                  "holder_number", "top_floatholders"]
    paths = {cat: os.path.join(DATA_DIR, f"{cat}.csv") for cat in categories}

    # 续传：读取已采集的 ts_code
    done_sets = {cat: _load_existing(paths[cat]) for cat in categories}
    already = min(len(s) for s in done_sets.values()) if done_sets else 0
    todo = [c for c in ts_codes
            if any(c not in done_sets[cat] for cat in categories)]
    print(f"共 {len(ts_codes)} 只，已完成 {already}，待采集 {len(todo)} 只")

    start = _start_date(years)
    total = len(todo)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_collect_one, code, start, years, paths, done_sets): code
                   for code in todo}
        for i, future in enumerate(concurrent.futures.as_completed(futures)):
            code = futures[future]
            try:
                future.result()
            except Exception as e:
                print(f"  [错误] {code}: {e}")
            if (i + 1) % 50 == 0 or (i + 1) == total:
                print(f"  进度：{i + 1}/{total}")

    print("tushare 数据采集完成。")
