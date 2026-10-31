---
name: mainboard-data-consolidation
description: 审核并逐步整合 GitHub Task01 A股主板基础数据与 Gold 选股数据；先验证后切换，保存证据与回滚能力。
---

# A股主板数据整合 Skill

适用仓库：`magangwwd/ashare-research-infra`。运行前先读 `docs/mainboard-data-consolidation-plan-20261028.md`、`docs/MAINBOARD_UNIFIED_OPERATIONS.md`、`docs/drive-phase2.md`、`astock-quant/stock_state_strategy/DRIVE_ACTIONS_OPERATIONS.md`。

## 执行规程
1. 从 GitHub 读取 main SHA、PR #116、`task01.yml` 与 `astock-quant-drive-first.yml` 的最近真实 runs、日志、artifacts；从 Drive 只读两边正式 manifest。不要仅凭工作流配置宣称运行成功。
2. 记录 Task01 pointer、manifest SHA256、as_of、数据范围、缺口、文件数；Gold manifest、股票名录、历史月分区和状态校验。区别已验收与未验收。
3. 先修复 Task01 正式发布与凭据/目录权限，再开发单向适配器；适配器必须使用 pointer 指向的已验收版本，不能读 staging，也不能从旧 Gold 反向覆盖 Task01。
4. 用影子目录输出 Gold 格式，验证股票集合、日期、OHLCV单位、daily_basic、adj_factor、停牌、退市、历史与策略回放。保留每轮 diff 与 run ID，连续5个交易日。
5. 测试失败旧指针不动、版本回滚可用；达标后才提独立生产切换 PR。**不得自动关闭 Gold 定时下载或删除旧库。**
6. 每次完成一项，在 PR/文档记录：变更、测试命令、实际结果、阻塞、下一步。报告用简明中文：已完成/未完成/影响/下一步。

## 禁止事项
不打印 Secrets；不绕过工具安全拒绝；不把 CI 通过当作真实 Drive 发布；不改变冻结 V1 选股规则；不重复触发大规模下载；不把 15分钟数据直接接入日频冻结策略。

## 完成定义
GitHub Actions 真实执行 + 两端 Drive 文件实际校验 + 5日影子对比 + 策略输出一致 + 回滚演练 + 正式切换与观察；未满足时只能标为进行中。


## 生产端只读排障（2026-10-28 增补）

当连接器显示正式 marker 缺失时，先核对 `docs/mainboard-baseline-audit-20261028.md`，不要直接判定数据丢失。PR #116 新增 `tools/mainboard_remote_probe.py` 和 `.github/workflows/mainboard-dual-drive-probe.yml`。该 workflow 必须先审查并合并到 main，才可在 Actions 中手动触发；它不设 schedule、不下载行情、不修改云盘，输出 `mainboard-remote-probe` artifact。分别检查 Task01 OAuth 与 Gold rclone 的正式根目录、项目根 marker 和核心文件；`PASS` 仅代表文件名/类型存在，仍须校验文件内容、日期、SHA、5日影子策略和回滚。若显示 `MISSING_SECRET`、`WRONG_REMOTE_ROOT`、`PROBE_ERROR` 或 `MARKER_MISMATCH`，保持旧系统不动并针对性修复。

## 指针与 Manifest 第二层只读验收（2026-10-29）

PR #116 诊断脚本新增 Task01 指针与 manifest SHA256、版本父子关系、版本/日期/文件数一致性检查；Gold 增加 manifest 日期和两项质量标志检查。此检查**不读取 Parquet、不下载历史、不验证全分区、不保证时效性**。返回 `INVALID_POINTER`、`BROKEN_VERSION_LINK`、`MANIFEST_SHA_MISMATCH`、`MANIFEST_POINTER_MISMATCH`、`GOLD_NOT_VALIDATED` 等时一律阻止生产切换。新增10项测试在 `tests/test_mainboard_manifest_contract.py`；必须复核 CI run `40722398618` 终态。只有通过生产凭据的 GitHub Actions 手动只读诊断并检查 artifact，才能继续影子数据适配。


## 第四轮实证与日期一致性门禁（2026-10-29）

此前连接器查询曾报正式 marker 缺失；**重新实查已确认两侧 marker 均存在**，不要沿用旧结论。证据与SHA详见 `docs/mainboard-baseline-audit-20261028.md`。

- Task01 正式 pointer `snapshot_as_of=2026-10-24`，manifest SHA 与实际下载一致；5406项，历史完整性标志仍为 false。
- Gold manifest `as_of_date=2026-10-27`，两份核心 Parquet 的实际 SHA 与 manifest 一致；因此 **Task01 落后 Gold，禁止替换**。
- 两个 Task01 发布失败 run 40562368344、40598869222 的日志都显示 `Drive request failed (410)`，阶段为 Drive 发布；具体 HTTP 请求类型待定位，不能未经证据判为 OAuth 失效、配额不足或会话过期。run 40748947088 检查时仍在运行。
- `tools/mainboard_remote_probe.py` 新增 `compare_as_of`：两个源均验收且日期相同只返回 `ALIGNED_DATE_ONLY`；不同则返回 `TASK01_BEHIND_GOLD` 或 `GOLD_BEHIND_TASK01`，并以退出码2阻止继续。即使 `ALIGNED_DATE_ONLY` 也不允许生产切换/删除。测试 `tests/test_mainboard_cross_source_date.py`，本轮提交后 CI 必须再次核实。
- 下一步先排查 410 的具体请求阶段，待 Task01 新正式指针恢复更新后再做 Gold 影子对比。禁止在 Task01 落后时直接去重下载。


## 第五轮：2026-10-29 08:00，410与240分钟超时分开处理

- Task01 run 40748947088 已确认 `cancelled`，其发布在 7046 文件仅报告100项 staged 时被取消；这是独立于 HTTP410 的**执行时间瓶颈**。新 run 40786202732 检查时 pending。
- 历史两次 HTTP410 的请求类型仍未知。新增独立草稿 PR #121：仅对 `resumable_upload` 的410进行有界重试，并对远端文件存在性、元数据、SHA严格核实；未通过真实生产验证，不得当作修复完成。
- 下一轮先读 PR #121 CI 40791360486 和 Task01 新 run 的终态；若410类型不是 resumable PUT，拒绝合并 PR #121 作为该问题的修复。另行设计独立发布 job / 可恢复 staging，避免240分钟时限导致完整数据永远不能提交。
- 仍不改冻结 V1，不停止 Gold，不删除旧数据。


## 第六轮：2026-10-29 12:00，独立发布超时恢复草稿

- 从完整日志纠正：Task01 run `40748947088` 不是仅100个文件，最终上传达到 **3600/7046** 后因240分钟job上限取消；这仍未完成全量远端SHA验收与指针切换。
- 最新 run `40786202732` 检查时 in_progress，验收 artifact 已产生，发布阶段尚未确认成功；进行中日志404不能解释为没有上传。
- Task01 正式指针仍截至10月24日，Gold正式manifest已到10月28日，继续阻止去重切换。
- 新增隔离草稿 PR #123：超时样式取消（235–265分钟）+同run验收和checkpoint双artifact门禁，使用独立350分钟job重试已验收快照，保留Task01并发锁、不重新下载行情；增加旧日期回退拒绝。**仅草稿，未合并未生产验证**；CI `40815247495`/`40815261324` 待核验。不能因为独立发布预算增加就宣称问题解决。
- 下一轮先确认新Task01 run终态和410操作类别；核对PR #123 CI/安全边界，真实发布前不关闭旧任务、不删数据、不改冻结V1。


## 第七轮：2026-10-29 16:00，取消状态与工件完整性

- Task01 run `40786202732` 已终止为 `cancelled`（发布步骤取消），但同一run的验收与checkpoint工件均已保存：`12871950683`、`12878879550`；checkpoint约3.42GB，GitHub提供SHA256。不要将验收通过等同于正式发布。
- Drive 正式指针仍是 Task01 `2026-10-24`，Gold manifest `2026-10-28`；禁止切换和删除。
- 草稿 PR #123 新增工件digest/体积校验和提前释放ZIP磁盘空间，提交 `4bb7c285af3f91beb2f90788974a2b741a8d93ad`；新CI `40838902886` 尚未通过最终验收。该恢复工作流尚未合并、未运行生产，不得称为修复成功。
- 下轮先检查CI与取消日志，再独立审核workflow_run信任边界、tar解包安全、350分钟成本和实际恢复可行性；只有通过闸门才能考虑合并。详细证据见 `docs/mainboard-baseline-audit-20261028.md`。


## 第八轮：2026-10-29 20:00，超时恢复安全闸门与真实状态

- 实读 main SHA `19d92f10476bb2c40a2da84a64ffd74194b77685`；PR #116、#121、#123 仍为草稿，生产未切换。
- Task01 run `40786202732` 的 `task01` job 最终 `cancelled`，取消的是 `Publish accepted snapshot to Drive`；日志显示最后上传到 **4400/7069** 文件（2026-10-29 06:55 UTC），并非仅3600。质量验收和同run checkpoint已完成；checkpoint artifact `12878879550`，3,424,299,634 bytes，SHA256 `f2526309edea55fe5bcffb9e77ac032f870b63ae6cff5f9addc1d3b2a5ff99bd`，到期 `2026-11-01T06:59:59Z`。验收报告 artifact `12871950683`。不可把工件上传成功当成Drive发布成功。
- 本轮真实Drive读回：Task01 `Ashare_Quant/current.json` 的 `snapshot_as_of=2026-10-24`、`validated=true`、`critical_history_complete=false`；Gold `main_board/manifest.json` 的 `as_of_date=2026-10-28`、两个质量标志为true。两端日期不一致，禁止切换。
- PR #123 上轮 CI `40838902886` 已 `success`。本轮新增严格门禁：仅当源run已完成验收且 **Drive发布步骤本身被取消** 时，才允许恢复；解压工件仅允许 `data/`、`metadata/` 路径和常规文件/目录/链接类型。提交 `fcaf77966f99f3025809c4ebda8ca5e10b19c2ea`、`f56b2484f4a470da16a8c1b16057c733bc9652f5`，中间CI `40870320426`、`40870312757` 因旧测试断言不适配内部符号链接而失败；已修正测试，最新提交 `a3e2eec8a378ee1ff6e33a115812c02a4ffc9aa2` 的 CI `40870564343`、`40870554595` 均通过，其中前者 **157 passed、2 skipped**。仅为离线CI通过，仍非生产发布。
- **重要运行限制**：`workflow_run` 仅响应工作流部署后新完成的源run，不会追溯已取消的 `40786202732`；3天工件会过期，且恢复方案重新上传全部约7069文件，350分钟是否足够尚未验证。不可把该草稿描述为已恢复，也不要为试验重复触发昂贵的全量上传。
- 下轮先核查新CI和最新正式指针；独立复核GitHub Actions工作流信任边界、工件期限、运行时间与成本，确认恢复方式确实可执行后才考虑合并。并行设计可验证的增量/续传式staging，避免每轮重传全量。原有Task01与Gold下载均保留，冻结V1不变。


## 第九轮：2026-10-29 23:59，显式暂存续传草稿

- 实查 Task01 取消 run `40786202732` 的 staging ID `1lLB28lNbC3OWZeGx2xctOjQlBlwjSIKM` 仍在 Drive `staging/` 下；日志确认最后进度 `4400/7069`，但这些未逐文件复验，不能冒充正式版本。
- PR #123 新增可选 `python -m data_pipeline.drive_publish --resume-stage-id <经核验的暂存文件夹ID>`，**仅供后续受控发布演练**。默认原发布逻辑不变；程序校验 stage 直接父目录、已存在路径集合、大小、远端 SHA256，发现不匹配则拒绝，缺失文件才上传，完成后仍须全量远端 SHA 和指针读回。该功能在 draft 中，未合并、未生产执行。
- commits `ae6b4e4d1c6d6f84c45f53f9e323e5813b57c06e`、`4539bc333910abbb7a06dfaa9821389a856cad81`；CI `40907575903` 需检查终态。禁止将单测绿灯视为正式发布。
- 两端正式manifest重新下载确认：Task01 10月24日、Gold 10月28日，仍禁止切换、去重、删除。后续必须审核 CI、工件有效期、运行成本及完整远端复验，再设计受控生产验证；不从历史日志自动选择未验证的stage。

- 第九轮补充核验：PR #123 最新 CI [40907575903](https://github.com/magangwwd/ashare-research-infra/actions/runs/40907575903) 已 completed/success，离线测试 **162 passed、2 skipped**；只证明代码离线验收，尚未用生产 OAuth 执行 staging 续传或正式指针切换。


## 第十轮：2026-10-30，续传配额与只读证据门禁

- 真实 Drive pointer 与 manifest 核验：Task01 `2026-10-24`，正式 manifest SHA256 `118e6afb6febd3993656afce9b46452809e5a05bf412db7c87515a891bc3e9e8` 与 pointer 相符；Gold manifest `2026-10-28`，两项质量标志为 true。日期落后，仍禁止切换。
- 取消 run `40786202732` 的 stage `1lLB28lNbC3OWZeGx2xctOjQlBlwjSIKM` 仍存在，**不是正式数据，也尚未完成逐文件 SHA 验证**。checkpoint `12878879550` 将于 2026-11-01 06:59:59 UTC 到期，不能依赖过期后恢复。
- PR #123 的显式续传现在按**经远端 SHA 验证后的缺失字节 + 1MiB**判断 Drive 剩余容量，不再要求重复预留整份快照；普通全量发布保留原全量容量门禁。缺少 staging 根、父目录不符、路径/大小/SHA不符、配额不足均拒绝，不改生产 pointer。提交 `3af09c78`、`88c43cde`、`e656c49e`，CI `40944992102` 待最终确认。
- 不要为了赶 checkpoint 过期而跳过安全审查、直接合并草稿或重复大规模上传；下一轮先验收 CI 与生产凭据只读差异，再决定是否进行受控续传。完整证据见 `docs/mainboard-baseline-audit-20261028.md`。


## 第十一轮：2026-10-30 08:00，暂存续传只读预检（仍未发布）

- 实读 main SHA `370630f3b7681f60bbcc126982e7ddcaec265fc1`；PR #116/#121/#123 仍 draft/open。Task01 run `40948715414` 仍在发布步骤，备份 run `40972437556` pending，均不能算正式发布成功。
- 本轮从 Drive 直接读取正式 JSON：Task01 `current.json` 的 `snapshot_as_of=2026-10-24`；Gold `manifest.json` 已更新到 `as_of_date=2026-10-29`，质量标志为 true。历史完整性标志仍不满足统一切换条件；禁止以旧 Task01 覆盖 Gold。
- PR #123 新增 `--audit-resume-stage-id <人工指定的stage ID>`：对恢复 checkpoint 的已验收本地清单与远端暂存逐文件核对 SHA256/大小/路径，核验正式 pointer/manifest、日期与剩余配额，输出已验证复用数、缺失数和缺失字节；**仅 GET/read，不创建文件夹、不上传、不移动版本、不更新指针**。调用示例：`python -m data_pipeline.drive_publish --audit-resume-stage-id <STAGE_ID> --report metadata/stage_audit.json`。需在受控、具备已验收 checkpoint 的运行环境中执行；不可从旧日志自动挑选 stage。
- 代码提交 `07595d89`，无写入回归测试 `dfdf78cc`；新 CI `40977006624` / `40977014204` 仍待终态。**尚未用生产凭据执行只读预检**，不能声称4400个暂存文件已核验。原 `40944992102` 离线 CI 已通过。
- 必须先审查并通过最新 CI，确认 stage 与本地 checkpoint 内容完全一致，再决定是否受控续传；恢复工件 `12878879550` 到期时间为 2026-11-01 06:59:59 UTC，不得因此跳过安全审查。


## 2026-10-30 12:00 CST：旧生产 Manifest 完整性保护（修复草稿）

- 实读 main SHA `68831d6cd4d2b2f7292a3539b7bb662ef51babe4`，整合 PR #116 与恢复 PR #123 均未合并。Task01 run `40948715414` 已 cancelled（发布步骤超时），备份 run `40972437556` 检查时仍在发布，不能称为成功。
- 本轮真实下载 Drive 两侧正式 JSON：Task01 pointer `2026-10-24`、5406文件、2,331,724,247字节，正式 manifest SHA256 `118e6afb6febd3993656afce9b46452809e5a05bf412db7c87515a891bc3e9e8` 与 pointer 相符；Gold `2026-10-29`，两项质量标志为 true。两侧相差5个自然日，**仍不准切换**。
- 在恢复草稿 PR #123 发现一项真实风险：原发布前只检查旧生产 manifest 的 `validated` 和版本名，未核对指针记录的 SHA256 和 manifest 的版本目录父子关系。已补充发布前拒绝损坏/错目录的旧 manifest，避免在旧版本不可确认恢复时启动新 staging。提交 `a0888bf238b7681c30353c7cdd596e6f5b4acf1d`；新增3组无写入回归测试提交 `591d582cb02a2c26e8720e53b625d0aed786fc07`。最新 CI `41000945440` 创建时为 queued，**须复核终态，不可称测试通过或已生产修复**。
- 本次只修复草稿的安全门禁，没有触发全量下载、没有改生产指针、没有停止 Gold，也没有修改冻结 V1。下一步先核验 CI，再评估旧 checkpoint 和 staging 的只读校验及恢复可执行性。

## 第十二轮：2026-10-30 16:00，受控只读暂存核验工作流

- 最新 Task01 run `40972437556` 发布取消，日志到 `7000/7094`，仍未完成远端 SHA 和正式指针提交。stage ID `1GDuiqVqySPIURk-yoeRkBNnb7L-6ChJN` 已确认在 staging 根下，**尚未逐文件验证**。同run checkpoint `12960014331`（3.76GB）至 `2026-11-02T07:06:43Z` 到期；验收 artifact `12953020958`。
- 在 PR #123 草稿新增 `.github/workflows/task01-stage-audit.yml`：仅手动 `workflow_dispatch`，仅 main，显式传 `source_run_id=40972437556`、`stage_id=1GDuiqVqySPIURk-yoeRkBNnb7L-6ChJN`；先验证来源 run 的接受状态、工件摘要和解包安全，再只读调用 `python -m data_pipeline.drive_publish --audit-resume-stage-id "$STAGE_ID" --report metadata/stage_audit.json`。**代码未合并、工作流未在生产执行；须先审核 CI 和权限**。
- 仅当审计 artifact 中 `audit_only=true`、`passed=true`、`pointer_unchanged=true`、`production_switch_allowed=false` 且远端 SHA 逐文件无异常时，才可评估受控续传。即使只读核验成功，也不代表发布成功。切勿自动选 staging、直接合并含自动恢复的草稿 PR、修改冻结策略或停止旧下载。
- 最新正式 Drive：Task01 `2026-10-24`，Gold `2026-10-29`，仍阻止统一切换。证据详见 `docs/mainboard-baseline-audit-20261028.md`。

## 2026-10-30 19:06：生产文件实测与昂贵恢复的显式启用门禁

- 生产文件本轮实际 SHA256 校验：Task01 正式 manifest `118e6afb6febd3993656afce9b46452809e5a05bf412db7c87515a891bc3e9e8` 与 `current.json` 相符，正式日期仍 `2026-10-24`；Gold 正式 manifest `2026-10-29`，其 `instruments.parquet` 与 `daily_snapshot.parquet` 下载哈希均与 manifest 相符。仅验证这三项，不代表历史分区或全量数据完整。
- 最新取消 run `40972437556` 的暂存目录 `1GDuiqVqySPIURk-yoeRkBNnb7L-6ChJN` 仍存在；checkpoint artifact `12960014331` 于 `2026-11-02T07:06:43Z` 到期。**7000个暂存文件没有逐项验 SHA**，不可直接续传或当成正式库。
- PR #123 已给自动超时恢复增加显式仓库变量 `TASK01_TIMEOUT_RECOVERY_ENABLED == 'true'` 和源工作流路径核验，默认不触发高成本350分钟自动重传；提交 `b9d04d8`、`e810cfc5`，必须核验新 CI 终态。未完成生产只读核验前不要启用变量或合并草稿为生产恢复。
- 下一轮优先：确认 CI → 安全审核并执行只读 stage 审计（需合法可用的 main 工作流）→ 保存 `stage_audit.json`、逐文件校验与缺失字节 → 评估续传成本及生产发布 → 5交易日影子验证。始终保留旧指针、Gold下载与冻结V1。完整证据在 `docs/mainboard-baseline-audit-20261028.md`。


## 长耗时只读 SHA 审计的并发指针复核（2026-10-30）

PR #123 的暂存审计可能耗时数小时。审计开始时记录正式 `current.json` 文件 ID 和完整原始字节；逐文件核验后**再次读取正式指针 ID/字节和旧版本 manifest**，任一变化必须判为失败，不得输出 `pointer_unchanged=true`。本防护在草稿 PR #123 提交 `fb4c59a5` 和回归测试 `7ee479a6`，**尚待 CI/生产验证**。若 Task01 在审计期间更新了正式库，应重新读取最新 pointer 并重新决策，不能使用过时审计报告续传。保持两套生产下载及冻结 V1 不变。


## 暂存只读审计的证据绑定（2026-10-30）

- PR #123 草稿只读审计输出必须含 `audit_only=true`、`passed=true`、`production_switch_allowed=false`、`pointer_unchanged=true`，且 `stage_id` 必须与人工指定目录一致、`pointer_sha256` 必须为64位小写十六进制。
- 审计工作流验收后在 artifact 中补记 `source_run_id`、`checkpoint_sha256`、`audit_run_id`、`audit_commit_sha`，供后续受控恢复核对来源。只读报告不是发布凭证；仅在CI、生产审计和后续正式指针读回均通过后才讨论续传。
- 2026-10-30 实读 Task01 `2026-10-24`、Gold `2026-10-29`，Task01 manifest SHA 与指针一致，Gold manifest SHA `79699efb7c09423450e02b4e1a2a1ca7fb28b1fffca7dd847847a45b13b38291`；详见 `docs/mainboard-baseline-audit-20261028.md`。未核验暂存7000文件、未切换生产。
- GitHub `task01-stage-audit.yml` 的早期 push failure run `41023552472` 为零jobs，**不等于实际审计失败或成功**。工作流尚未部署main；需先解决可执行入口，不得重复全量上传试错。


## 2026-10-31：独立只读审计部署顺序（PR #140）

原 #123 包含自动恢复与写入续传，不宜为运行只读审计而整包合并。已建立独立草稿 PR #140，只含手动 main-only 的 `task01-stage-audit.yml`、`--audit-resume-stage-id` 只读路径及测试，默认 `publish()` 不变。先验收 #140 CI、安全边界；再合并独立只读 PR 到 main；**只在 main** 用仍有效的来源 run/checkpoint 和明确 stage ID 手动执行。工作流需保存 `audit_only=true`、`passed=true`、`pointer_unchanged=true`、`production_switch_allowed=false`、源run/checkpoint/pointer SHA 与实际 Actions run ID。若工件过期或有SHA不符，停止，不能用旧 staging 推断成功。#123 继续 draft，严禁为绕过 job main-only 限制而在 PR 分支放宽密钥门禁。2026-10-31 实测 Task01正式日期10-24、Gold正式日期10-29，仍不得切换。


## 2026-10-31：只读暂存审计已部署，必须先检查工件仍存在

- 独立只读审计 [PR #140](https://github.com/magangwwd/ashare-research-infra/pull/140) 经CI #41120148907（test job success）与代码/权限检查，已合并 main，SHA `4afc2d300dfa328c010ddab4a5f7d9d0421ea747`。工作流 `.github/workflows/task01-stage-audit.yml` 仅允许 main 上手动运行，需显式 `source_run_id` 与 `stage_id`；无 schedule、无自动恢复、无行情下载或Drive写入。原 `publish(...)` 默认路径不变。**合并仅代表可部署，不代表已完成生产审计。**
- 手动审计前必须通过 GitHub Actions artifact API **实时列出同一来源run的两项未过期工件**：`task01-validation-report` 和 `task01-checkpoint`（含合法SHA256 digest、<=5GB）；检查来源run为 main Task01 已验收、发布步骤取消；检查stage是该run实际创建且仍位于 staging 下。不可仅信旧PR文本、曾经的工件ID或job步骤“Save checkpoint success”。
- 反例：本轮对 Task01 run `40972437556` 的工件列表只查到验收报告 `12953020958`，**未查到**曾记录的checkpoint `12960014331`。即使其stage `1GDuiqVqySPIURk-yoeRkBNnb7L-6ChJN` 仍存在，也不得直接提交该run的只读审计或续传；应先寻找最新合法可用的同run工件并核实其来源stage。
- 只有实际审计 run 成功、下载 `task01-stage-audit` artifact、核验 `audit_only=true`、`passed=true`、`production_switch_allowed=false`、`pointer_unchanged=true`、`stage_id`、`source_run_id`、`checkpoint_sha256`、`pointer_sha256`、`sha256_verified_staged_files`、`missing_files` 和 `missing_bytes` 后，才考虑后续受控发布；审计成功仍不能算发布成功。若连接器缺少 workflow_dispatch 能力，明确记录“未触发”，不得虚构 run ID。
- 2026-10-31 实际两侧正式核验：Task01 `as_of=2026-10-24`，production manifest SHA256 `118e6afb6febd3993656afce9b46452809e5a05bf412db7c87515a891bc3e9e8` 匹配；Gold `as_of=2026-10-30`，instruments SHA256 `6e06b2f17c6d46384df35b15c558f2fb1c9fe845e0cc7f608eec95d5e0789586`、daily_snapshot SHA256 `6eb60191b7c9f589b3de9028fe9e55ac7200daa0772846d3da61d1cf7f906f08` 均匹配。日期不一致且Task01历史不完整，继续禁止切换、删除、停Gold或修改冻结V1。
