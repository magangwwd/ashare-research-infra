import os

BASE_DIR        = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR        = os.path.join(BASE_DIR, "financial_data")
DAILY_DATA_DIR  = os.path.join(BASE_DIR, "daily_data")
STOCK_LIST_CSV  = os.path.join(BASE_DIR, "filtered_stocks.csv")
INDUSTRY_CSV    = os.path.join(BASE_DIR, "industry_map.csv")

# 采集参数
YEARS           = 3        # 取近几年年报
WORKERS         = 5        # tushare 并发线程数
SLEEP_SEC       = 1.5      # 每只股票查询后限速（秒）

# 金融行业（资产负债率豁免）
FINANCE_INDUSTRIES = {"银行", "保险", "证券", "多元金融", "信托", "租赁"}

# 评分权重（各维度满分，合计100）
# 依据实证 Spearman ρ 调整：growth 最强(ρ=+0.159@1Y,+0.255@2Y)，valuation 方向错误(ρ=-0.220)已移除
SCORE_MAX = {
    "profitability": 20,
    "growth":        45,   # 30 + 10(原valuation) + 5(原shareholder节省) = 45
    "stability":     20,
    "shareholder":    5,   # 实证负相关(ρ=-0.132@1Y)，大幅降权
    "integrity":     10,
}

# 动态评级分位阈值（从高到低，每项表示该评级要求的最低百分位排名）
# Top 3% → A+，3–10% → A，10–25% → B+，25–45% → B，45–65% → C，Bottom 35% → D
RATING_PERCENTILES = [
    (97, "A+"),
    (90, "A"),
    (75, "B+"),
    (55, "B"),
    (35, "C"),
    ( 0, "D"),
]

# 行业景气折扣：6M涨跌幅中位数低于阈值时，该行业股票总分打折
CYCLE_DISCOUNT   = 0.85
CYCLE_THRESHOLD  = -0.10   # 6M中位收益 < -10% 触发

# 固定评级阈值（仅用于 --test 等小样本模式，样本量不足以算百分位时回退）
RATING_THRESHOLDS = [
    (90, "A+"),
    (80, "A"),
    (70, "B+"),
    (60, "B"),
    (50, "C"),
    (0,  "D"),
]
