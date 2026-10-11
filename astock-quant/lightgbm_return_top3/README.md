# LightGBM 20日收益 Top3

这个目录实现一个独立的 A 股研究模块：用 LightGBM 预测未来 20 个交易日的可交易收益，信号日选预测值最高的 3 只股票，默认按 `T+1` 开盘买入、持有 20 个交易日后收盘卖出。

## 当前口径

- 数据源：`daily_data/*.csv`
- 股票池：`filtered_stocks.csv`
- 行业：`industry_map.csv`
- 标签：`Close[t+20] / Open[t+1] - 1`
- 换手率：若 `daily_data` 含 `TurnoverRate`，默认纳入换手率因子；数值为百分比，例如 `0.47` 表示 0.47%
- 辅助数据：若存在 `market_meta/daily_market_features.csv` 和 `event_data/daily_event_features.csv`，默认按 `Date,ts_code` 拼接估值/市值/事件滚动因子
- 价格行为状态：可用 `--with-state-features` 显式纳入价格行为状态和心理风险代理特征做 A/B 对照；当前默认关闭，避免改变正式主模型口径
- 调仓：20 日非重叠调仓，`Top3`
- 训练：`2020-2023`
- 验证：`2024` 月度 walk-forward
- OOS：`2025+` 月度扩窗重训
- 成本：默认单边 `30bp`

## 审计后执行逻辑

- 训练集和历史候选池只使用信号日可见条件 `signal_eligible`
- 交易执行层单独使用 `entry_executable`
- 若 Top3 中某只股票在执行层未成交，该槽位保留为现金，不自动补买下一名
- `backtest_trades.csv` 会额外输出：
  - `requested_holdings`
  - `holdings`
  - `skipped_holdings`
  - `execution_rate`
  - `invested_weight`

## 文件说明

- `feature_engine.py`：构建特征面板、标签、信号日筛选标记和截面 rank 变换
- `model_backtest.py`：模板选择、月度训练、验证/OOS 回测、产物输出
- `daily_report.py`：生成指定交易日的最新 Top3 报告和截面预测
- `PLAN.md`：当前设计基线与审计结论
- `output/`：回测与日报产物

## 安装依赖

```bash
python -m pip install lightgbm
```

## 常用命令

全量回测：

```bash
python -m lightgbm_return_top3.model_backtest
```

快速验证：

```bash
python -m lightgbm_return_top3.model_backtest --test
python -m lightgbm_return_top3.model_backtest --sample-size 100
```

换手率因子 A/B 对照：

```bash
python -m lightgbm_return_top3.model_backtest --test --output-prefix turnover_test
python -m lightgbm_return_top3.model_backtest --test --no-turnover-features --output-prefix baseline_test
```

辅助数据因子 A/B 对照：

```bash
python -m lightgbm_return_top3.model_backtest --test --output-prefix aux_test
python -m lightgbm_return_top3.model_backtest --test --no-aux-data-features --output-prefix no_aux_test
```

价格行为状态因子 A/B 对照：

```bash
python -m lightgbm_return_top3.model_backtest --test --with-state-features --output-prefix state_test
python -m lightgbm_return_top3.model_backtest --test --output-prefix no_state_test
python research_notes/compare_lightgbm_state_features.py --sample-size 500 --template small --n-estimators 120 --output-prefix state_feature_oos_compare_sample500_fast
```

生成日报：

```bash
python -m lightgbm_return_top3.daily_report --trade-date 2026-04-24
```

## 输出文件

- `output/backtest_summary.csv`
- `output/backtest_trades.csv`
- `output/validation_metrics.csv`
- `output/validation_monthly.csv`
- `output/feature_importance.csv`
- `output/predictions_latest.csv`
- `output/lightgbm_top3_YYYY-MM-DD.md`

## 最新审计结果

截至 `2026-04-26` 的全量回测结果：

- 选中模板：`large`
- 选中特征数：`38`
- `validation_2024`：`mean_rank_ic=0.095939`，`avg_net_return=4.0868%`，`avg_net_alpha=4.1126%`
- `oos_2025_plus`：`mean_rank_ic=0.119864`，`avg_net_return=9.3159%`，`avg_net_alpha=7.4737%`
- `all_eval`：`mean_rank_ic=0.108756`，`avg_net_return=6.9718%`，`avg_net_alpha=5.9132%`
- 全样本平均执行率：`89.66%`

更完整的统计请看：

- [backtest_summary.csv](</g:/我的云端硬盘/stock/lightgbm_return_top3/output/backtest_summary.csv>)
- [validation_metrics.csv](</g:/我的云端硬盘/stock/lightgbm_return_top3/output/validation_metrics.csv>)
- [backtest_trades.csv](</g:/我的云端硬盘/stock/lightgbm_return_top3/output/backtest_trades.csv>)

## 当前限制

- 买入端“近似涨停”仍是简化代理，不等于真实交易所撮合
- 卖出端未处理跌停顺延
- 未处理停牌冻结后的延后卖出
- 未纳入冲击成本和盘口容量约束
- 日报属于研究输出，不应直接当作实盘成交确认
