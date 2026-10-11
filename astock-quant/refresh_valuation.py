"""
refresh_valuation.py
用 daily_basic(trade_date=...) 单次全市场查询，覆盖写入 financial_data/valuation.csv。
确保所有股票的 trade_date 一致，修复原先逐股采集导致的日期混乱问题。
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import tushare as ts

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "financial_data"
FIELDS = "ts_code,trade_date,pe_ttm,pb,ps_ttm,dv_ttm,total_mv,circ_mv"

pro = ts.pro_api()


def _find_latest_trading_day(lookback: int = 10) -> str:
    today = datetime.today()
    for i in range(lookback):
        d = (today - timedelta(days=i)).strftime("%Y%m%d")
        try:
            df = pro.daily_basic(trade_date=d, fields="ts_code,trade_date,pe_ttm")
            if df is not None and not df.empty:
                return d
        except Exception:
            continue
    raise RuntimeError("找不到近期交易日数据，请检查 tushare token 或网络。")


def refresh():
    DATA_DIR.mkdir(exist_ok=True)
    trade_date = _find_latest_trading_day()
    print(f"目标交易日：{trade_date}")

    df = pro.daily_basic(trade_date=trade_date, fields=FIELDS)
    if df is None or df.empty:
        print("ERROR: 未获取到数据", file=sys.stderr)
        sys.exit(1)

    print(f"获取 {len(df)} 条记录，trade_date 分布：")
    print(df["trade_date"].value_counts().to_string())

    out = DATA_DIR / "valuation.csv"
    bak = DATA_DIR / "valuation.csv.bak"
    if out.exists():
        out.rename(bak)
        print(f"原文件已备份至 {bak.name}")

    df.to_csv(out, index=False, encoding="utf-8")
    print(f"已写入 {out}  ({len(df)} 行)")


if __name__ == "__main__":
    refresh()
