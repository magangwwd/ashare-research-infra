# 后续实施提示词：Task 01 迁移至 GitHub Actions 每日增量更新

你处理仓库 `magangwwd/ashare-research-infra`。请先阅读 `docs/task01-actions-handoff-plan.md`，按方案实施并测试每日增量更新接管。当前任务不是策略研究或回测。

## 先检查，禁止从零重建或直接启用 schedule

1. 阅读根目录 `config/config.yaml`、`data_pipeline/`、`research_engine/` 的数据读取边界、Task 01 workflows，以及归档 `cloud_runtime/task01/README.md`、`snapshot_manifest.json` 和恢复适配代码。Task 02/BTC/ETF 等其他任务不得修改或运行。
2. 根目录 main 的主板 15min/独立全 A 股 daily 规则与云端归档的全证券分钟规划/分钟聚合日线规则不同。先查最近提交和真实生产证据，明确目标证券全集及价格源；**不回退 main，不把主板分钟当全 A 股覆盖**。存在口径冲突时汇报并在生产切换前解决，不猜测用户意图。
3. 历史保留范围：现有 Required/Optional 数据集从 2020-01-01 起，15 分钟从 2026-01-01 起；保留必要身份/有效关系上下文。当前归档使用 CPython 3.12 恢复二进制，不能当成已完成源码迁移；恢复为可审阅 Python 和对应测试，并验证兼容。
4. 查 GitHub 实际 workflow state/runs、云端实际 PID/heartbeat/锁、Drive 正式及 working 指针、最新成功 checkpoint。不能仅凭“active”、旧 RUNNING 文件或 cursor 标志判定健康/完整。归档清单只证明代码，不证明历史数据已完成。
5. 凭证只检查名称、绑定和真实调用结果。GitHub Secret 列表 API 403 不代表凭证缺失。目标 Drive folder_id：`1rkC99Ymgp8mjvAcJNRPJMlOGLdI9_bue`。使用 `TUSHARE_TOKEN`、`GDRIVE_OAUTH_JSON`；微信可选 `PUSHPLUS_TOKEN`。不要索取聊天中的值，禁止打印、入 Git、日志或 Artifact。

## 实现目标

实现适合 GitHub 临时 Runner 的 daily-sync：

写入所有权/租约 → 读取已验证 Drive manifest → 按需恢复 metadata/Clean 引用 → capability audit → 计算完整交易日缺口及有限修订窗口 → 下载本次增量 → 验证 → hash 去重上传变更分区 → 校验所有新旧引用 → CAS 最后提交 manifest/current → 脱敏报告 → 释放所有权。

- 不在 Runner 每夜遍历整个多年请求全集，也不整库恢复和整库重传。
- 兼容现有 legacy 和 working-2/bundle；明确正式版本与未完成工作 checkpoint，失败保留最后已验证 production。
- Drive 并发默认 1、请求间隔先 8 秒；403 userRateLimitExceeded/rateLimitExceeded 与 storageQuotaExceeded/dailyLimitExceeded 分开处理；429、5xx、网络正文中断支持有限指数退避+jitter+Retry-After。会话失效按规范重建；上传 journal 以内容身份区分。
- GitHub 所有 Task 01 写入任务共用 concurrency `task01-data`，不取消正在提交的运行。它约束不了 Codex；必须证明跨环境单写者屏障/有效租约及 current 条件更新。若云端还在写，默认只读，不发起第二个生产写入者。
- 同一天重跑幂等，不重复完成请求或同 hash 对象。用上海时区、真实 trade_cal 和提供者可用时间取最近完整交易日，不凭 UTC 日期或周一至周五推断。
- 按已确认的证券全集/价格源处理 daily；若采用分钟聚合，严格首/高/低/末与 vol/amount 单位转换，不伪造 pre_close/change/pct_chg。adj_factor 和 daily_basic 独立日更。
- 财务、指数、行业、停复牌、涨跌停、股本事件、现有白名单保留并做有限增量/修订。确认权限不可用才 CAPABILITY_UNAVAILABLE；空返回/网络失败不得当完成。
- Raw 短期保留、Clean 长期保留；有水合/缓存/暂存磁盘上限和安全停止，失败持久化 local/Drive checkpoint，不丢已验证对象。

## workflow 和验证

先实现仅 workflow_dispatch 的 preflight/dry-run/显式生产发布模式，最小权限与受保护 environment。**本轮不得直接开启 schedule，也不得停止正在正常写入的云端回补来抢占所有权**；先完成代码和测试，形成可审阅接管结果。后续明确批准切换后，执行单写者交接和手动 Runner 正式更新。

离线用模拟响应测试：新旧 manifest/bundle 恢复、真实 Parquet PK/覆盖、分钟上市退市/停牌/空响应、完整日 cutoff、403/429/Retry-After、正文断线、会话失效、低磁盘、去重续传、并发租约冲突、失败不动 current、同日重跑，以及 metadata 标志不能冒充历史完整。

真实 Runner 验证应从只读授权和容量、manifest 取证开始；再做隔离小范围试跑及完整 Drive recovery drill，validation 必须真实通过。日更验收只证明日更；全历史完整仍要求全部目标请求/日期检查、history_pending_requests=0、history_complete=true、all_enabled_requests_examined=true、market_history_complete=true、Critical>=95% 及全库恢复验收。

最终给出变更文件、测试通过/跳过/未跑、GitHub run/PR、Drive current 前后版本、最新数据日、各数据集增量/缺口、上传/复用数、源规则和单写者交接证据。观察至少 3 个真实开市日和一次幂等重跑后，再准备每日一次 schedule（建议北京时间21:30，以接口可用性为准）的独立授权变更；不恢复高频历史调度。

不要宣称“每天自动更新已接管”直到真实 Runner 和 Drive 恢复证据支持。若缺少权限、Secrets 或源规则未决，完成所有独立代码/测试后具体说明阻塞项。
