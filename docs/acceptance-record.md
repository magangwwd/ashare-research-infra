# Task 01 验收记录

## 2026-10-02：连续两次真实运行成功

仓库为私有 `magangwwd/ashare-research-infra`，默认分支 `main`。以下数据与代码提交 `eabc93dbde658456f4dc405310e8ff1ead87c7b2` 对应；Token 只存储于 GitHub Actions Secrets。

| 检查 | 首次完整发布 | 恢复后续传 |
| --- | --- | --- |
| Actions | [36992443218](https://github.com/magangwwd/ashare-research-infra/actions/runs/36992443218) | [36993652567](https://github.com/magangwwd/ashare-research-infra/actions/runs/36993652567) |
| 触发/模式 | 手动 / MAINTENANCE | 手动 / MAINTENANCE |
| 单元及离线集成测试 | 32 项通过 | 32 项通过 |
| 真实五类 Critical 能力审计、Smoke | 通过 | 通过 |
| 更新、总验收、正式发布 | SUCCESS | SUCCESS |
| 本次历史请求 | 40 | 40 |
| 已有完整请求跳过数 | 2 | 45 |
| 历史遍历 cursor | 40 | 57 |
| 全部历史请求遍历完毕 | false | false |
| 正式 Artifact ID | 11221005687 | 11220787377 |
| 独立核对的 Parquet 文件 | 256 | 262 |

cursor 是启用请求序列中的位置，会受去重和可用范围影响，不等于累计下载天数。第二次运行恢复已有状态、跳过完整请求后继续下载；不是重抓全部历史。两份下载产物均逐文件核对 Manifest 记录的大小、SHA256，并在本地对解压后的数据再次执行 `validate_task01`，结果通过。

## 已验收正式数据

第二次产物中的最近交易日为 **2026-09-30**，理论应交易池 **5,559** 只，三类核心行情的 missing_ts_codes 均为空。覆盖率按理论池交集计算，数据源还返回部分额外记录，因此原始行数可以大于理论池。

| 数据集 | 总记录数 | 最新交易日记录数 | 最新覆盖率 | 已存历史交易日逐日检查数 |
| --- | --- | --- | --- | --- |
| stock_basic | 5,911 | — | — | — |
| security_master | 5,911 | — | — | — |
| trade_cal | 26,128 | — | — | — |
| daily | 27,900 | 5,561 | 100% | 9 |
| adj_factor | 27,929 | 5,572 | 100% | 15 |
| daily_basic | 83,410 | 5,561 | 100% | 19 |

所有已存 Clean 核心行情日期均通过最低 95% 覆盖率验证。自然分区同时通过固定 Arrow Schema、ZSTD、Hive 路径、非空、关键字段及主键唯一性检查。`data/current/acceptance.json` 记录发布时验收和 MAINTENANCE 模式；`metadata/acceptance.json` 记录随后独立 CLI 验收。

正式 Artifact `task01-validated-data` 包含 Raw、Clean、完整 metadata（Manifest、能力快照、字段 Schema、Gap、有效配置）和核心数据集文档。恢复 Artifact `task01-checkpoint` 包含当前和上一正式版本及未完成工作，不应代替正式数据验收报告。

本次已自动发现 11 个宽基指数，写入有效配置 `metadata/effective_config.json` 的 `index_universe`；主行业体系尚未确定。当前 Gap 包含数据源早期历史覆盖不足、退市记录字段缺失、扩展接口权限不足等，仍需后续审计和补抓。

## 历史范围与未完成验收

以账号实际返回数据和当前基础股票池验证，历史边界探测结果为 daily / daily_basic **1992-05-08**，adj_factor **1990-12-19**。这只是本次探测的覆盖率合格起点，不是 Tushare 的永久接口起点或全量下载完成证明。边界前不完整数据保留 Raw 和 Gap，不发布到 Clean；边界后每个实际下载日期仍须独立满足覆盖率，失败会阻止发布。每月重新探测，详见[提供者边界](provider-boundaries.md)。

仍待完成：

- 全部可获得历史的分批下载、缺口补抓及历史修订持续实测。
- 连续真实交易日 DAILY_UPDATE 和无人干预定时运行；当前两个成功案例均为非交易日 MAINTENANCE。
- 财务/行业/指数与其他扩展数据的完整历史覆盖；行业主体系选择。
- 真正远程运行失败后的旧正式版本保持演练；本地恢复和阻断测试已通过。
- Phase 1 稳定后实施 Google Drive staging、远程验证和正式切换；当前未接入 Drive。

因此当前结论是 **Phase 1 的真实数据链和两次成功发布已验证，Task 01 最终验收尚未完成**。

## 初次失败及修复记录

[首次运行 36984398679](https://github.com/magangwwd/ashare-research-infra/actions/runs/36984398679) 因未注入 `TUSHARE_TOKEN` 正确失败，没有发布正式数据。Token 生效后，真实运行又暴露退市记录 market 缺失、SZSE 初期日历空、修补请求缺少显式 stock_basic 字段、早期行情覆盖不足等问题；均已修复并增加测试。核心失败阻断规则和 95% 阈值保持不变。
