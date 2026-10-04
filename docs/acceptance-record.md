# Task 01 验收记录

## 2026-10-04（北京时间）：Drive→Runner 灾难恢复链代码已接入，待真实 Runner 强制恢复验收

已新增 `data_pipeline/drive_restore.py` 并接入常规 `task01` Workflow。恢复策略为 GitHub checkpoint 优先；当 checkpoint 不存在或下载/解包失败时，自动回退到 Google Drive 当前正式版本。另新增手动参数 `force_drive_restore`，可在真实 Runner 中故意忽略 GitHub checkpoint，用于灾难恢复演练。

恢复过程不会直接覆盖本地生产：先读取唯一 `current.json`，验证其 accepted/validated 状态、版本目录、Manifest ID、Manifest SHA256、文件数和总字节数；再校验正式版本目录实际清单与 Manifest 一致；之后逐文件下载到隔离临时目录，逐文件验证大小与 SHA256。Raw/Clean、acceptance 和必要 metadata 恢复完成后，再用现有 Critical 验收器重新验证恢复快照。只有所有校验通过，才原子建立本地 `data/current` 指针。任何校验失败都不得切换本地 current；如果本地已有 `data/current`，Drive fallback 会拒绝覆盖。

新增单元测试覆盖：成功恢复并切换、下载内容损坏阻断、Manifest checksum 错误阻断、路径穿越/白名单外路径拒绝、已有本地 current 时拒绝覆盖。Workflow 的 checkpoint 解包也改为先在临时目录恢复，Artifact 损坏时清理局部状态后再进入 Drive fallback，避免半恢复状态污染。

当前状态：**恢复链代码完成，但尚未取得一次真实 GitHub Runner 的 `force_drive_restore=true` 成功证据。** 在该真实演练通过前，不能把“Drive 灾难恢复”标为最终验收完成。


## 2026-10-04（北京时间）：第二次真实 Drive 发布与版本切换验收通过

为验证首次发布之后的真实替换路径，重新执行第 15 次数据任务的 checkpoint 续传（run `37133945515` attempt 2）。恢复最新状态后，Tushare Smoke、历史补齐、Production acceptance、真实交易日回放/恢复演练、正式 Artifact 和 checkpoint 全部 SUCCESS；新 checkpoint Artifact 为 `11288019885`。本轮正式验收 `as_of=2026-10-04`，Critical 总验收仍通过。

随后重新执行 Drive 发布 run `37160747646` attempt 2。发布期间直接从 Google Drive 检查到新版本先位于 staging，而根目录 `current.json` 仍保持首次正式版本不变；上传完整后完成远程路径、大小和逐文件 SHA256 校验，才移动到 versions 并最后切换指针。发布 job 和发布证据 Artifact `11289650328` 全部 SUCCESS。

- 新正式版本：`2026-10-04T000708.613594_0000-6a756985`，版本目录 ID `1O6t2EE1V1rmRCQYgzdbpiD-TosgHfsN0`。
- 新 `current.json`：`validated=true`、`snapshot_as_of=2026-10-04`、`file_count=482`、`total_bytes=109,617,924`。
- 新 Manifest ID `1iwWFc93o3TE_klP6B9KT6VGCXM7cgegF`；下载后的 SHA256 为 `ac75ae922160723e5436785d823886298c4f018e584afebaadfdd94249f3948c`，与 `current.json.manifest_sha256` 完全一致；Manifest 的版本、validated、文件数和总字节数也与指针一致。
- `current.json.previous_version` 正确指向首次版本 `2026-10-03T230917.199630_0000-e9d5288a`。
- Drive `versions/` 独立列出两个正式版本；首次版本目录 `1xuu9_7Kuc325Wz4IeMImgMnswd2xgxSP` 及旧 `production_manifest.json` 仍存在且可读取。第二次发布结束后 `staging/` 为空。
- 因此“新指针切换、新版本可读、旧版本仍可恢复”这一里程碑已有真实 Drive 证据，不再只依赖离线故障测试。
- 当前历史仍未完成：`critical_history_complete=false`、`full_requested_core_range_complete=false`、`extensions_history_complete=null`。第二次发布成功不代表 Task 01 总验收完成。

在上述真实替换验证后，commit `a208de341b587bab03b194389c752e423763d62e` 已把 Drive 发布接入常规 `task01` job：本地验收和验收 Artifact 成功后自动执行 `data_pipeline.drive_publish`；Drive 发布失败会使数据 job 失败，但后续 `always()` recovery checkpoint 仍会保存。手动 `publish_drive_snapshot` 入口继续保留作重发/恢复。该自动衔接代码已提交，仍需下一次正常数据任务提供真实运行证据后，才能把“每日自动上传”标为运行验收完成。


## 2026-10-04（北京时间）：首次真实 Drive 快照发布成功

用户要求上传当前数据后，新增手动 `publish_drive_snapshot` 入口；默认定时任务继续原数据流程。[run #18 / 37160747646](https://github.com/magangwwd/ashare-research-infra/actions/runs/37160747646) 对应代码 `5614132964eadbbc18dc31a9ac1f2ee25ff1b7ec`，北京时间 07:07:44—07:25:53，全部发布步骤 success，普通数据任务和授权检查任务 skipped。

- 本地 77 项普通测试通过、2 项需真实数据的测试跳过；包含上传中断、漏文件、同尺寸内容损坏、远程读取失败、指针更新失败时旧数据/Manifest/指针不变，以及丢失指针写入响应的回读确认。Runner 安装/测试步骤通过。HTTP 错误提示随后调整为不假定指针状态，16 项发布测试再次通过。
- 恢复第 15 次数据任务检查点，仅使用 `data/current` 已验收版本，未发起 Tushare 新下载；按原快照 `as_of=2026-10-03` 重新执行 Critical 总验收。没有把失败暂存版本、OAuth JSON 或 Token 上传。
- 上传 480 个数据/元信息文件，共 106,368,751 字节，包含 398 个 ZSTD Parquet；Raw Parquet 66,252,639 字节，Clean Parquet 38,829,878 字节。发布清单和根目录指针另计。
- 全部远程路径、ID、大小和逐文件下载 SHA256 验证通过；生产 Manifest 上传/回读通过后将版本移入 versions，最后写入/回读 `current.json`。根目录指针指向 [正式快照](https://drive.google.com/drive/folders/1xuu9_7Kuc325Wz4IeMImgMnswd2xgxSP)，staging 独立检查为空。
- 独立通过 Drive 连接读取原快照和重新验收报告、历史进度、Current Pointer 及 production_manifest.json；Manifest SHA256 `9e0b221c96dc479ebc9949eaaf1a5ffec8feb72a565d3332e773ab39b0251017` 与指针相符，480 路径唯一、计数和总字节数一致。
- 独立抽样下载 `data/clean/daily/year=2026/month=09/part-000.parquet`：4,459,708 字节、116,588 行，SHA256 `29a7733cd59d396b49f79cb737ef5ba6f0f9cf15072eb83aaa6cf30ce08cdf52` 与 Manifest 相符；Date/Schema、ZSTD、非空和主键检查通过。没有将此抽样表述为本地全库再次验收。
- GitHub 保存 `task01-drive-publication-report`，Actions 的 Drive publication notice 包含相同安全发布结果。上传前 Google 账号总配额 429,496,729,600 字节（400 GiB），可用 422,966,836,744 字节；配额与其他 Google 服务共享，数值是上传前快照。

源数据来自定时 [run #15 / 37133945515](https://github.com/magangwwd/ashare-research-infra/actions/runs/37133945515)，北京时间 2026-10-03 23:38—23:57，代码 `fe6126be74ad54c7de4d5924f472e67ecd31176c`，SUCCESS；实际启动比 20:00 cron 晚约 3 小时 38 分。该批次 MAINTENANCE 完成 240 个历史请求，跳过 674 个完整请求。

最新交易日仍为 2026-09-30，三类核心理论池 5,559、覆盖率均 100%；daily / adj_factor / daily_basic 行数分别为 222,570 / 222,690 / 222,570，均有 65 个已存交易日通过逐日检查。实际目标日尚缺 8,320 / 8,671 / 8,320；daily/basic 边界前 351 个交易日另列。正式指针中 `critical_history_complete=false`、`full_requested_core_range_complete=false`、`extensions_history_complete=null`。

本次验证了首次真实上传与正式切换，没有在真实 Drive 中故意破坏用户 Production。失败保护来自离线故障测试；下一版本替换旧指针尚待实际更新验证。每日自动上传、远程下载恢复、远程增量上传/断点续传、自动版本保留和 GitHub Artifact 清理尚未实现；现有 Runner/Artifact 恢复机制保留。Task 01 全量历史和最终验收仍未完成。

## 2026-10-03：实际历史进度报告与第 14 次完整任务

代码 `fe6126be74ad54c7de4d5924f472e67ecd31176c` 的 [run #14 / 37128661702](https://github.com/magangwwd/ashare-research-infra/actions/runs/37128661702) 于北京时间 22:09—22:27 成功，数据 job 用时 18 分 7 秒，Drive 授权 job 跳过。没有上传 Drive 文件。

- 本地 53 项普通测试通过、2 项 opt-in 真实演练跳过；Runner 的普通测试步骤成功，发布前 2 项真实演练报告均为 passed。
- 恢复既有检查点，MAINTENANCE 下载 240 个历史请求，跳过完整请求 424 个；all_enabled_requests_examined 为 false。
- 最近交易日 2026-09-30 的理论池 5,559，三类核心行情覆盖率均为 100%，missing_ts_codes 为空。
- 三类行情总行数为 daily 161,654、adj_factor 161,576、daily_basic 156,113；已存历史交易日检查数分别为 54、54、53。
- 实际文件扫描生成 history_progress.json / history_progress.md。日历 SSE 自 1990-12-19、SZSE 自 1991-07-03 至检查日没有内部缺失日期；文件扫描无错误。

| 核心数据集 | 观察目标起点 | 目标交易日 | 已存目标日 | 尚缺交易日 | 边界前交易日 |
| --- | --- | ---: | ---: | ---: | ---: |
| daily | 1992-05-08 | 8,385 | 54 | 8,331 | 351 |
| adj_factor | 1990-12-19 | 8,736 | 54 | 8,682 | 0 |
| daily_basic | 1992-05-08 | 8,385 | 53 | 8,332 | 351 |

起止日期、回执或游标不能证明区间完整。报告从实际 Parquet 日期识别缺口，core_observed_range_complete 和 full_requested_core_range_complete 均为 false；扩展全量为 null（尚未验证）。该报告保持分阶段下载模式，不因尚未下载的远期历史阻止最新完整版本发布。

Required 当前不可用：stock_st、stk_premarket、sw_daily、ci_daily、ci_index_member；Optional 当前不可用：stk_surv。原因及每个数据集 Gap 已随报告保存；已有财务/行业记录不能表述成全证券全报告期完整覆盖。

正式 Artifact `11276830363` 约 72.9 MB，380 个 Parquet 文件；Runner 验收及真实恢复演练通过。小型证据 Artifact `11276650786` 已下载核对，包括验收、进度、Gap、Manifest、能力和演练。没有下载最新正式整包做独立本地全文件复验。检查点 Artifact `11276905178` 已保存。

仍待：完整可用历史分批补齐，扩展数据覆盖、连续当日交易/定时运行和历史修订的持续证据；Drive staging、远程完整性验证及正式切换。Task 01 最终验收尚未完成。

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
