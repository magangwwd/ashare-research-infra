# Git同步与统一数据源说明（2026-10-11）

**状态：代码同步至独立Git分支；不表示已完成数据更新或回测。**

## 代码与数据的边界

- 本目录是 Google Drive `project/stock_state_strategy` 的代码快照；GitHub 中路径为 `astock-quant/stock_state_strategy`。
- 正式冻结策略为 `LOCK_MARKUP_SINGLE_PAPER_V1`；状态 `PAPER_ONLY_NO_ENTRY_AUTHORIZATION`，不可自动实盘交易。
- **唯一正式行情源**：Tushare Gold `down_data/data/gold/main_board`，Parquet + manifest。仓库根目录旧 `daily_data/*.csv` 是独立 LightGBM/Kronos 历史研究数据，禁止直接混入正式V1。
- 635MB以上历史依赖 `artifacts/legacy`、历史输出 `output`、`runtime` 均不进入Git。若需完整冻结V1重算，需要从原Drive项目恢复对应资产。
- 持仓监控的实际 `.env`、`live_position_monitor/config.json` 和运行状态未进入Git，只提交空白凭据模板与去个人化样例配置。

## 项目路径（Windows PowerShell）

```powershell
cd 'G:\我的云端硬盘\project\astock-quant\stock_state_strategy'
$env:ASTOCK_MARKET_DATA_ROOT = 'G:\我的云端硬盘\project\down_data\data\gold\main_board'
python data_source_preflight.py --config configs/ownership_state.json
python data_source_preflight.py --config config.json --strict-legacy
python -m unittest -v test_data_source_paths.py
python -m pip install -r requirements.txt
python run_pipeline.py tests
```

改成真实存在的Gold目录；上述路径仅是示例。也支持Linux环境变量形式。
未设置环境变量时，会先采用配置的相对路径；若不存在，再查找该Git仓库父级的 `down_data`。最终实际选择以预检报告显示为准。

## 可执行范围

```powershell
python run_pipeline.py current-state --as-of YYYY-MM-DD
```

仅在数据、股东披露及必要的历史依赖都通过验证之后运行。当前 `run_pipeline.py forward-20260818` 是冻结历史快照，不是自动跟随最新日期的日报。

**预检是结构与manifest日期检查，不会更新数据，也不能证明所有证券分区均完整。** 日线增量更新应在 `down_data` 原有Tushare下载链路完成，再对 manifest/数据覆盖率作完整验收。

## Git操作约束

只在新分支内继续更改并通过代码审查后合并。源文件保持原策略语义，只有数据解析增加环境变量适配。禁止覆盖旧仓库LightGBM/Kronos数据和模型，也禁止提交API密钥、实际持仓配置、运行日志和历史Parquet。
