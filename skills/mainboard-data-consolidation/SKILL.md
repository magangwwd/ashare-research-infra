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
