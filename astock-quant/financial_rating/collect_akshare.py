"""
采集 akshare 补充数据：ST/风险警示状态
结果写入 financial_data/risk_flags.csv
"""

import os
from datetime import datetime, timedelta

import pandas as pd
import tushare as ts
import akshare as ak

from .config import DATA_DIR

pro = ts.pro_api()


def _latest_trade_date() -> str:
    """从 valuation.csv 读取最近交易日；若文件不存在则回退到最近工作日。"""
    val_path = os.path.join(DATA_DIR, "valuation.csv")
    if os.path.exists(val_path):
        try:
            df = pd.read_csv(val_path, usecols=["trade_date"])
            valid = df[df["trade_date"].astype(str) != "NA"]["trade_date"]
            if not valid.empty:
                return str(int(float(valid.mode()[0])))
        except Exception:
            pass
    d = datetime.today()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def collect_hk_hold() -> None:
    """
    北向资金持股比例（沪深港通）一次批量采集，覆盖全部 A 股。
    结果写入 financial_data/hk_hold.csv：ts_code, trade_date, ratio
    """
    out_path = os.path.join(DATA_DIR, "hk_hold.csv")
    trade_date = _latest_trade_date()
    print(f"采集北向持股数据（trade_date={trade_date}）...")
    try:
        df = pro.hk_hold(
            trade_date=trade_date,
            fields="ts_code,trade_date,vol,ratio",
        )
        if df is None or df.empty:
            print("  [提示] 北向持股数据为空，跳过")
            return
        df = df.dropna(subset=["ts_code"])
        df.to_csv(out_path, index=False, encoding="utf-8")
        print(f"  北向持股：{len(df)} 只，已保存至 {out_path}")
    except Exception as e:
        print(f"  [警告] 北向持股采集失败：{e}，跳过")


def collect_risk_flags() -> pd.DataFrame:
    """
    获取当前全部 ST/*ST/退市风险股票，写入 risk_flags.csv。
    返回 DataFrame: ts_code, name, risk_type
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    out_path = os.path.join(DATA_DIR, "risk_flags.csv")

    print("获取 ST/风险警示股票列表（akshare）...")
    try:
        df = ak.stock_zh_a_st_em()
    except Exception as e:
        print(f"  [警告] akshare ST 数据获取失败：{e}，跳过")
        return pd.DataFrame(columns=["ts_code", "name", "risk_type"])

    if df is None or df.empty:
        print("  [提示] 未获取到 ST 数据")
        return pd.DataFrame(columns=["ts_code", "name", "risk_type"])

    # akshare 返回字段因版本而异，尝试标准化
    code_col = next((c for c in df.columns if "代码" in c or "code" in c.lower()), None)
    name_col = next((c for c in df.columns if "名称" in c or "name" in c.lower()), None)

    if code_col is None:
        print(f"  [警告] 无法识别股票代码列，实际列名：{list(df.columns)}")
        return pd.DataFrame(columns=["ts_code", "name", "risk_type"])

    result = pd.DataFrame()
    result["raw_code"] = df[code_col].astype(str).str.strip()
    result["name"] = df[name_col].astype(str).str.strip() if name_col else ""

    # 转换为 tushare 格式：600000 → 600000.SH，000001 → 000001.SZ
    def _to_ts(code: str) -> str:
        code = code.zfill(6)
        if code.startswith("6"):
            return f"{code}.SH"
        return f"{code}.SZ"

    result["ts_code"] = result["raw_code"].apply(_to_ts)
    result["risk_type"] = "ST"

    final = result[["ts_code", "name", "risk_type"]].drop_duplicates("ts_code")
    final.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"  ST/风险股票：{len(final)} 只，已保存至 {out_path}")
    return final
