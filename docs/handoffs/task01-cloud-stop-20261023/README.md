# Task 01 云端停机交接档案（2026-10-23）

用户明确要求：本项目本地进程全部停止，停止前先完成 handoff 存档。本目录是接手依据；**不是历史完成证明，也不表示 GitHub 已可靠接管生产**。

当前阶段：**已在存档及恢复验证后，按用户要求停止全部本地 Task01 服务**。停止时间：`2026-10-23T07:10:01.672284+00:00`。`stop-verification.json` 记录实际信号、进程归零和 STOP 保护；`handoff.json` 已更新。旧恢复包中的 RUNNING/BLOCKED 是停机前证据，不是实时状态。GitHub 生产接管尚未通过本次停机认证。

## 已完成的停机前存档

- 云盘恢复包：[打开文件](https://drive.google.com/file/d/1PAfWwbMbIpYnGqJqf3agkZ1ZfCj_l1CK/view)，需要现有 Drive 授权；未新增公开分享权限。
- 目标目录：`task01-handoff-archives`，folder ID `171cgvEJNff-OcfikVyeeMMCfEa4CV6Ef`。
- 文件 ID：`1PAfWwbMbIpYnGqJqf3agkZ1ZfCj_l1CK`。
- 文件名：`cloud-stop-20261023-318aa4c36885b286258fac765f5cb49bed851fe0b3e8f2eb738e0f08e125c515.tar.gz`。
- 大小：**30,430,548 bytes**；SHA256：`318aa4c36885b286258fac765f5cb49bed851fe0b3e8f2eb738e0f08e125c515`。
- 归档内 **218** 个受清单校验的文件；源码、显式配置、测试、metadata、JSON/SQLite 回执、Drive 引用索引、工作指针及脱敏故障记录均已保存。SQLite 使用 backup API 一致性快照，未直接拷贝活动 WAL。
- 已实际从 Drive 下载恢复包，校验整体和全部包内文件 SHA256，检查恢复后的 SQLite integrity；**仅恢复包演练通过，全历史数据库恢复演练未通过本次交接执行**。
- 未包含 OAuth、Token、环境变量转储、可续传 session URL；历史 Parquet 和缓存未重复打包，按现有 manifest 引用恢复。额外留存约 651 MB Raw 不属于长期保存目标，本地仍原样保留，未删除。
- 云端实际源码的隔离归档另见 `cloud_runtime/task01/`。其中部分核心实现为固定 SHA 的 CPython 3.12 恢复二进制，不能当作可审阅源码迁移已经完成。

本地同份材料：`/workspace/task01-handoff-20261023T065808Z`。本地数据根：`/workspace/ashare-task01-resumed-20261018`。**不要因为进程停止就删除这些目录**；后续另行确认完整数据库恢复与保留策略。

## 最新工作 checkpoint

- Drive 工作目录 ID：`1R3Y0GdYzthmsSHRg2ufRo-aLdwGfN-Bc`；工作 `current.json` 文件 ID：`1eMos543XPXcAA2c6Q72ifOAyQoUm8Mzs`。
- Manifest 文件 ID：`17tsRQv8MsCN9VteI1GkzKDaa4NKKSjiF`；schema：`working-2`。
- Manifest 时间：`2026-10-22T21:19:41.301794+00:00`；大小 `2,276,740` bytes；SHA256 `eb2596b6d988aa52aa45994e4670bba2827ae06f8306b96bc31b73ee24fa4e8c`。
- **5,682 个逻辑文件引用，逻辑大小 2,507,546,653 bytes**；不能将它当作物理 Drive 对象数或已完成全库验收的大小。
- 本次从 Drive 读取并核验 manifest，匹配本地工作版本。逐本地文件比对发现：**没有未纳入 checkpoint 的 Clean 文件**。这证明本地 Clean 的内容/引用与清单一致，不能代替全部远端对象重新下载校验。
- 支持 bundle 中的逻辑分区；用支持 `working-2` 的 reader 解析 offset/hash。`.parquet` 可能是远端 stub，不能直接交给 pyarrow 作为真实 Parquet。
- 工作指针与正式根 production `current.json` 不同。本次未更新正式指针，未删除任何既有 Drive 数据。

## 尚未完成的内容

- 停机前主下载进程已退出，watchdog 声明 `BLOCKED`；故障类型 `ValidationError`，最后失败时间 `2026-10-22T22:06:05.355835+00:00`。保存了调用栈位置，原实现未保留具体异常消息，不能编造详细原因。
- `full_backfill_run.json`：`PASS_FINISHED` 只表示该轮结束；`market_history_complete=false`。旧目标截止 **2026-10-12**，不是“截至当前已完整”。
- 该旧计划 `daily` 尚欠 **4** 个请求；`stk_mins` **5243/5593**，尚欠 **350** 个请求。其他数据集的回执 pending=0，不等于接口权限、行级完整性和最终验收均通过。
- 2026-10-12 之后的交易日需要重新按 trade_cal/provider 检查并补齐，不能包含在上述 354 个待处理请求中作虚假总量承诺。
- 最近核验的 GitHub Task01 [run 39864704830](https://github.com/magangwwd/ashare-research-infra/actions/runs/39864704830) 在 `Publish accepted snapshot to Drive` 失败。归档 PR #93 在检查时未合并；之后应重新查真实状态。
- `history_pending_requests=0`、`history_complete=true`、`all_enabled_requests_examined=true`、`market_history_complete=true` 和全库 Drive recovery drill 尚未联合满足，不得报告历史完成。

## GitHub 接手步骤

1. 从本目录 `handoff.json`、`stop-verification.json` 及真实云端进程确认停止事实；只读恢复阶段不得启动旧 watchdog。将来创建新云环境时也不得自动恢复本回补，除非生产写入所有权明确交还。
2. 从 Drive 下载上述 handoff 包，先核验整体 SHA256和大小，再在全新的隔离目录安全展开；拒绝绝对路径、`..`、symlink/hardlink 和重复成员。以包内 `archive-members.json` 校验所有成员。
3. 优先使用 GitHub 可审阅实现；如仅为取证运行恢复副本，固定 CPython 3.12 并验证恢复二进制 SHA。严禁无意执行其 full-backfill/watchdog 命令。离线快照测试使用 `cloud_runtime/task01/` 及其嵌套 YAML 测试样本。
4. 保留 `state/metadata/manifest/requests.json` 与经 backup 的 `requests.sqlite`；先理解新旧回执覆盖规则，勿只选一个旧文件而丢进度。将包内状态恢复到独立临时 root，核对数据版本、目标日期、metadata 和 Drive 索引；不要直接覆盖当前生产状态或导入旧 PID 文件作为实时状态。
5. 使用 GitHub Secrets 的 `GDRIVE_OAUTH_JSON`、`TUSHARE_TOKEN` 只读验证授权和容量。下载工作 manifest，逐逻辑文件验证大小/SHA，按需水合 Clean；全库验收时检查实际 Parquet PK、交易日缺口、覆盖及恢复 validation，不能把 manifest 自身 SHA 通过当成数据通过。
6. 解决 `ValidationError`、旧计划缺口和后续日期；保留 main 的已确认证券全集/价格源，不能把主板分钟当全 A 股 daily。先恢复可审阅源码并证明新旧格式兼容，再批准生产接管。
7. 按 `docs/prompts/chatgpt-task01-daily-update.md` 实现有限增量日更、单写入者控制、hash 去重和最后提交 current。Actions 共用 concurrency 但它本身不约束云端；确认 STOP 后不再有云端写入者，避免重启旧服务。
8. 真实 Runner 受控更新、同日幂等重跑及 Drive 恢复验收成功后，按用户授权完成每日一次调度交接。本次只停止本地服务，**不修改 GitHub workflows 的启停/cron，不把现有 active 状态当日更成功**。

## 本次边界

只归档和停止本项目的 backfill、watchdog、微信通知、状态观察及存在时的外部心跳服务。保留源代码、Clean、Raw、metadata、本地 checkpoint 和既有远端对象；不操作平台 agent/tunnel，不重启容器，不运行 Task02/研究/回测。
