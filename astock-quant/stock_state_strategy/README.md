# 股票持仓状态与入场研究系统

2026-09-12维护范围收缩为状态池、状态转换及持仓提醒。V1继续冻结观察，不新增入场/止盈模型；研究模块保留为运行和复核依赖。两份迁移复核输出已移到 `../archive/2026-09-12/migration_checks/`，当前 `output/forward_20260818` 和 `artifacts/legacy` 均保留。下文历史日期不代表今天的行情。

本项目由原 `demo01` 入场/收益风险研究和 `demo02` 股东持仓/阶段状态研究非破坏性合并而成。两个原项目保持不变；新代码、测试和文档集中在本目录，新运行产物写入 `output/`。

当前正式策略为 `LOCK_MARKUP_SINGLE_PAPER_V1`，状态始终是：

`PAPER_ONLY_NO_ENTRY_AUTHORIZATION`

它只允许生成状态、信号、模拟订单和评测记录，不授权连接券商或提交真实订单。

## 系统关系

```text
公开行情与历史状态 ──> 可执行T+1标签/冻结收益风险排序 ──┐
                                                        ├─> 锁仓→A级拉升 V1纸面策略
股东户数与前十大流通股东 ──> 四阶段状态与可信度 ───────┘
```

源码保持扁平模块布局，原因是原项目大量使用同目录模块导入；两个项目没有重名Python模块，因此合并后不需要动态修改 `sys.path` 指向旧项目。

## 关键文件

- `LOCK_TO_MARKUP_PAPER_STRATEGY.md`：V1策略原文，逐字节保留。
- `lock_to_markup_paper_strategy.json`：V1机器配置，随策略文档保留。
- `config.json`：入场模型、严格可执行标签和冻结排序配置。
- `configs/ownership_state.json`：股东披露、持仓组成和历史状态研究配置。
- `configs/ownership_rebuild.json`：在新项目 `runtime/` 中完全重建披露数据的配置。
- `configs/forward_20260818.json`：截至2026-08-18的隔离前向配置。
- `run_pipeline.py`：统一运行入口。
- `MERGE_MANIFEST.json`：原文件到合并文件的映射和策略哈希。

## 数据与历史产物

合并项目已将原项目约635MB的必要历史资产迁入内部目录，当前数据位置为：

- 统一主板行情：`../down_data/data/gold/main_board`
- 原股东披露原始/处理数据：`artifacts/legacy/demo02_data`
- 冻结V5严格标签：`artifacts/legacy/demo01_output/v5_tail_label_audit/v5_tail_labels.parquet`
- 冻结控盘事件：`artifacts/legacy/demo02_output/control_state/scored_events.parquet`
- 冻结排序校准候选：`artifacts/legacy/demo02_output/state_transition_backtest/candidate_rows.parquet`

上述历史资产均为只读输入，并在配置文件中以显式内部路径记录，不会被当作新项目输出覆盖。`archive/source_snapshots/` 保存原项目顶层文件快照，`artifacts/legacy/` 保存迁移后的数据和历史输出；项目运行不再依赖 `demo01` 或 `demo02` 目录。

迁移已于2026-08-24完成，并在删除原目录后通过40项测试及截至2026-08-18的端到端V1复核。原 `demo01`、`demo02` 目录已送入回收站。

## 安装与验证

```powershell
cd G:\我的云端硬盘\project\stock_state_strategy
python -m pip install -r requirements.txt
python run_pipeline.py tests
```

统一测试入口同时包含 `live_position_monitor/test_monitor.py` 的实时监控规则测试。

也可直接运行：

```powershell
python -m pytest -p no:cacheprovider
```

## 主要工作流

只读使用原demo02已处理股东数据运行持仓结构研究：

```powershell
python run_pipeline.py ownership-research
```

加入 `--download` 才会调用Tushare；原始和处理结果写入新项目的 `runtime/data`，不会写回原demo02：

```powershell
python run_pipeline.py ownership-research --download
```

运行入场模型与冻结特征审计：

```powershell
python run_pipeline.py entry-audit
```

生成指定日期的四阶段状态池：

```powershell
python run_pipeline.py current-state --as-of 2026-08-11
```

重建截至2026-08-18的状态、转换和V1当前快照：

```powershell
python run_pipeline.py forward-20260818
```

前向流程会重建约350万行逐日状态缓存，耗时和磁盘占用明显高于单日状态池。

## 状态与策略边界

系统先判断股票是否适合定义，再在吸筹、锁仓、拉升、派发四类中选择状态。只有锁仓和拉升能进入A级；吸筹和派发因缺少逐日大/小单资金流，只能作为B级代理状态。

V1入场候选必须同时满足：

1. 当前为A级拉升；
2. 上一有效状态为锁仓；
3. 转换间隔1—45日；
4. 状态得分、分差、覆盖率、稳定性、可信度和成交额达到冻结门槛；
5. Demo01冻结排序字段完整；
6. T+1开盘未高开超过3%，且不存在停牌或不可买的一字涨停。

`estimated_pl_ratio >= 1.2` 是影子审计字段，不是V1硬入场门槛。移动止盈V2历史实验未达到替换标准，未激活；V1仍使用ATR止损、派发退出和20日时间退出。

## 文档目录

- `docs/ENTRY_MODEL_RESEARCH_HISTORY.md`：原demo01完整研究交接记录。
- `docs/OWNERSHIP_STATE_RESEARCH_HISTORY.md`：原demo02持仓结构交接记录。
- `docs/CONTROL_POOL_ENTRY_AUDIT.md`：控盘Top20叠加入场条件审计。
- `docs/CONTROL_STATE_RESEARCH.md`：控盘状态研究定义。
- `docs/STATE_DEFINITION_RESEARCH.md`：四阶段状态池定义。
- `docs/MOVING_TAKE_PROFIT_V2_REJECTED_EXPERIMENT.md`：未激活的移动止盈实验。

历史文档中的原目录和原输出路径作为研究记录保留，不代表新项目默认写入位置。

## 香山股份持仓监控

`live_position_monitor/` 已配置香山股份 `002870.SZ`、买入价49元的多源实时行情监控。它按正式V1策略冻结ATR止损，并监测拉升转派发和20日时间退出；触发后通过SMTP发送邮件，但没有券商接口，也不会自动下单。

首次使用前必须核对暂定买入日并设置SMTP环境变量：

```powershell
cd live_position_monitor
python monitor.py --check-config
python monitor.py --initialize --dry-run
```

完整配置与启动方法见 `live_position_monitor/README.md`。

## 安全约束

- 不回填策略冻结日前已观察到的信号。
- 不使用公告日前不可得的股东信息。
- 不因前向结果临时调整阈值、排序、费用、仓位或退出规则。
- 任何策略逻辑变更必须使用新策略编号和新的前向起点。
- 本项目不提供任何实盘下单接口。
