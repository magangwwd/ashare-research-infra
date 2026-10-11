"""
TechnicalPatternScanner/config.py — 全局配置：路径、回测日期分割
"""

import os

# ── 路径 ──────────────────────────────────────────────────────────────
BASE_DIR       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR       = os.path.join(BASE_DIR, "daily_data")
STOCK_LIST_CSV = os.path.join(BASE_DIR, "filtered_stocks.csv")
OUTPUT_DIR     = os.path.join(BASE_DIR, "TechnicalPatternScanner", "output")

# ── 回测 / 样本外 日期分割 ─────────────────────────────────────────────
BACKTEST_START = "2020-01-01"
BACKTEST_END   = "2024-12-31"   # 训练集上限
OOS_START      = "2025-01-01"   # 样本外验证起点
