# Task 01 冻结规格与验收对应

本项目按用户提供的 47 项冻结规格执行。此文保留开发约束与检查对应关系；不得将 Task 02 功能加入当前任务。

## 不可更改的边界

唯一主源 Tushare；唯一目标为 A 股研究数据基础设施。只创建 data_pipeline、config、tests、docs 和单一 `.github/workflows/task01.yml`。保持五个清晰模块，requirements.txt 管理依赖，普通 YAML 配置和环境变量 Secret。

不实现因子/选股/标签/ML/回测/交易/新闻/15 分钟/AI Agent/DuckDB/复杂日志/高级质量/完整 Lineage/PIT；不展开每日 ST、停牌、指数成员或上市天数，不长期保存 QFQ/HFQ。Task 02 才建立 curated 与 factors。

数据等级：五个 Critical 不可禁用；Required 失败先保核心并记录 Gap；Optional 只采 frozen whitelist；Candidate 不自动采。启动审计权限并保存日期和能力快照，行业先检测后决定主体系，主要指数通过配置管理而非代码常量。

Raw 保留原语义、字段、日期以及实际能获得的财务历史版本。Clean 固定 Schema/version/dtype/Date/主键，重复保留最后一条；财务按报告期、公告及修订选择最新有效版本。预留 available_date，不能宣称严格 PIT。财务 Raw 使用 ann_year/ann_month，Clean 使用 report_year/report_period。

Parquet/ZSTD/Hive；自然分区验证、compact、再验证后才能发布；目标 128–256 MB，小分区无需强拆。Clean 长期保留，行情 Raw 约 90–180 天，财务与元数据 Raw 长期保留。

Parquet 是事实来源，Manifest 可重建；分批历史、断点续传、失败和缺失修补、数据分类增量回补及每月历史审计。当天 Critical、Required、Optional 优先，最后历史修复。交易日 DAILY_UPDATE，非交易日 MAINTENANCE。

覆盖率最低 95%，首次缺失逐股补抓；日期/类型/非空/关键字段/主键基础验证；任何 Critical 下载或验收失败须非零退出，未验收不得发布，保持上一完整正式版本。Phase 1 为 Artifact，Phase 2 才接 Drive 的 staging → remote validate → current pointer 发布。

## 交付和外部验收

| 子任务 | 实现入口/证据 | 仍需真实验收 |
| --- | --- | --- |
| 01.1 Repository Bootstrap | README、requirements、config、gitignore、package | 已验证仓库推送和 Runner 安装/单元测试 |
| 01.2 Tushare Bootstrap | download.py；重试、分页、限流、Capability Audit | 五个 Critical 权限与网络调用 |
| 01.3 Security Master & Calendar | clean.py、bootstrap；UUID5 和 market 元数据 | L/D/P 完整性、交易所/板块口径 |
| 01.4 Critical Historical | manifest.py、history；按依赖和预算推进 | 各接口实际历史起点及全部可获得范围 |
| 01.5 Critical Validation | validate.py、daily；95% 边界与补抓测试 | 实际理论池和停牌数据权限 |
| 01.6 Financial Data | explicit financial schemas、版本测试 | 财务接口字段、公告过滤、修订可获得性 |
| 01.7 Industry & Index | audit、discover_metadata、index_universe | 历史稳定性，确定行业主体系 |
| 01.8 Market State Raw | suspend_d、stk_limit、namechange、stk_st、stk_share、dividend | 各 Required 接口和历史覆盖 |
| 01.9 Optional Whitelist | capability + registry + Gap | 白名单接口权限和分页 |
| 01.10 Incremental | current windows、cursor、repair、monthly_audit | 连续真实交易日/非交易日运行 |
| 01.11 GitHub Actions | 单 Workflow；UTC 12:00；恢复 Artifact；首次手动运行已验证失败阻断 | Secrets 配置、定时运行及真实数据成功验收 |
| 01.12 Automated Acceptance | validate CLI、工作流非零阻断、恢复测试 | 正式数据全量验收和故障演练 |
| 01.13 Google Drive | [Phase 2 发布合同](drive-phase2.md) | Phase 1 稳定后实施；当前未接通 |

实现代码和离线测试完成只代表开发交付，不能代表真实 Task 01 已最终验收。最终验收需要 Critical 实际可用、历史推进/补抓/修订经过实测、Actions 无人运行、失败保持旧版本，Drive 接通后 Production 只含已验收数据。

## 后续修改规则

用户冻结规格优先于仓库习惯。不要擅自改变 95% 阈值、关闭 Critical、增加外部数据源、把 Optional 失败提升为核心失败，或把核心失败降为警告。README 必须持续反映真实状态和未完成验收。
