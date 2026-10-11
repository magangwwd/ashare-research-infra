"""
python -m financial_rating [options]

选项：
  --collect-only   仅采集数据，不评分
  --score-only     仅评分（使用已存档数据），不采集
  --skip-report    仅更新 scores.csv，不生成/覆盖 report.md
  --test           只处理前10只股票（快速验证）
  --workers N      tushare 并发线程数（默认5）
  --years N        财务数据年限（默认3）
"""

import argparse
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from financial_rating.config import DATA_DIR, STOCK_LIST_CSV, WORKERS, YEARS
from financial_rating.collect_tushare import collect_all
from financial_rating.collect_akshare import collect_risk_flags, collect_hk_hold
from financial_rating.scorer import compute_scores
from financial_rating.report import generate_report


def main():
    parser = argparse.ArgumentParser(description="财务数据采集与评级")
    parser.add_argument("--collect-only", action="store_true", help="仅采集数据")
    parser.add_argument("--score-only",   action="store_true", help="仅评分（使用已存档数据）")
    parser.add_argument("--skip-report",  action="store_true", help="仅更新 scores.csv，不生成/覆盖 report.md")
    parser.add_argument("--test",         action="store_true", help="仅处理前10只股票")
    parser.add_argument("--workers",      type=int, default=WORKERS, help="并发线程数")
    parser.add_argument("--years",        type=int, default=YEARS,   help="财务数据年限")
    args = parser.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)

    # 确定股票列表
    meta = pd.read_csv(STOCK_LIST_CSV, encoding="utf-8-sig")
    ts_codes = meta["ts_code"].dropna().tolist()
    if args.test:
        ts_codes = ts_codes[:10]
        print(f"[测试模式] 仅处理前10只：{ts_codes}")

    # 阶段1：数据采集
    if not args.score_only:
        print("\n===== 阶段1：采集 tushare 财务数据 =====")
        collect_all(ts_codes=ts_codes, workers=args.workers, years=args.years)

        print("\n===== 阶段1b：采集 akshare 风险标记 =====")
        collect_risk_flags()

        print("\n===== 阶段1c：采集北向持股（批量，一次覆盖全市场）=====")
        collect_hk_hold()

    # 阶段2：评分
    if not args.collect_only:
        print("\n===== 阶段2：计算综合评分 =====")
        df_scores = compute_scores(
            ts_codes=ts_codes if args.test else None,
            years=args.years,
        )

        report_path = None
        if args.skip_report:
            print("\n===== 阶段3：跳过报告生成（--skip-report）=====")
        else:
            print("\n===== 阶段3：生成报告 =====")
            report_path = generate_report(df_scores)

        print("\n===== 完成 =====")
        print(f"评分结果：{os.path.join(DATA_DIR, 'scores.csv')}")
        if report_path is not None:
            print(f"综合报告：{report_path}")
        else:
            print("综合报告：已跳过，未覆盖 report.md")

        # 打印简要摘要
        rating_dist = df_scores["rating"].value_counts().sort_index()
        print("\n评级分布：")
        for rating, cnt in rating_dist.items():
            print(f"  {rating}: {cnt} 只")
        top5 = df_scores.head(5)[["ts_code", "name", "total_score", "rating"]]
        print("\nTop 5：")
        print(top5.to_string(index=False))


if __name__ == "__main__":
    main()
