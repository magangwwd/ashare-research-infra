# Task 01 验收记录

## 2026-10-03：Runner Drive 授权验证通过

用户报告 Google Cloud 应用已转为 In production，并重新授权、替换 GDRIVE_OAUTH_JSON。应用发布状态来自用户确认；没有读取或导出任何 Secret。已独立核对 [run #13 / 37127560205](https://github.com/magangwwd/ashare-research-infra/actions/runs/37127560205)，代码为 `ea1e78bd9237bb460d8f735e95b93cad8cdee00a`。

- 北京时间 2026-10-03 21:50:39—21:51:00，`drive-auth` 用时 21 秒，所有步骤成功。
- Runner 使用 Secret 刷新访问 token，目标目录 `1rkC99Ymgp8mjvAcJNRPJMlOGLdI9_bue` 验证通过，`can_add_children: true`。
- 默认 `task01` 数据 job 为 skipped；本次是授权检查，不能计为核心数据更新或总验收。
- 检查输出 `file_upload_tested: false`、`production_publish_enabled: false`。没有测试实际上传，没有启用 Drive 数据发布。
- 授权准备与真实 Runner 验证已完成；取消 Testing 的 7 天规则不代表 refresh token 永不失效，不将本次验证描述为永久凭据保证。

接续事项仍是 Phase 1 历史和稳定性验收，以及之后的 staging、远程完整性校验、Current Pointer 和失败保护实现；此时无需再次授权。

## 2026-10-03：准备 Runner Drive 授权（尚未授权）

用户明确要求协助完成 GitHub Actions 独立授权，因此先准备凭据工具；不是提前宣告 Phase 2 数据发布完成。

- 已读取用户创建的 Ashare_Quant 文件夹，ID 为 `1rkC99Ymgp8mjvAcJNRPJMlOGLdI9_bue`，检查时为空。
- 提供本机 Desktop OAuth + PKCE 工具，刷新/验证目录后通过 gh CLI stdin 写入 GDRIVE_OAUTH_JSON，不将凭据写入代码、日志、配置或 Artifact。
- 10 项授权/凭据保护测试通过，普通测试合计 47 项通过、2 项需真实数据的演练在本地跳过。
- 同一个 Workflow 增加手动勾选的只读 `drive-auth` 验证。未勾选时按原数据流程运行，不自动上传。
- 当前连接没有设置仓库 Secrets 的权限；需用户在自己的电脑登录 Google 和有仓库管理权限的 GitHub 账号。Google OAuth 授权、Secret 写入和真实 Runner 验证尚未完成，不计为通过。
- `storage.drive.enabled` 为 false；尚无 staging 上传、远程校验或 Production 切换。

## 2026-10-02：近期共同窗口补齐与第五次成功发布

最新运行 [36999912593](https://github.com/magangwwd/ashare-research-infra/actions/runs/36999912593) 对应代码 `7135823fd22db831c02111bddec05af3cd405ca5`，全部步骤 SUCCESS。

- 37 项普通测试通过；2 项真实交易日回放/恢复演练在发布前通过。
- 本次 MAINTENANCE 历史预算/实际请求为 240，已有完整请求跳过 70 个；本轮三个批次合计 720 个请求，仍未遍历完全部历史。
- 下载顺序改为日期/年份/月份从近到远；历史起始配置未改变，已有完整请求继续按文件与回执跳过。
- 最新 Manifest 显示 daily 和 adj_factor 的近期分区范围扩展至 2026-09-11—2026-09-30，daily_basic 至 2026-09-09—2026-09-30。Runner 对全部已存核心交易日执行逐日验收；分区范围本身不代表更早所有日期已补齐。
- 最新日理论池仍为 5,559，三类核心行情覆盖率均为 100%，missing_ts_codes 为空。
- 总记录数：daily 72,906；adj_factor 72,569；daily_basic 84,005。已存历史交易日逐日验收计数分别为 38 / 38 / 40。
- 最新正式 Artifact `11223432800` 有 340 个 Parquet 文件；Runner 验收通过，恢复演练核对源文件不变、拒绝坏覆盖率、模拟指针中断并修补成功。
- 小型验收证据 Artifact `11223916734` 已下载核对，包含正式验收、340 文件 Manifest、历史进度和两个演练报告。最新正式整包约 43.9 MB，尚未在本地重新下载/全文件复验；不能将此前四包的本地复验描述成最新整包独立复验。

当前结论：Phase 1 数据链、两种运行分支、断点恢复和失败保护已有真实证据；历史全量、连续当日定时运行、历史修订与完整扩展覆盖仍需持续推进，Google Drive 未接通。Task 01 最终验收尚未完成。

## 2026-10-02：真实交易日回放、退出成员与恢复验证

第四次完整运行 [36998336977](https://github.com/magangwwd/ashare-research-infra/actions/runs/36998336977) 使用代码提交 `6b5deb37d50221cae9c1d3e04cc616a9418b2879`，全部步骤 SUCCESS。

- 36 项普通测试通过，2 项真实演练在发布前单独执行并通过。
- MAINTENANCE 再推进 240 个历史请求，跳过已有完整请求 330 个；与第三次运行合计推进 480 个请求（包括合法空响应），不等于 480 个交易日或分区。
- 正式 Artifact `11223062308` 已下载，332 个 Parquet 文件大小/SHA256 逐一匹配 Manifest，独立总验收通过。
- 最新交易日核心覆盖率仍为 100%；已存历史交易日逐日检查为 daily 30、adj_factor 30、daily_basic 40。
- 行业成员已保存 21 只证券的 32 条记录：当前成员 Y=21、已退出成员 N=11，带原始入选/退出日期；不是全证券行业覆盖。
- 回放 2026-09-30，真实请求 Critical 数据、验证 DAILY_UPDATE 分支，并在隔离目录验收/发布成功；证据为 `metadata/trading_day_replay.json`。它不等于 2026-10-02 当天实时交易或 cron 触发证据。
- 真实数据恢复演练再次通过，332 个源文件不变，坏覆盖率发布被阻止，指针中断保持旧正式版本，补齐后恢复成功。
- 新增小型 `task01-validation-report` Artifact（ID `11223096998`），便于独立读取验收、Manifest、历史、回放和恢复证据。

最近三类核心行情共同日期窗口仍只有 5 个交易日。后续历史改为从近到远补齐，使近期共同窗口先形成；已下载较早数据及历史最早边界均保留。行业历史仍按上市日期先后推进，不展开每日行业/成员状态。

## 2026-10-02：240 请求维护批次与真实数据恢复演练

第三次完整运行 [36996934881](https://github.com/magangwwd/ashare-research-infra/actions/runs/36996934881) 使用代码提交 `c146bafe863251419bd80e7611e927af626740ae`，全部步骤 SUCCESS。

- 36 项单元及离线集成测试通过；真实数据演练单独执行并通过。
- 本次 MAINTENANCE 历史预算/实际请求均为 240，跳过已有完整请求 95 个；全历史仍未遍历完毕。
- 正式 Artifact `11222277481` 下载后逐一核对 275 个 Parquet 文件大小和 SHA256，本地总验收通过。
- 最新交易日 2026-09-30、理论池 5,559 只，三类核心行情覆盖率均为 100%，缺失列表为空。
- 已存历史交易日逐日检查：daily 20、adj_factor 19、daily_basic 29；总记录数分别为 28,207 / 27,961 / 83,689。
- Runner 使用真实正式数据在隔离目录模拟覆盖不足和指针切换中断，两者都保持旧正式文件不变；恢复暂存并补齐后重新验收/切换成功，证据为 `metadata/recovery_drill.json`。

本批行业成员显式执行 Y/N 请求，但查询对象多为新上市证券，N 返回空。后续历史请求改为按上市日期从早到晚推进，近期刷新保持独立轮转；不能把一次空 N 响应描述为已经获得退出历史。

本次修复了行业历史仅包含历史起始年已上市证券、默认只请求最新成员，以及分红接口重复传入无效年份范围等问题。交易日历史预算仍为 40，非交易日提高到 240；核心当天更新顺序和 95% 阈值未改变。

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

cursor 是数据集轮转游标，用于安排下一次从哪个数据集开始；具体请求是否完成以实际文件及回执为准。它不是总历史请求的位置、累计下载天数或完成百分比。第二次运行恢复已有状态、跳过完整请求后继续下载；不是重抓全部历史。两份下载产物均逐文件核对 Manifest 记录的大小、SHA256，并在本地对解压后的数据再次执行 `validate_task01`，结果通过。

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
- 连续真实交易日 DAILY_UPDATE 和无人干预定时运行；当前常规成功案例均为非交易日 MAINTENANCE，最近交易日的隔离回放已通过。
- 财务/行业/指数与其他扩展数据的完整历史覆盖；行业主体系选择。
- Runner 上真实 Parquet 的质量失败/指针中断/修补演练已通过；实际网络故障和远程上传故障仍待对应阶段验证。
- Phase 1 稳定后实施 Google Drive staging、远程验证和正式切换；当前未接入 Drive。

因此当前结论是 **Phase 1 的真实数据链和两次成功发布已验证，Task 01 最终验收尚未完成**。

## 初次失败及修复记录

[首次运行 36984398679](https://github.com/magangwwd/ashare-research-infra/actions/runs/36984398679) 因未注入 `TUSHARE_TOKEN` 正确失败，没有发布正式数据。Token 生效后，真实运行又暴露退市记录 market 缺失、SZSE 初期日历空、修补请求缺少显式 stock_basic 字段、早期行情覆盖不足等问题；均已修复并增加测试。核心失败阻断规则和 95% 阈值保持不变。
