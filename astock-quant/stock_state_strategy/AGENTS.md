# stock_state_strategy — Codex工作边界

本子目录是独立的A股持仓状态研究项目。请先读 README.md、GIT_SYNC_AND_DATA.md、LOCK_TO_MARKUP_PAPER_STRATEGY.md。

- 正式 V1 为冻结的 `LOCK_MARKUP_SINGLE_PAPER_V1`；只能模拟，禁止改阈值、排序、成本、退出规则以及实盘交易。
- 正式行情源为外部 `down_data/data/gold/main_board`，优先用 `ASTOCK_MARKET_DATA_ROOT` 显式定位。**不要读取父仓库 `daily_data/*.csv` 当成V1主数据。**
- 运行数据预检：`python data_source_preflight.py --config configs/ownership_state.json`。
- 旧策略研究数据在 `artifacts/legacy`，未同步Git，禁止臆造或静默重建。
- 不提交 `.env`、实际 `live_position_monitor/config.json`、Parquet、输出报告和仓库以外的文件。
- 修改源码必须通过本子目录测试，并标明真实执行环境、数据日期、通过/失败与阻塞信息。
