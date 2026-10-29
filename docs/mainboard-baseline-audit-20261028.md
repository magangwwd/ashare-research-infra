# A股主板双链路首轮实查记录（2026-10-28）

状态：**BLOCKED / 禁止切换与删除**。这是 Google Drive 连接器对指定文件夹的元数据只读检查，不是 Actions 日志或生产端 OAuth/rclone 的完整验证。

## 仓库证据
- main SHA: `b2d6191e7b86cd4c87443c6582d2a083f07bea7b`。
- 整合 PR #116: draft/open，原计划与使用指南、Skill 在 `plan/unify-mainboard-data-20261028`。
- Gold 生产计划 23:50 北京时间运行 `gold-refresh` 然后 `current-state`；Task01 夜间 21:17/23:30/次日03:23。
- GitHub 连接器的通用 fetch 拒绝直接访问 workflow runs REST 列表（400 URL not allowed）；不能据此判定 Actions 失败。该轮新增代码提交后的 PR CI run `40676481865` 曾显示 `in_progress`，需复查最终结论。

## Google Drive 实际元数据
1. `Ashare_Quant/` 根目录 folder ID `1rkC99Ymgp8mjvAcJNRPJMlOGLdI9_bue`：根目录列出 `versions/`、`staging/` 等文件夹，但**未发现 `current.json`**。精确文件名全盘查询也未返回该文件。
2. `Ashare_Quant/versions/`（`1zZgsIjjzKe236ddYQ1DBpgddEhdcjyib`）：可见最新命名版本 `2026-10-24T211915.119571_0000-7c30e07d`，该版本根目录列出 `data/`、`metadata/`、`docs/`，**未发现 `production_manifest.json`**。目录存在不等于已正式发布。
3. `Ashare_Quant/staging/`（`1oXqyvWAJytdi3EMJRGucUX8U7Lsx4_c1`）：可见 2026-10-28 01:56 UTC 新暂存版本，**绝不能作为正式数据**。有多日未清理的暂存目录，尚未确认原因，不应贸然删除。
4. Gold 预期目录 `down_data/data/gold/main_board/`（`1vCz7H5aeePjFapg7xLPu44x-PUSQOX_8`）：根目录列出 `daily_prices/`、`daily_metrics/`、`adjustment_factors/` 等文件夹，**未发现 `manifest.json`、`instruments.parquet`、`daily_snapshot.parquet`**。精确文件名全盘查询未返回这三者。

**重要限定**：以上是当前连接器可见目录的结果。仍须通过两条生产工作流各自的 OAuth/rclone 根目录和 GitHub run 日志交叉确认：可能是生产使用不同根目录、发布未完成、文件被移动或连接器可见性差异，不能直接断言数据已丢失。

## 本轮代码
- `tools/mainboard_inventory_gate.py`：读取两侧**完整根目录列表 JSON**，只检查必须的生产标志是否存在且类型唯一，输出 JSON 诊断报告。缺失、重复、类型错误时退出码 2；不连接云盘、不上传、不删除、不切换生产。
- `tests/test_mainboard_inventory_gate.py`：覆盖齐全但不可切换、缺 Task01 指针、缺 Gold manifest、重复指针、暂存冒充正式、文件类型错误、无效 JSON。
- 运行方式（需要先保存完整、未截断的两个根目录 JSON）：`python -m tools.mainboard_inventory_gate --task01-listing task01-root.json --gold-listing gold-root.json --output audit.json`；`python -m pytest -q tests/test_mainboard_inventory_gate.py`。
- 此门禁只是**元数据第一关**，即使 PASS 仍需校验 Task01 指针与 SHA256、Gold 数据、日期和策略回放；脚本始终返回 `production_switch_allowed=false` 与 `deletion_allowed=false`。

## 下一轮优先级
1. 读取 CI run `40676481865` 终态、失败 job 日志并修复代码问题。
2. 从 Task01/Gold 各自实际 Actions 日志确认真实远端根 ID、最近7次 run 结果；重点定位缺 `current.json` 和 Gold manifest 的原因。
3. 在不触发全量 Tushare 下载的情况下，执行只读 OAuth/rclone 目录与指针检查，产出有 run ID 的审计报告；未解决前不开发生产切换、不删除旧数据。


## 2026-10-28 20:02 北京时间第二轮进展

- GitHub Pipeline CI `40676481865`、`40676538851` 的 job 均为 `completed/success`。后一轮日志实际显示 **125 passed, 2 skipped**，属于离线单测，不是生产云盘验收。
- 通过 Google Drive API `'<folder_id>' in parents and trashed = false` 精确复查：Task01 根目录可见7项，含 `versions/` 和 `staging/`，仍**未见 `current.json`**；Gold 主板根目录可见6个子目录，仍**未见 `manifest.json`、`instruments.parquet`、`daily_snapshot.parquet`**。两次检查来自同一连接器视角，不能替代生产 OAuth/rclone 实查。
- 新增 `tools/mainboard_remote_probe.py`：在 GitHub Runner 使用现有 `GDRIVE_OAUTH_JSON` 和 `GDRIVE_RCLONE_CONFIG_B64` 分别只读列举正式根目录；先校验 Gold 项目根 marker，再检查两侧正式 marker，返回安全状态码，错误不打印 OAuth/rclone 详细信息；始终拒绝生产切换和删除。
- 新增 `tests/test_mainboard_remote_probe.py`（7项离线测试）和手动、**无 schedule** 的 `.github/workflows/mainboard-dual-drive-probe.yml`；仅将去敏诊断 JSON 作为 Actions artifact 保存14天，不触发 Tushare、无云盘写入。
- 新代码 CI run `40684693795` 已启动，记录时 `in_progress`。**新工作流还在 PR 分支，未在 main 上手动执行生产凭据检查**。GitHub 连接器不提供直接 workflow_dispatch 操作，不能声称生产检查已完成。

### 下一轮闸门
先复核 `40684693795` 终态并修复任何失败；通过代码审查后合并仅只读诊断工作流，再在 main 用正式 Secrets 手动执行一次，读取 artifact 确认真实根目录和 marker。未取得两端生产端证据前，不切换策略、不清理数据。

## 2026-10-29 00:02 北京时间第三轮推进

- 复核上轮 CI：Pipeline CI `40684693795` 和 `40684776855` 的 job 均为 `completed/success`，**仅代表离线 CI 成功**。
- 在 PR #116 的只读诊断 `tools/mainboard_remote_probe.py` 上新增第二层验收：Task01 读取 `current.json`，校验 `validated=true`、版本目录/manifest 的 Drive ID 与父子关系、`production_manifest.json` 的 SHA256、版本/日期/文件数量一致；Gold 只读读取 `manifest.json`，检查 `as_of_date` 与 `primary_keys_unique`、`ohlc_valid`。不会读取或传出行情分区。
- 新增 `tests/test_mainboard_manifest_contract.py` 的10项离线测试，覆盖SHA损坏、错误目录、指针未验收、版本不一致、Gold 验收失败等。提交：`534a911070d53a4e3916e85e48a7c1164f7956de`、`7c37b91c53802d04ef2185075db5ca2d14b08748`。
- 新 CI `40722398618` 创建后检查时为 `in_progress`；**尚未验收通过，不得声称成功**。
- 生产凭据端的只读诊断仍未运行，Drive 两侧正式 marker 仍缺少实测确认；不得停止原下载、删除数据或切换选股。下一轮优先复核新 CI、审查 PR 后使只读工作流在 main 可执行，并取得真实生产端 artifact。


## 2026-10-29 04:01 北京时间第四轮：正式 marker 已找到，纠正此前缺失判断

**重要纠正：** 本轮 Google Drive 连接器重新列举与精确读取，Task01 `Ashare_Quant/current.json` 和 Gold `down_data/data/gold/main_board/manifest.json`、`instruments.parquet`、`daily_snapshot.parquet` **均实际存在**。此前“未找到”是当时查询结果，不能再作为当前缺失结论。不能据此推断生产端曾丢失文件。

### 实际读取与核验（只读，不使用生产 Secrets）

- Task01 `current.json`：`validated=true`，`snapshot_as_of=2026-10-24`，`source_run_id=40119936249`，正式版本 `2026-10-24T211915.119571_0000-7c30e07d`，指向 **5406** 个文件、**2,331,724,247** 字节；`critical_history_complete=false`、`full_requested_core_range_complete=false`，不能宣称历史齐全。
- Task01 版本目录实际存在、其父目录是 `versions/`；`production_manifest.json` 也实际存在且直接位于所指版本目录。下载后计算 SHA256 **`118e6afb6febd3993656afce9b46452809e5a05bf412db7c87515a891bc3e9e8`**，与指针一致；manifest `validated=true`、版本和 `snapshot_as_of` 与指针一致，文件数=5406，文件字节和=2,331,724,247，路径及文件 ID 在清单中均唯一。**尚未逐一下载5406个分区核验内容**。
- Gold `manifest.json`：`as_of_date=2026-10-27`，`primary_keys_unique=true`、`ohlc_valid=true`；`instruments.parquet` **3486行**、`daily_snapshot.parquet` **3189行**（行数来自 manifest）；实际下载这两个 Parquet，SHA256 均与 Gold manifest 声明完全一致。未解析 Parquet 内部行内容，未完成策略回放。
- **日期不一致：Task01 2026-10-24，Gold 2026-10-27，落后3个自然日**；这不等于3个交易日，必须阻止适配器以旧 Task01 替换新 Gold。即使两端 manifest 验收标志为真，也不能代表今天已更新。
- GitHub Actions `Task01` run [40119936249](https://github.com/magangwwd/ashare-research-infra/actions/runs/40119936249) 已确认 `completed/success`，与上述指针来源一致。
- 最新检查到的 Task01 失败 run [40562368344](https://github.com/magangwwd/ashare-research-infra/actions/runs/40562368344) 与 [40598869222](https://github.com/magangwwd/ashare-research-infra/actions/runs/40598869222)：**均在 `Publish accepted snapshot to Drive` 步骤失败，日志均显示 `Drive request failed (410)`**，分别已暂存约1600/6994、2900/7037个文件，旧正式指针未见切换。**410来自哪个请求阶段尚未查明，不可直接断言配额或会话过期**。
- Task01 run [40748947088](https://github.com/magangwwd/ashare-research-infra/actions/runs/40748947088) 在本轮检查时 `in_progress`，执行到 `Daily update or maintenance`；尚未取得最终结果。Gold run [40576385273](https://github.com/magangwwd/ashare-research-infra/actions/runs/40576385273) `completed/success`，与 Gold 2026-10-27 manifest 及文件修改时间相符。
- PR #116 新增日期对齐检查：即使两边分别 `PASS`，只要 `as_of` 不相同便返回 `TASK01_BEHIND_GOLD` / `GOLD_BEHIND_TASK01`，禁止误判可影子替换；新离线测试覆盖本次实际日期差异。日期相同也**不等于**可以切换生产。

### 下一轮明确任务

1. 先复核 Task01 run 40748947088 的终态、是否再次报410；若失败，增加不泄露请求 URL/凭据的安全错误阶段分类，定位失败是上传会话、文件读回还是目录操作。
2. 复核新增日期门禁的 CI 结果，补充主板股票、OHLCV/单位、停复牌、复权及冻结策略差异对比；未完成之前保留双链路。
3. 不重触发全量下载、不删除 staging 或旧 Gold、不合并生产切换。生产端手动只读 probe 仍待在 main 上可执行后实测。


### 410 诊断补丁已独立提交

新建独立草稿 [PR #120](https://github.com/magangwwd/ashare-research-infra/pull/120)：`data_pipeline/drive_publish.py` 的失败日志增加固定操作类别（例如 `resumable_upload`、`read_file_media`、`list_files`），不包含 URL、session ID 或凭据；5项离线测试复现410并检查去敏。**不改变重试、上传、指针或调度行为，也不宣称已修复410。** 新 CI run [40760207733](https://github.com/magangwwd/ashare-research-infra/actions/runs/40760207733) 创建时为 queued；必须检查终态后再考虑合并。PR #116 的日期门禁 CI run [40759865151](https://github.com/magangwwd/ashare-research-infra/actions/runs/40759865151) 已 `completed/success`。


### 410 安全诊断补丁已合并主分支

[PR #120](https://github.com/magangwwd/ashare-research-infra/pull/120) 在 CI run [40760207733](https://github.com/magangwwd/ashare-research-infra/actions/runs/40760207733) **completed/success**（日志 `123 passed, 2 skipped`）后已 squash merge 到 main，合并 SHA `e121a48231cc8d35ebcd3d64e88e6f217bd869b6`。**只增强410故障阶段的安全分类日志，不改变上传行为；当前在运行的旧 SHA Task01 不会自动获得该改动，须观察后续基于新 main 的正常计划运行。** 410 的真正根因尚未定位或修复，禁止把本次合并视为生产恢复。


## 2026-10-29 08:00 北京时间第五轮：发布耗时与410双重阻塞（已实读 Actions）

- main SHA `e121a48231cc8d35ebcd3d64e88e6f217bd869b6`，PR #116 仍 draft/open。此前 PR #120 的操作类型诊断已在 main；这不等于修复。
- Task01 run [40748947088](https://github.com/magangwwd/ashare-research-infra/actions/runs/40748947088) **completed/cancelled**，其 task01 job 在 `Publish accepted snapshot to Drive` 步骤被取消；该次 job 总时长约240分钟（workflow `timeout-minutes: 240`）。日志：已通过下载、验收和恢复演练，随后 `staging created files=7046 bytes=2723914251`，报告 `staged files=100/7046`；正式指针成功切换没有证据。**不要把被取消的上传当作成功**。
- 新 run [40786202732](https://github.com/magangwwd/ashare-research-infra/actions/runs/40786202732) 于 2026-10-28 23:16:38 UTC 创建，检查时仍 `pending`，不能称作正在下载或已经完成。
- 两次更早失败的 [40562368344](https://github.com/magangwwd/ashare-research-infra/actions/runs/40562368344) 与 [40598869222](https://github.com/magangwwd/ashare-research-infra/actions/runs/40598869222) 分别在 1600/6994、2900/7037 文件处报 HTTP 410。因当时版本没有新操作标签，**尚未确认410是否来自 resumable PUT**。
- 本轮建立独立**草稿** [PR #121](https://github.com/magangwwd/ashare-research-infra/pull/121)，对 `resumable_upload` 的 410 仅在已确认文件404后使用新ID限重试一次，或在元数据+SHA吻合时复用；其他410、状态不明或再次失效直接阻断。新增5项离线测试。**未合并，未生产验证**；CI run [40791360486](https://github.com/magangwwd/ashare-research-infra/actions/runs/40791360486) 检查时 `in_progress`。
- **独立阻塞**：现有 Task01 将约2小时数据处理与约7000文件逐项上传/远程校验串在一个240分钟 job 内；仅提高超时不保证解决410或全量发布。后续应优先研究独立发布 job / 可恢复 staging 的设计及容量/配额/时限预算，先在草稿 PR 离线验证，避免重复全量下载。
- 仍保持 Task01 和 Gold 两条生产链路、旧指针及旧数据不动；没有5交易日影子比对、冻结策略一致性和回滚验收，不得切换/删除。


### 同轮 Google Drive 正式入口复核（2026-10-29 08:03 北京时间）

- 直接列举 Task01 根文件夹 `1rkC99Ymgp8mjvAcJNRPJMlOGLdI9_bue`：`current.json` ID `1Z51dXKxxt2J_4jtcW7NcSqsNt3V3qMdB`，修改时间 `2026-10-25T00:16:56Z`。**实际读取 JSON**：`validated=true`、`snapshot_as_of=2026-10-24`、`source_run_id=40119936249`、`file_count=5406`，历史完整性仍 false。
- 直接列举 Gold 根文件夹 `1vCz7H5aeePjFapg7xLPu44x-PUSQOX_8`：`manifest.json` ID `1wO2UFU4-kQsujMLo9G8rQLoUIeSRaCqn`，修改时间 `2026-10-28T20:45:24Z`；**实际读取 JSON**：`as_of_date=2026-10-28`、`primary_keys_unique=true`、`ohlc_valid=true`。核心 `instruments.parquet` 与 `daily_snapshot.parquet` 均在正式目录中。
- **Gold 已推进至10月28日，Task01 仍停留10月24日，落后4个自然日**。本次只重新读取两端正式 JSON，不宣称重新逐文件核验5406个文件或 Gold 全量分区；前轮 Gold 10月27日的日期差异记录已被新事实替代。
- 因为正式日期不一致，继续禁止统一读取切换及清理旧 Gold。


### Task01 正式 Manifest 本轮独立复验

- 通过当前 `current.json.manifest_file_id=1RzoGA4-RqdqQQSzmaE6M3wkiSh9-GVG1` 实际下载 `production_manifest.json`（1,371,895字节），本地重算 SHA256=`118e6afb6febd3993656afce9b46452809e5a05bf412db7c87515a891bc3e9e8`，与 pointer 中声明完全相同。
- Manifest `validated=true`、`snapshot_as_of=2026-10-24`、`version=2026-10-24T211915.119571_0000-7c30e07d`、`file_count=5406`、`total_bytes=2331724247`，与 pointer 一致；文件条目声明字节数合计也为 2,331,724,247。
- 此次仅验证 Manifest 本身和元数据一致性，**没有下载核验5406个底层分区**，也不证明最新数据已发布。


## 2026-10-29 12:00 北京时间第六轮：分离240分钟超时与410故障

- main SHA 仍为 `e121a48231cc8d35ebcd3d64e88e6f217bd869b6`；整合 PR #116 为 draft/open。PR #121（410有界恢复）仍为 draft/open，CI `40791360486` 与 `40791381245` 均 completed/success，**不代表真实410已修复**。
- Task01 run [40748947088](https://github.com/magangwwd/ashare-research-infra/actions/runs/40748947088) 终态 cancelled，重新从完整日志确认：发布于 2026-10-28 21:07 UTC 启动，21:10 创建 staging，22:51 已到 **3600/7046** 文件，22:51:54 被取消；**更正前一轮“仅100文件”的过时进度**。约101分钟完成3500文件上传；结合前置数据处理及全量远端SHA核验，现有240分钟合并job预算明显不足。
- Task01 新 run [40786202732](https://github.com/magangwwd/ashare-research-infra/actions/runs/40786202732) 截至本轮仍 in_progress；其 `task01` job 已通过更新、验收、恢复演练并产出 `task01-validation-report` artifact，正在 `Publish accepted snapshot to Drive`。未见成功发布凭证；进行中的 job 日志读取返回404，不能臆断上传进度或410操作类型。
- Google Drive 通过正式文件ID重新下载：Task01 `current.json` ID `1Z51dXKxxt2J_4jtcW7NcSqsNt3V3qMdB`，as_of=2026-10-24，manifest SHA256仍为 `118e6afb6febd3993656afce9b46452809e5a05bf412db7c87515a891bc3e9e8`；Gold `manifest.json` ID `1wO2UFU4-kQsujMLo9G8rQLoUIeSRaCqn`，as_of=2026-10-28，质量标志均true。**两侧正式日期仍差4个自然日**，且 Task01 历史完整性仍false。
- 为解决“下载/验收+全量上传共用240分钟job”的独立瓶颈，已新建 **draft [PR #123](https://github.com/magangwwd/ashare-research-infra/pull/123)**：仅当 main 分支 Task01 运行被取消、`task01` job 耗时235–265分钟且同一run同时具有验收和checkpoint artifact时，才允许独立350分钟发布；与原Task01共用并发锁，不重新下载Tushare；拒绝旧于当前正式日期的checkpoint以避免倒退。该草稿未合并/未生产执行，350分钟是否足够仍需实测。PR #123 CI runs [40815247495](https://github.com/magangwwd/ashare-research-infra/actions/runs/40815247495)、[40815261324](https://github.com/magangwwd/ashare-research-infra/actions/runs/40815261324) 启动后尚待最终核验。
- 下一轮先看 Task01 #40786202732 终态与410操作标签，再审核 #123 的安全边界及CI；若合并前仍无可靠证据，不启用自动发布恢复。保持双下载、旧指针、冻结V1与旧数据不动。


## 2026-10-29 16:00 北京时间第七轮：第二次240分钟取消与恢复工件安全加固

- main SHA `e121a48231cc8d35ebcd3d64e88e6f217bd869b6`；整合 PR #116 未合并，Task01 正式发布仍是优先阻塞。
- Task01 [run 40786202732](https://github.com/magangwwd/ashare-research-infra/actions/runs/40786202732) 已从 in_progress 变为 **completed/cancelled**，`task01` 的更新、验收、恢复演练全部 success，`Publish accepted snapshot to Drive` 为 cancelled。**不是成功发布**。
- 同一 run 的 `task01-validation-report` artifact ID `12871950683`（459789字节）与 `task01-checkpoint` artifact ID `12878879550`（3424299634字节）均存在，checkpoint ZIP SHA256=`f2526309edea55fe5bcffb9e77ac032f870b63ae6cff5f9addc1d3b2a5ff99bd`，到期时间 `2026-11-01T06:59:59Z`；仅有工件不代表数据已经发布。
- 重新读取 Drive 正式 JSON：Task01 `current.json` ID `1Z51dXKxxt2J_4jtcW7NcSqsNt3V3qMdB`，`snapshot_as_of=2026-10-24`；Gold `manifest.json` ID `1wO2UFU4-kQsujMLo9G8rQLoUIeSRaCqn`，`as_of_date=2026-10-28`，质量标志仍 true。Task01 仍落后，不得切换。
- PR #123 前两轮离线 CI `40815247495`、`40815261324` 和后续 `40815389698` 均 completed/success；本轮进一步更新 PR #123：恢复工件要求 GitHub 提供合法 SHA256 digest 且不超过5GB，下载后对 ZIP 做 SHA256 校验，并在解包 tar 前删除大 ZIP 释放磁盘空间。提交 `de0522719c92f9af7c0bb858735a49e8b54a6fe5`、`4bb7c285af3f91beb2f90788974a2b741a8d93ad`。**新增补丁的 CI 尚待重新确认**，PR 仍为草稿，未在生产启用。
- 下一轮优先复核新 CI、检查取消 job 的240分钟耗时及完整日志；评估 workflow_run 自动重试的权限、磁盘和计费风险，确认安全后才允许合并。不要为了诊断重复启动大规模全量下载。


### 2026-10-29 20:00 CST：第八轮独立核验（只读）

- main 最新 SHA：`19d92f10476bb2c40a2da84a64ffd74194b77685`。
- Task01 源运行 `40786202732` 的 `task01` job `cancelled`；完整日志最后可确认 **4400/7069** staged files（2026-10-29T06:55:44Z）。本次仍未进入逐文件远端 SHA256 读回与 current pointer 切换。
- 同源 run 工件：`task01-validation-report` #`12871950683`；`task01-checkpoint` #`12878879550`，3,424,299,634 bytes，SHA256 `f2526309edea55fe5bcffb9e77ac032f870b63ae6cff5f9addc1d3b2a5ff99bd`，过期 `2026-11-01T06:59:59Z`。此SHA是 GitHub 工件ZIP摘要，**不是云盘已发布快照摘要**。
- Google Drive 实读 `Ashare_Quant/current.json`（file ID `1Z51dXKxxt2J_4jtcW7NcSqsNt3V3qMdB`）：`snapshot_as_of=2026-10-24`、`validated=true`、`critical_history_complete=false`；其正式 `production_manifest.json`（file ID `1RzoGA4-RqdqQQSzmaE6M3wkiSh9-GVG1`）实际 SHA256=`118e6afb6febd3993656afce9b46452809e5a05bf412db7c87515a891bc3e9e8`，与 pointer 相符，5406文件、2,331,724,247字节。
- Gold 正式 `main_board/manifest.json`（file ID `1wO2UFU4-kQsujMLo9G8rQLoUIeSRaCqn`）：`as_of_date=2026-10-28`，主键与OHLC质量标志true；记录 instruments 3486行、daily_snapshot 3190行。Task01 仍落后，禁止替换。
- PR #123 上轮 CI #`40838902886` success；本轮补丁 `fcaf77966f99f3025809c4ebda8ca5e10b19c2ea`、`f56b2484f4a470da16a8c1b16057c733bc9652f5` 增加仅在 **已验收且发布步骤取消** 时恢复及tar解压路径/类型白名单；后续测试修正提交 `a3e2eec8a378ee1ff6e33a115812c02a4ffc9aa2`，最新 CI `40870564343`、`40870554595` 均 success；前者157 passed、2 skipped。**仅离线测试通过，尚未合并、未执行真实发布。**
- 结构性风险：恢复流程 `workflow_run` 不追溯旧run，且当前实现每次重新上传全部文件并再次逐文件下载计算SHA256，350分钟可能仍不足。禁止把独立发布预算当作真正断点续传。下一步优先审查可恢复 staging 或分批上传/验证方案，降低重复开销。保留Gold、Task01及冻结V1原状。


## 2026-10-29 23:59 CST 第九轮：真实未完成暂存目录可复用性验证（仍禁止切换）

- 实读 main SHA `19d92f10476bb2c40a2da84a64ffd74194b77685`；PR #116/#121/#123 均未合并，旧生产流程不变。
- Task01 run `40786202732` 任务已 cancelled；原始 job 日志显示 2026-10-29 03:51:52 UTC 创建 staging ID `1lLB28lNbC3OWZeGx2xctOjQlBlwjSIKM`，计划 7069 文件/2,805,499,976 字节；最后进度 06:55:44 UTC **4400/7069**，06:57:52 UTC 取消；未进入逐文件远端 SHA 验收及正式指针切换。
- **本轮 Google Drive 实查**：该 staging ID 仍存在，位于 Task01 `staging/`（parent `1oXqyvWAJytdi3EMJRGucUX8U7Lsx4_c1`），其下存在 `data/` 文件夹；这是未完成暂存，不是已验收版本。尚未递归核验全部4400项的远端 SHA。
- **本轮重新下载两端正式 JSON**：Task01 `current.json` ID `1Z51dXKxxt2J_4jtcW7NcSqsNt3V3qMdB`，SHA256 `5d712e4c30c5ccc9c70ebd6672ebc401df97ba14f5d46d07e877baf915482926`，as_of `2026-10-24`；其 `production_manifest.json` ID `1RzoGA4-RqdqQQSzmaE6M3wkiSh9-GVG1`，SHA256 `118e6afb6febd3993656afce9b46452809e5a05bf412db7c87515a891bc3e9e8`，5406项；Gold `manifest.json` ID `1wO2UFU4-kQsujMLo9G8rQLoUIeSRaCqn`，SHA256 `3975dc23a2137c7879aef8c0aec4263c887f260d708f31ca8bd66974903fd480`，as_of `2026-10-28`，两项质量标志 true。**正式日期差4天，禁止切换**。
- **实际代码工作**：草稿 PR #123 新增可显式指定 `--resume-stage-id` 的只读复核后续传逻辑：必须是当前 staging 的直接子目录，已存在文件按相对路径、字节数和远端 SHA256 与本地已验收清单逐一比对；未知/重复/篡改文件拒绝；仅上传缺失文件，最终仍全量远端 SHA 验收并最后切换指针。默认无该参数时原发布路径不变。代码 commit `ae6b4e4d1c6d6f84c45f53f9e323e5813b57c06e`，5项离线测试 commit `4539bc333910abbb7a06dfaa9821389a856cad81`，CI run `40907575903` 启动后尚待终态。**未在生产启用/运行，不得宣称已恢复发布**。
- 下一步先看 CI 和代码安全审查，明确源run的验收工件及暂存目录与本地 accepted inventory 一致；在可控独立发布任务中显式传入已核验 staging ID，避免重传4400项，但必须先测算远端 SHA 扫描耗时及剩余工件期限。旧数据、原下载与冻结V1不动。
