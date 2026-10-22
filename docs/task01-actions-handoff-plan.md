# Task 01：云端代码归档与 GitHub Actions 每日更新接管方案

日期：2026-10-22。目标分两步：先保存当前云端可恢复运行代码；再实施每日增量更新。**本次不宣告历史完成，不启动生产接管，不启用任何 schedule。**

## 本次实际交付

- `cloud_runtime/task01/`：当前 Codex Task 01 代码、显式配置、离线测试、恢复所需的校验过的 CPython 3.12 缓存文件，以及文件大小/SHA256 清单。不是数据库备份，也不是对根目录生产代码的覆盖。
- `.github/workflows/task01-cloud-snapshot-ci.yml`：仅离线验证上述快照；无生产凭证、Drive/Tushare 写入及定时触发。
- `docs/prompts/task01-actions-daily-handoff.md`：交给后续实施会话的完整提示词。
- `scripts/export_task01_cloud_snapshot.py`：按白名单导出，排除数据、凭证、运行状态和 Task 02。

原地运行的 `data_pipeline/` 及本地修改保持保留；本次只提交隔离的代码快照及方案，避免重置当前运行现场。嵌套目录中的 `.github/workflows/` 是原有测试读取的 YAML 样本，GitHub 不会将它们识别为仓库根目录的可执行 workflow。

## 已核实的差异与限制

1. 远端 `main` 已改为 `intraday.universe: mainboard`，且 `activate_daily_from_minutes: false`。云端恢复引擎仍是全证券请求规划和分钟聚合日线规则。两者不等价；**不得用主板分钟价格替代全 A 股 daily**，也不能借同步代码回退 main 的新规则。接管前明确目标证券全集和价格主来源；存在未决差异时保留 main 的生产行为并阻止切换。
2. 历史目标仍为：其余既有 Required/Optional 数据从 2020 年起，15 分钟从 2026 年起；基础证券身份和区间内仍有效的关系上下文应保留。不能把日期边界缩短误作删掉数据集。
3. 云端 `backfill-working-v1/current.json` 实际使用 `working-2`，支持打包对象的逻辑分区引用。它是未完成历史的工作 checkpoint，不能作为“历史已完成”的正式凭证。正式根 `current.json` 与工作指针必须区分。
4. 当前恢复快照使用 `data_pipeline/recovered_oct08/*.bin`；Python 版本必须固定 CPython 3.12，魔数和 SHA256 必须校验。这保存了灾难恢复能力，但**尚不满足全部核心逻辑可源码审阅的接管目标**。后续将运行逻辑和测试恢复为可审阅 Python，保持旧数据格式可读。
5. 仓库 API 在本次检查中显示 `task01.yml` 和 `task01-backfill.yml` 为 active；这不同于早期“已暂停”的描述。本次不改变其启停状态。接管实施前核查真实 runs、版本、写入目录及 watchdog 触发，不靠本地 YAML 或历史聊天判断是否运行。
6. Git 读写和仓库信息 API 可用；读取 Actions Secrets 名单返回 `403 Resource not accessible by integration`。这不表示 Secret 缺失。后续通过设置界面确认绑定，并通过只读授权测试验证可用性，禁止索取、打印或导出 Secret 值。

## 阶段 A：代码与基线验收

- 使用已有 checkout；不 reset 云端工作目录，不影响正在运行的回补，不运行策略/Task 02/BTC。
- 在独立临时目录安装 CPython 3.12 依赖，核验快照清单和所有恢复二进制魔数/SHA256，执行离线测试。不要把 live 测试跳过描述为真实 API 已通过。
- 从正式指针和最新成功工作指针只读取证：版本、数据截止日、manifest schema、物理对象与逻辑分区、完整性报告、schema/metadata。保持最后已验证正式版本不变。
- 历史完成必须基于目标交易日/证券请求全集、实际 Parquet PK/覆盖/日期缺口和最终恢复演练。`history_pending_requests=0`、`history_complete=true`、`all_enabled_requests_examined=true`、`market_history_complete=true`、Critical>=95% 及 Drive 完整恢复验收需同时成立。未知不能转为 true。
- 如果仍未完成：继续当前可恢复回补或明确规划有预算的人工批次。不得每夜重跑全部历史，也不能把每日最新成功等同全历史成功。历史回补与日更若暂时并存，必须划清分区和指针的写入所有权。

## 阶段 B：实现 daily-sync，而不是把 full-backfill 搬到 Runner

建议新建显式入口 `python -m data_pipeline.daily_sync --config ... --root ...`。参数和最终模块名以实现为准；本次尚未实现此入口。

流程：取得写入租约 → 从 Drive 已验证 manifest 恢复必要 metadata/分区引用 → fresh capability audit → 计算已完整结束交易日的真实缺口与有限修订窗口 → 下载/更新 → 验证 → 仅上传改变的内容寻址对象 → 验证新 manifest 的全部引用 → 最后 CAS 更新正式 current → 留存报告并释放租约。

要求：

- Runner 不恢复全部多年 Raw/Clean；对本次需要的分区按需水合，保留未触及分区的 hash/引用。Raw 短期保留，Clean 长期保留。
- 不允许使用旧整库上传流程覆盖新版 working-2/bundle 数据。支持向后读取；新 schema 明确版本、大小、SHA256、bundle offset、metadata 与验证证据。
- 同 hash 不重传；上传 journal 按内容身份隔离，支持会话 404/410 重新建会话；网络正文中断、403/429 分类、Retry-After、指数退避和随机抖动均需测试。Drive 请求共享限速器，初期并发 1、请求间隔 8 秒，按实测再调。
- GitHub `concurrency: task01-data` 且 `cancel-in-progress: false`；此锁不能约束 Codex，必须再实现/验证跨环境生产写入租约。Drive current 条件更新需实际有效；先 GET 再无条件 PATCH 不能冒充 CAS。未能证明可用原子租约/CAS 时，使用可验证的“云端写入已退出并停用、仅 Actions 写入”接管屏障，拒绝并行发布。
- 同一天重复运行应幂等；按上海时区、trade_cal 和数据接口可用时间选择最新完整日；节假日不造新数据，也不能遗漏上一次失败的真实缺口。
- daily 来源遵循已确认的证券范围决策。若保留 mainboard 分钟，则全 A 股 daily 独立保留；若未来明确改为全 A 股分钟主源，必须先证明全目标池分钟链完整，之后才按首/高/低/末聚合，并验证 vol/amount 单位。不得伪造 pre_close/change/pct_chg。adj_factor、daily_basic 独立更新。
- Required/Optional 接口不可用仅在 fresh audit 证明权限不可用时记录 CAPABILITY_UNAVAILABLE 和原因；网络故障/空返回不能当权限不可用。可用接口按适当日期/修订窗口增量更新，并报告未决 backlog。
- 每次日更验证本次变化分区和最新 Critical；定期另做完整数据库恢复演练，不能因 Runner 时限取消历史验收，也不必每晚重新下载整个库。

## 阶段 C：Actions 验证和切换

1. 新 workflow 先只有 `workflow_dispatch`，默认只读 preflight/dry-run；生产写入使用受保护的 `task01-production` environment 或等价的明确发布门槛。
2. 最小权限 `contents: read`，按确需设置 artifacts/actions 权限。生产凭证仅来自 Secrets/环境绑定：`TUSHARE_TOKEN`、`GDRIVE_OAUTH_JSON`，微信可选 `PUSHPLUS_TOKEN`。PR 离线 CI 不注入这些值。
3. 在 Runner 真正测试：只读授权/容量、manifest 恢复、小日期区间试跑、断线/限流/低磁盘/失败不动指针、同日第二次无意义请求和上传为零、从已验证 Drive 版本恢复后 validation。
4. 准备可审阅的接管报告：云端最后写入 PID/状态、Drive 最后成功版本与截止日、未决缺口、源规则、日更新耗时/API预算、回退版本。确认单写者屏障后再让首个 Runner 写入；保留旧正式版本和云端本地 checkpoint。
5. 至少观察连续 3 个实际开市日成功，其中包含一次幂等重跑；证明数据日、Critical 覆盖和 Drive current 正确。此后才提交启用每日一次 schedule 的独立变更，不能本次直接开启或恢复高频 schedule。
6. 建议时间为北京时间 21:30（UTC 13:30），但最终以实际 Tushare 数据可用性和完整交易日规则为准；schedule 只是尽力触发，手动补跑共用同一锁和同一幂等入口。不要恢复每几分钟历史回补调度。

## 报告与告警

保留数据日、成功/失败/冷却阶段、各数据集增量行数、目标/完成/待处理请求、真实缺口、Drive 逻辑文件数/物理对象数/字节、上传/复用数、SHA 验证、current 前后版本、Runner 时长和恢复演练结果。Artifacts 只放脱敏报告，不打包 Token/OAuth、完整运行环境或多年数据库；恢复以 Drive 为准。

现有“长时间没有进度”微信告警保持禁用。单次 API 重试/计划冷却不是停机；仅针对最终失败、租约冲突、数据日落后或验收失败发送有去重的结果通知。整个云环境离线的外部监测与 Actions 日更故障告警分开。

## 最终放行条件

代码合入、Secrets 真正可用、新格式完整恢复和验证、源/证券全集口径已确认、单写者屏障生效、每日幂等试跑成功、历史完整性未被掩盖，并获得对启用 schedule 的明确授权。任何条件未知时报告阻塞项，不把合并归档 PR 当成生产已接管。
