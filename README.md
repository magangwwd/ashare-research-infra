# ashare-research-infra

Task 01 只建设 **稳定、可恢复、可自动更新的 A 股研究数据底座**：

`Tushare → Raw → Clean → Parquet → Validate → GitHub Artifact`

冻结需求、实现对应关系和未完成的外部验收见 [docs/task01-spec.md](docs/task01-spec.md)。后续 Codex 请先阅读本文件，再修改代码。可靠性、恢复能力和数据完整性优先；保持少量清晰模块。

## 当前状态

Phase 1 实现已推送到 GitHub 私有仓库，20 项离线测试通过。2026-10-02 已执行[首次 Actions](https://github.com/magangwwd/ashare-research-infra/actions/runs/36984398679)：Runner 安装和单元测试通过，真实 Smoke 因 `TUSHARE_TOKEN` 未注入而失败；Workflow 正确显示 FAILED，跳过正式数据发布。详见[验收记录](docs/acceptance-record.md)。

**真实 Tushare capability audit、live smoke、全量历史下载及正式数据验收尚未完成**。先在仓库 Actions Secrets 配置 `TUSHARE_TOKEN`，再手动运行 Workflow。代码和离线测试通过不等于真实数据验收通过；历史任务分批推进，不承诺一次运行完成整个历史库。

Phase 2 Google Drive 尚未启用，必须等 Phase 1 真实验收稳定后实施，见 [Drive 发布规格](docs/drive-phase2.md)。没有 Drive 凭据、远程 Production 或伪造的成功报告。

## 范围

- Critical：`stock_basic / trade_cal / daily / adj_factor / daily_basic`。不可禁用，失败返回非零，Workflow FAILED。
- Required：Security Master、财务、行业、主要指数与成分历史、名称/ST 原资料、停复牌、涨跌停、股本变化及分红资本事件。暂时失败记录 Gap，继续核心流程。
- Optional 白名单：融资融券、大宗交易、龙虎榜、股东人数、股东增减持、机构调研、限售解禁。权限和接口可用才下载，失败不阻止核心发布。
- Candidate：质押、回购等仅登记，不下载。

Task 01 不实现因子、技术指标、选股、主升标签、ML/Ranking、回测、Walk-forward、交易、15 分钟数据、新闻/NLP、AI Agent、DuckDB、完整 Lineage、复杂日志、严格 Point-in-Time 或高级质量平台。不展开每日 ST、停牌和指数成员状态，不计算上市天数、QFQ/HFQ 或 Market Regime。不建立 `curated/factors/models/backtest` 空目录。

## 安装与入口

Python 3.12，使用简单 requirements，不使用 Poetry/Conda/uv。

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest -q
```

通过环境 Secrets 或安全的本地环境注入 `TUSHARE_TOKEN`，不要写入源代码、YAML、日志、Artifact 或聊天。没有 Token，真实任务会失败退出。

```bash
# 少量主板股票、最近两个交易日，隔离临时目录，走完整真实数据链。
python -m data_pipeline.run_task01 --mode smoke

# smoke 成功后，自动区分 DAILY_UPDATE / MAINTENANCE。
python -m data_pipeline.run_task01 --mode auto

# 单独检查最后一个正式版本；返回 0 / 1。
python -m data_pipeline.validate
```

Smoke 结果绑定当前 Python 代码指纹；修改代码后必须重新执行真实 smoke。离线 API 替身测试不会生成 live smoke 通行标记。`--root /path` 可分离数据目录，`--config path` 可指定配置。单独验收支持 `--as-of YYYY-MM-DD`，常规运行日期使用 Asia/Shanghai。

## 代码导航

| 模块 | 职责 |
| --- | --- |
| `download.py` | Token、限流、重试、分页保护、接口能力快照、数据等级及依赖排序 |
| `clean.py` | 显式字段映射、Schema 版本、Date/dtype、主键、财务版本和 Security Master |
| `manifest.py` | Hive 分区、ZSTD/compact、Parquet 扫描、Manifest/Gap、可恢复暂存与原子切换 |
| `validate.py` | 基础质量、覆盖率、Critical 总验收和 CLI |
| `run_task01.py` | Smoke、当天优先、分类回补、扩展更新、修复、历史预算和月度审计 |

普通参数在 `config/config.yaml`，敏感参数只读环境变量。

## Raw / Clean 和 Schema

Raw 保留原字段及原日期格式，包括 Tushare 新字段。只额外增加 `_fetched_at`，不更改源字段含义。财务 Raw 保留每次获得的公告/修订版本，无法获得的历史版本不伪造。

Clean 仅包含 `clean.py::SCHEMAS` 显式声明的字段；未知源字段不会自动进入 Clean。Schema 当前为 `1.0.0`，每行带 `schema_version`。运行时在 `metadata/schemas/` 导出 `source_field`、`clean_field`、dtype、description、主键和规则。新增字段优先兼容升级，重大结构变化才升级主版本。

日期使用 Arrow `date32`，更新时间使用 UTC timestamp；Raw 日期仍为原格式。财务映射 `end_date → report_period`，保留 `ann_date / actual_ann_date`；预留 `available_date`，Task 01 不擅自推定可用时点。

| 核心数据 | Clean 主键 | 自然分区 | 清洗规则 |
| --- | --- | --- | --- |
| stock_basic | security_id | snapshot=current | L/D/P 状态共同采集，重复保留最后一条 |
| security_master | security_id | snapshot=current | UUID5 主键；保留 exchange/symbol、上市/退市、行业和原始名称 |
| trade_cal | exchange + calendar_date | year | SSE、SZSE；is_open 为整数 |
| daily | security_id + trade_date | year/month | 原始 OHLC、成交量额，重复保留最后一条 |
| daily_basic | security_id + trade_date | year/month | 原估值、市值、股本等字段，不计算因子 |
| adj_factor | security_id + trade_date | year/month | 原复权因子，保留最后一条 |

`security_id` 由 Tushare 证券标识生成稳定 UUID5，不随公司名称变化。主板依据 `stock_basic.market == 主板` 和明确的交易所元数据识别，不依据数字代码前缀。`mainboard_pool()` 只提供基础主板池；严格研究股票池属于 Task 02。ST 原资料保存名称变化和可用 `stk_st`，不展开每日状态。

财务 Raw 分区 `ann_year/ann_month`；Clean 分区 `report_year/report_period`，按公告日、实际公告日及 update_flag 选择当前最新版本。报表还按 `report_type / comp_type` 区分口径。Raw 供以后重建 PIT，Clean 不是严格 PIT 数据。

所有文件均为 ZSTD Parquet。每次自然分区合并前后都验证；compact 根据实际压缩体积估计分片，目标 192 MB（128–256 MB 范围内），小分区不强制拆分。行情 Raw 按自然月清理约 180 天前分区；财务和元数据 Raw 长期保留，Clean 长期保留。

## Manifest、恢复和发布

数据事实来源是 **实际 Parquet**，不是 JSON 中的 complete 标签。启动扫描生成 `metadata/manifest/partitions.json`，记录 dataset、layer、partition、日期范围、rows、updated_at、status、validation_status、文件 SHA256。JSON 损坏可由扫描重建。历史行情还按交易日重新检查股票覆盖率，因此覆盖不足的现存文件会补抓。

`requests.json` 记录完成的有界请求（包括合法空结果）及所在分区；`gaps.json` 记录失败请求和 missing_ts_codes。非行情请求回执丢失时保守重抓无法证明完成的范围，不把一次空公告期当作错误。分区损坏会使对应请求回执失效，进入恢复。不要手工把 status 改为 complete 来跳过验证。

正式读取路径始终为 `data/raw/` 和 `data/clean/`。实现使用内部 `data/.versions/<run>/raw|clean` 的不可变版本；`data/raw → current/raw`、`data/clean → current/clean`。新运行以硬链接建立暂存副本，所有分区写入采用新文件替换，绝不原地修改共享 inode。失败保留暂存和 Gap；下次继续同一暂存。

只有 Critical 总验收通过，才以 `os.replace` 一次切换 `data/current`。失败时上一正式指针和文件不变，首次失败不会创建可读的正式数据。成功保留当前和上一版本；内部版本是发布机制，不是第三层 Curated。Manifest 属于工作状态，**消费者以 `data/current/acceptance.json` 判断正式版本**。

## 调度、历史下载和修订

启动执行完整 Capability Audit，快照存放 `metadata/capabilities/`，记录检测日期、权限和 AVAILABLE/UNAVAILABLE；接口错误不输出 Token。Optional 不可用自动跳过。行业检测 SW2021、SW2014、CITICS，保留可用体系的元数据与原始成员历史。主体系默认空，必须有真实历史覆盖与稳定性证据后再在 `industry.primary` 指定，辅助体系同时保留。

指数从可获得的 `index_basic` 按配置中的主要宽基名称发现，并写入 `config.yaml::index_universe`，没有硬编码指数代码。行情/成分历史按该配置抓取；Tushare 有的成分接口是定期权重快照，Task 01 不将它假装成完整每日成员状态。

单次运行顺序：核心基础信息和日历 → 最新交易日三个 Critical → 各类型近期回补 → Required → Optional → Gap 修复 → 有预算历史下载 → 月度审计 → 总验收 → 发布。非交易日为 MAINTENANCE，同入口检查最近交易日，并执行低频刷新、修复及历史维护。

- daily / adj_factor：最近 5 个交易日；daily_basic：最近 15 个交易日。
- 财务：轮转检查最近约 400 天公告，抓取后覆盖 Clean 最新版本，Raw 保留实际获取版本。
- 扩展数据有独立预算和持久游标，避免几千只股票的财报查询阻塞核心当天更新。
- 历史默认从 1990 年开始；日历分年、行情按交易日有界请求/按月落库、财务按证券/年、指数按月。依赖由声明自动排序，多数据集轮转推进，默认每次最多 40 个历史工作单元，失败后不重跑完整库。
- Critical 历史起点从实际非空响应探测，不写死接口起始年份。探测假设非空可用区间连续，快照记录该假设；后续空洞仍进入 Gap，边界可由实际权限结果复核。
- 每次扫描全库的文件结构；每月重新抓取一段轮转的旧行情来发现修订。预算默认为 10 个工作单元，是滚动审计，**不是每月重新下载全部历史**。

API 自动重试、限速、分页。分页重复或超过上限直接失败，避免将截断结果误作完整。历史下载量和接口权限依赖实际 Tushare 账号；`metadata/manifest/history.json` 显示当前推进情况。任一 Critical 下载/验证失败，整个运行退出非零；Required/Optional 错误只记录 Gap。有限历史批次完成不代表整个 Task 01 历史验收完成。

## 验收规则

总验收检查 Critical 文件存在/非空、Schema、ZSTD、Date/数值类型、主键唯一，以及最近交易日的数据和股票覆盖率。

理论股票池使用证券主表上市/退市区间和可获得的当日停牌原记录；不只用当前上市股票重建历史池。停牌接口不可用时用保守的上市池，可能导致验收失败，不能假称已获得完整应交易池。

首次全市场请求后对 missing_ts_codes 逐股补抓，再验收 **本次响应及补抓结果**。旧文件不能补足本次坏响应。最低覆盖率固定不低于 95%；最少行数按理论池规模计算并设下限；关键价格/股本字段空值比例默认不得超过 5%。最终小于 95% 禁止发布，大于等于 95% 可发布但残余缺口仍留在 Gap。

不引入高级 OHLC 数学规则、分布检测、漂移或质量评分。日志只输出 SUCCESS/FAILED 和必要错误原因。

## GitHub Actions

唯一 Workflow：`.github/workflows/task01.yml`。`schedule` 为 `0 12 * * *`，即北京时间约 20:00；GitHub 可能延迟启动。另有无参数 `workflow_dispatch` 手动触发。

在仓库 **Settings → Secrets and variables → Actions** 添加唯一业务 Secret `TUSHARE_TOKEN`。Workflow 使用 GitHub 自动提供的 `github.token` 读取同仓库 Artifact，它不是额外的业务凭据。

每次运行：安装 → Unit Tests → 恢复最新 checkpoint → 真实 Smoke → 自动模式 → 正式版本验收 → 发布 validated Artifact。任何 Critical/Smoke 验收失败，后续发布步骤不会运行，Workflow 保持 FAILED。

- `task01-validated-data`：只有成功时上传，包含经验证的正式数据与 Schema/能力/验收资料。
- `task01-checkpoint`：成功或失败都保存，包含最后正式版本及未完成暂存、Manifest、Gap；**不是正式数据发布产物**。下次 Runner 从最新未过期 checkpoint 恢复。
- 未成功恢复旧检查点时禁止保存部分恢复状态。取消/强制终止可能来不及保存新 checkpoint，下次使用上一个已上传的完整检查点。
- Phase 1 Artifact 有 GitHub 存储配额和保留期限（正式 30 天、恢复 90 天）；不是永久存储。大规模历史库应在 Phase 1 实测稳定后进入 Drive Phase 2。超过保留期且没有备份时，无法恢复已过期的 Runner 本地状态。

从 Actions 页面手动运行并查看失败原因。不要使用 continue-on-error 把核心失败变绿，不要把恢复检查点作为 Task 02 输入。

## 后续 Codex 的接续点

1. 配置 Token，真实执行 Smoke/Capability Audit，复核接口字段、分页及权限。
2. 运行每日与非交易日模式，确认 Actions 的恢复、故障保留和正式 Artifact。
3. 分批完成可获取的历史范围，消化 Critical Gap，按真实数据检查财务/行业历史，确定行业主体系。
4. Phase 1 稳定后按 Drive 规格实现认证、staging、远程完整性验证、Current Pointer 和近似原子发布；上传失败时保留旧 Production。
5. Task 02 只读 `data/clean/` 或经验证的正式 Artifact，从 `data/curated/` 与 `factors/` 开始，实现严格股票池、每日状态、上市天数、动态复权及第一批因子。

这些接续工作必须基于真实验收记录，不得把离线测试或已编写代码标记成全量数据已验收。
