"""
A股主板股票筛选脚本
排除条件：
  1. 上市不满3年
  2. 商誉占净资产比例 > 30%
  3. 大股东质押率 > 50%
  4. 经营现金流连续两年为负
  5. 创业板、科创板、ST/*ST板块
"""

import tushare as ts
import pandas as pd
import concurrent.futures
from datetime import datetime, timedelta
from pathlib import Path

pro = ts.pro_api()
BASE_DIR = Path(__file__).resolve().parent

# ── Step 1：获取主板股票列表并初步过滤 ──────────────────────────────────────
print("Step 1: 获取股票列表...")
cutoff = (datetime.today() - timedelta(days=3 * 365)).strftime('%Y%m%d')

df_sh = pro.stock_basic(exchange='SSE', list_status='L',
                        fields='ts_code,name,list_date,market')
df_sz = pro.stock_basic(exchange='SZSE', list_status='L',
                        fields='ts_code,name,list_date,market')
df = pd.concat([df_sh, df_sz], ignore_index=True)

before = len(df)
df = df[df['market'] == '主板']
print(f"  排除非主板：{before - len(df)} 只，剩余 {len(df)} 只")

before = len(df)
df = df[~df['name'].str.contains('ST', na=False)]
print(f"  排除ST：{before - len(df)} 只，剩余 {len(df)} 只")

before = len(df)
df = df[df['list_date'] <= cutoff]
print(f"  排除上市不满3年（{cutoff} 之后）：{before - len(df)} 只，剩余 {len(df)} 只")

# ── Step 2：排除质押率 > 50% ─────────────────────────────────────────────────
print("\nStep 2: 获取质押率数据...")
# 获取最近一期全市场质押数据
pledge = pro.pledge_stat(end_date='20260328',
                         fields='ts_code,end_date,pledge_ratio')
if pledge.empty:
    pledge = pro.pledge_stat(end_date='20260321',
                             fields='ts_code,end_date,pledge_ratio')
pledge = pledge.sort_values('end_date').groupby('ts_code').last().reset_index()

df = df.merge(pledge[['ts_code', 'pledge_ratio']], on='ts_code', how='left')
df['pledge_ratio'] = df['pledge_ratio'].fillna(0)

before = len(df)
df = df[df['pledge_ratio'] <= 50]
print(f"  排除质押率>50%：{before - len(df)} 只，剩余 {len(df)} 只")

# ── Step 3：排除商誉/净资产 > 30% ────────────────────────────────────────────
print("\nStep 3: 查询商誉数据（多线程，预计5-10分钟）...")

def get_goodwill_ratio(ts_code):
    try:
        data = pro.balancesheet(
            ts_code=ts_code,
            fields='ts_code,end_date,goodwill,total_hldr_eqy_exc_min_int',
            limit=8
        )
        data = data[data['end_date'].str.endswith('1231')].head(1)
        if data.empty:
            return ts_code, None
        equity = data['total_hldr_eqy_exc_min_int'].iloc[0]
        if equity is None or equity <= 0:
            return ts_code, None
        goodwill = data['goodwill'].iloc[0] or 0
        return ts_code, goodwill / equity * 100
    except Exception:
        return ts_code, None

goodwill_results = {}
total = len(df)
with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
    futures = {executor.submit(get_goodwill_ratio, code): code
               for code in df['ts_code']}
    for i, future in enumerate(concurrent.futures.as_completed(futures)):
        code, ratio = future.result()
        goodwill_results[code] = ratio
        if (i + 1) % 100 == 0 or (i + 1) == total:
            print(f"  进度：{i + 1}/{total}")

df['goodwill_ratio'] = df['ts_code'].map(goodwill_results)

before = len(df)
df = df[df['goodwill_ratio'].isna() | (df['goodwill_ratio'] <= 30)]
print(f"  排除商誉>30%净资产：{before - len(df)} 只，剩余 {len(df)} 只")

# ── Step 4：排除经营现金流连续两年为负 ───────────────────────────────────────
print("\nStep 4: 查询经营现金流数据（多线程，预计5-8分钟）...")

def get_ocf_neg(ts_code):
    try:
        data = pro.cashflow(
            ts_code=ts_code,
            fields='ts_code,end_date,n_cashflow_act',
            limit=10
        )
        data = data[data['end_date'].str.endswith('1231')].head(2)
        if len(data) < 2:
            return ts_code, False  # 数据不足，保留
        ocf0 = data['n_cashflow_act'].iloc[0]
        ocf1 = data['n_cashflow_act'].iloc[1]
        if ocf0 is None or ocf1 is None:
            return ts_code, False
        return ts_code, (ocf0 < 0 and ocf1 < 0)
    except Exception:
        return ts_code, False

ocf_results = {}
total = len(df)
with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
    futures = {executor.submit(get_ocf_neg, code): code
               for code in df['ts_code']}
    for i, future in enumerate(concurrent.futures.as_completed(futures)):
        code, is_neg = future.result()
        ocf_results[code] = is_neg
        if (i + 1) % 100 == 0 or (i + 1) == total:
            print(f"  进度：{i + 1}/{total}")

df['ocf_neg_2yr'] = df['ts_code'].map(ocf_results)

before = len(df)
df = df[~df['ocf_neg_2yr']]
print(f"  排除连续两年负现金流：{before - len(df)} 只，剩余 {len(df)} 只")

# ── Step 5：保存结果 ──────────────────────────────────────────────────────────
print("\nStep 5: 保存结果...")
output = df[['ts_code', 'name', 'list_date', 'market',
             'pledge_ratio', 'goodwill_ratio']].copy()
output = output.sort_values('ts_code').reset_index(drop=True)
output_path = BASE_DIR / 'filtered_stocks.csv'
output.to_csv(output_path, index=False, encoding='utf-8-sig')

print(f"\n{'='*50}")
print(f"最终股票池：{len(output)} 只")
print(f"已保存至：{output_path}")
print(f"{'='*50}")
print(output.head(10).to_string())
