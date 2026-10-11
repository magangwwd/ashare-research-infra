"""
TechnicalPatternScanner/utils.py — 公共工具：数据加载、技术指标计算
"""

import os
import time
import warnings
import concurrent.futures

import numpy as np
import pandas as pd

from .config import DATA_DIR, STOCK_LIST_CSV

warnings.filterwarnings("ignore", category=FutureWarning)


# ── 数据加载 ──────────────────────────────────────────────────────────

REQUIRED_PRICE_COLUMNS = ["Close", "High", "Low", "Open", "Volume"]
OPTIONAL_PRICE_COLUMNS = ["TurnoverRate"]


def _normalize_price_frame(df: pd.DataFrame | None) -> pd.DataFrame | None:
    """Normalize a price frame to the repo-standard Date index + OHLCV columns."""
    if df is None or df.empty:
        return None

    out = df.copy()
    if "Price" in out.columns and "Date" not in out.columns:
        out = out.rename(columns={"Price": "Date"})
    turnover_aliases = {
        "turnoverrate": "TurnoverRate",
        "turnover_rate": "TurnoverRate",
        "turnover": "TurnoverRate",
        "换手率": "TurnoverRate",
    }
    rename_map = {
        column: turnover_aliases[str(column).strip().lower()]
        for column in out.columns
        if str(column).strip().lower() in turnover_aliases and column != "TurnoverRate"
    }
    if rename_map:
        out = out.rename(columns=rename_map)

    if "Date" in out.columns:
        out["Date"] = pd.to_datetime(out["Date"], errors="coerce")
        out = out.dropna(subset=["Date"]).set_index("Date")
    else:
        out.index = pd.to_datetime(out.index, errors="coerce")
        out = out[out.index.notna()]
        out.index.name = "Date"

    missing = [col for col in REQUIRED_PRICE_COLUMNS if col not in out.columns]
    if missing:
        return None

    numeric_columns = REQUIRED_PRICE_COLUMNS + [col for col in OPTIONAL_PRICE_COLUMNS if col in out.columns]
    out = out[numeric_columns].apply(pd.to_numeric, errors="coerce")
    out = out.dropna(subset=REQUIRED_PRICE_COLUMNS, how="any")
    out.sort_index(inplace=True)
    return out if not out.empty else None


def _read_price_csv(path: str) -> pd.DataFrame | None:
    """Read standard repo CSVs and tolerate yfinance-style multi-row headers."""
    readers = [
        lambda: pd.read_csv(path, index_col="Date", parse_dates=True),
        lambda: pd.read_csv(path, skiprows=[1, 2]),
    ]

    for reader in readers:
        try:
            df = reader()
        except Exception:
            continue
        normalized = _normalize_price_frame(df)
        if normalized is not None:
            return normalized
    return None

def load_one(
    ts_code: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame | None:
    """
    加载单只股票 CSV，返回 DatetimeIndex 升序 DataFrame。
    列：Close, High, Low, Open, Volume；若存在则保留 TurnoverRate
    失败或文件不存在返回 None。
    """
    path = os.path.join(DATA_DIR, f"{ts_code}.csv")
    if not os.path.exists(path):
        return None
    try:
        df = _read_price_csv(path)
        if df is None:
            return None
        if start_date:
            df = df[df.index >= start_date]
        if end_date:
            df = df[df.index <= end_date]
        if df.empty:
            return None
        return df
    except Exception:
        return None


def load_all(
    ts_codes: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    workers: int = 8,
    verbose: bool = True,
) -> pd.DataFrame:
    """
    并发加载全市场（或指定列表）股票数据，返回 MultiIndex DataFrame。
    index: (ts_code, Date)
    columns: Close, High, Low, Open, Volume；若存在则保留 TurnoverRate
    """
    if ts_codes is None:
        meta = pd.read_csv(STOCK_LIST_CSV, encoding="utf-8-sig")
        ts_codes = meta["ts_code"].dropna().tolist()

    total = len(ts_codes)
    frames: dict[str, pd.DataFrame] = {}

    def _load(ts_code: str):
        return ts_code, load_one(ts_code, start_date, end_date)

    if verbose:
        print(f"加载 {total} 只股票数据（{workers} 线程）...", end=" ", flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for ts_code, df in ex.map(_load, ts_codes):
            if df is not None:
                frames[ts_code] = df

    failed = total - len(frames)
    if verbose:
        print(f"成功 {len(frames)}，跳过 {failed}")

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, names=["ts_code", "Date"])
    combined.sort_index(inplace=True)
    return combined


# ── 技术指标计算 ──────────────────────────────────────────────────────

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    对单只股票 DataFrame 添加所有技术指标列，返回新 DataFrame（不修改原始对象）。

    新增列：
      MA5, MA20, MA60
      EMA12, EMA26, MACD, MACD_signal, MACD_hist
      BB_upper, BB_mid, BB_lower, BB_width20
      RSI14
      ATR14, ATR20
      DI_plus14, DI_minus14, ADX14
      Vol_MA20
    全部 vectorized，无逐行循环。
    """
    df = df.copy()
    c = df["Close"]
    h = df["High"]
    l = df["Low"]
    v = df["Volume"]

    # 移动均线
    df["MA5"]  = c.rolling(5,  min_periods=5).mean()
    df["MA20"] = c.rolling(20, min_periods=20).mean()
    df["MA60"] = c.rolling(60, min_periods=60).mean()

    # MACD（EMA 12/26/9）
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    df["EMA12"]       = ema12
    df["EMA26"]       = ema26
    df["MACD"]        = ema12 - ema26
    df["MACD_signal"] = df["MACD"].ewm(span=9, adjust=False).mean()
    df["MACD_hist"]   = df["MACD"] - df["MACD_signal"]

    # 布林带（20 日，2 倍标准差）
    bb_mid          = c.rolling(20, min_periods=20).mean()
    bb_std          = c.rolling(20, min_periods=20).std()
    df["BB_mid"]    = bb_mid
    df["BB_upper"]  = bb_mid + 2 * bb_std
    df["BB_lower"]  = bb_mid - 2 * bb_std
    df["BB_width20"] = (df["BB_upper"] - df["BB_lower"]) / bb_mid.replace(0, np.nan)

    # RSI14（Wilder 平滑法，用 EWM 近似）
    delta     = c.diff()
    gain      = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss      = (-delta).clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    rs        = gain / loss.replace(0, np.nan)
    df["RSI14"] = 100 - 100 / (1 + rs)

    # ATR / DMI / ADX（Wilder 平滑法，用 EWM 近似）
    prev_close = c.shift(1)
    true_range = pd.concat(
        [
            h - l,
            (h - prev_close).abs(),
            (l - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df["ATR14"] = true_range.ewm(alpha=1 / 14, adjust=False).mean()
    df["ATR20"] = true_range.ewm(alpha=1 / 20, adjust=False).mean()

    up_move = h.diff()
    down_move = -l.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    atr14 = df["ATR14"].replace(0, np.nan)
    plus_di = 100 * plus_dm.ewm(alpha=1 / 14, adjust=False).mean() / atr14
    minus_di = 100 * minus_dm.ewm(alpha=1 / 14, adjust=False).mean() / atr14
    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)) * 100

    df["DI_plus14"] = plus_di
    df["DI_minus14"] = minus_di
    df["ADX14"] = dx.ewm(alpha=1 / 14, adjust=False).mean()

    # 成交量均线
    df["Vol_MA20"] = v.rolling(20, min_periods=20).mean()

    return df


def add_indicators_all(all_data: pd.DataFrame, verbose: bool = True) -> pd.DataFrame:
    """
    对 MultiIndex DataFrame（ts_code, Date）批量添加技术指标。
    逐股 apply，保留 MultiIndex 结构。
    """
    ts_codes = all_data.index.get_level_values("ts_code").unique()
    total = len(ts_codes)

    if verbose:
        print(f"计算 {total} 只股票技术指标...", end=" ", flush=True)

    results = []
    for i, ts_code in enumerate(ts_codes):
        df = all_data.loc[ts_code]
        df_ind = add_indicators(df)
        df_ind.index = pd.MultiIndex.from_product(
            [[ts_code], df_ind.index], names=["ts_code", "Date"]
        )
        results.append(df_ind)
        if verbose and (i + 1) % 500 == 0:
            print(f"{i+1}/{total}", end=" ", flush=True)

    if verbose:
        print("完成")

    return pd.concat(results)


def load_stock_names() -> dict[str, str]:
    """返回 {ts_code: name} 映射字典。"""
    meta = pd.read_csv(STOCK_LIST_CSV, encoding="utf-8-sig")
    return dict(zip(meta["ts_code"], meta["name"]))


def save_result_csv(df: pd.DataFrame, output_path: str) -> str:
    """
    保存 CSV；若目标目录不可写，则自动回退到带时间戳的新文件。
    回退顺序：
      1. 原目录下带时间戳文件
      2. 当前工作目录下带时间戳文件
    返回实际保存路径。
    """
    try:
        df.to_csv(output_path, index=False, encoding="utf-8-sig")
        return output_path
    except PermissionError:
        root, ext = os.path.splitext(output_path)
        if not ext:
            ext = ".csv"
        fallback_path = f"{root}_{time.strftime('%Y%m%d_%H%M%S')}{ext}"
        try:
            df.to_csv(fallback_path, index=False, encoding="utf-8-sig")
            return fallback_path
        except PermissionError:
            cwd_fallback = os.path.join(
                os.getcwd(),
                f"{os.path.basename(root)}_{time.strftime('%Y%m%d_%H%M%S')}{ext}",
            )
            df.to_csv(cwd_fallback, index=False, encoding="utf-8-sig")
            return cwd_fallback
