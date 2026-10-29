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
