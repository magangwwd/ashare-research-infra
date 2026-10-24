# 可直接粘贴到 GPT 聊天窗口的执行提示词

请使用已连接的 GitHub/Codex 工具，实际完成仓库 `magangwwd/ashare-research-infra` 的 Task 01 每日数据更新和 Google Drive 存储，不要只提供方案或示例 YAML。

## 目标与授权

将每日数据更新交给 GitHub Actions：每天收盘后检查真实缺口、更新可获取数据、验证、增量保存到 Google Drive。后续日更不依赖当前 Codex 云环境在线。

本指令授权完成代码修改、测试、创建并合入通过验证的 PR，以及在满足下述验收条件和单写入者交接后启用每日一次 schedule。旧归档方案中的“本轮不启用 schedule”仅适用于当时的归档阶段；本次是生产日更实施任务。不得恢复高频历史回补 schedule，也不得在验证前启用生产写入。若权限或受保护分支规则阻止操作，完成独立工作并具体报告阻塞。

## 先检查已有成果

1. 检查当前 main、PR #93 的实际状态，以及 `docs/task01-actions-handoff-plan.md`、`docs/prompts/task01-actions-daily-handoff.md`、`cloud_runtime/task01/README.md` 和快照 manifest。PR #93 是运行代码归档与离线 CI，不代表生产日更已经实现。
2. 检查当前 Task 01 workflows、真实 runs、云端回补/watchdog 实际进程、Drive 正式及工作指针、最新成功 checkpoint。不能把旧 RUNNING 文件当真实运行，也不能把工作 checkpoint 当完整生产数据。
3. 历史范围：现有 Required/Optional 数据集从 2020-01-01 起；15分钟从 2026-01-01 起；保留必要证券身份和有效关系上下文。复用已有历史，不清空重建，不重复下载完成请求。
4. main 与云端归档的分钟证券范围、daily 价格来源不同。默认保留当前 main 已确认的生产规则，不静默回退；主板分钟不能代表全 A 股覆盖。若现有规则无法满足数据目标，先说明具体差异，再解决后切换。不得伪造 pre_close/change/pct_chg；adj_factor、daily_basic 独立更新。
5. 部分归档恢复依赖为 CPython 3.12 二进制。优先查找对应源码/提交并恢复为可审阅实现，保持旧 manifest/bundle 兼容；不能只复制字节码就宣称完成生产迁移。

## 凭证与存储

- Google Drive 目标 folder_id：`1rkC99Ymgp8mjvAcJNRPJMlOGLdI9_bue`。
- 从 GitHub Secrets/Environment 绑定 `TUSHARE_TOKEN`、`GDRIVE_OAUTH_JSON`；微信可选 `PUSHPLUS_TOKEN`。验证实际授权与可用容量，不在聊天、Git、日志或 Artifact 中输出凭证。Secret 列表 API 返回403不等于 Secret 缺失。
- 缺少凭证时明确名称和安全配置位置，不伪造、不索取聊天中的明文值。

## 每日更新实现要求

1. 实现并验证适合临时 Runner 的日更入口：恢复已验证 manifest/必要 metadata及本次所需分区 → capability audit → 按上海时区和 trade_cal 确定最近已完整结束且接口可用的交易日 → 补真实缺口和有限修订窗口 → validation → 增量上传 → 验证所有引用 → 最后提交正式 current 指针。
2. 覆盖 daily、adj_factor、daily_basic、15分钟及当前启用的财务、指数、行业、停复牌、涨跌停、股本事件和白名单数据。不同接口按其日期语义和更新周期增量更新。权限确实不可用才标记 CAPABILITY_UNAVAILABLE并记录原因；空返回、网络失败不能直接标完成。
3. 同日重跑幂等；不每天扫描或下载整个多年请求全集，不整库恢复、整库上传。兼容 legacy及working-2/bundle；未修改分区复用原hash/远端引用。Raw短期保留，Clean长期保留；设置磁盘、请求数和运行时长预算并可恢复。
4. Drive共享限速器，上传并发先1、API请求间隔先8秒，遵守Tushare限流。分类处理403/429、配额耗尽、5xx与网络错误；支持指数退避、抖动、Retry-After和可续传会话失效恢复。
5. SHA256和大小验证成功后才引用对象；manifest全部有效后才更新current。提交需有实际有效的条件更新或等价串行发布保证；禁止GET后无条件PATCH冒充CAS。失败保留旧生产版本和已验证上传进度，不删除已上传有效对象。
6. 所有Task 01写入workflow共用`concurrency: task01-data`、`cancel-in-progress: false`。该锁不能约束Codex，必须验证跨环境单写入者租约或完成可核验的写入所有权交接。不得为了试跑强杀正在正常运行的回补；先完成独立代码和隔离测试，若正式交接需要停止现有回补/watchdog，报告具体影响并获得单独确认。

## Actions落地与验收

- 同时支持workflow_dispatch及每日一次schedule，建议北京时间21:30（UTC 13:30），以真实数据可用时间为准。先只读预检和隔离试跑，满足验收与单写入者条件后再启用生产schedule；只保留一个每日触发来源，消除重复生产调度。节假日安全跳过，失败后下次补漏，不能依靠高频schedule重试。
- 离线模拟测试覆盖限流/退避、断线和会话失效、低磁盘、hash去重、失败不动current、并发冲突、新旧格式恢复、真实Parquet PK/覆盖、最新完整交易日及同日重跑。
- 必须在真实GitHub Runner至少成功完成一次受控更新及一次同日幂等重跑，并从Drive恢复到隔离目录重新validation通过；提供run链接、数据日期、Drive版本、上传/复用数量及缺口证据。不能把CI通过、workflow active或YAML已合并当生产成功。
- 日更可在历史未全完成时只更新已有生产基线的新日期，但必须保留并单独报告历史backlog，不能因此宣称全历史完成。若无可用生产基线，先修复基线；历史请求全部检查、实际Parquet验证和完整Drive恢复验收通过后才报告历史完整。
- 启用后继续观察至少3个真实开市日，保存每日验收报告；观察尚未完成时如实说明，不能凭未来计划宣布稳定运行。不要为了等待3天而放弃当前可完成的实施和验证。
- 不运行策略、回测、Task 02、BTC、ETF；不重建云环境，不破坏最后一份已验证生产数据。保留现有停滞微信告警禁用设置；可选复用已授权的每日结果/最终失败通知，不新增未经授权的消息接收方。

## 最终报告

给出代码提交/PR及合入状态、真实Runner链接、schedule与下次计划时间、最新完整数据日、各数据集状态和历史缺口、Drive current版本和恢复验收结果、生产单写入者证据、监测安排及所有未完成事项。

如果当前聊天窗口只有只读GitHub连接、不能执行代码或调用Actions/Drive，明确说明实际能力边界，将任务交给具备仓库写入和运行能力的Codex会话；不得编造执行结果，也不要只以“方案已提供”结束任务。
