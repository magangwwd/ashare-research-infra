# A股主板统一数据运行指南

## 当前阶段
本指南随 PR #116 归档。生产仍保留两条更新链路，**未完成真实影子验收，不得宣称去重上线**。Task01 Drive 发布文档记载第19次恢复+发布成功，但仍须查最近运行和实际指针。

## 路径与用途
- Task01 正式库：Google Drive `Ashare_Quant/current.json` 指向 `versions/<version>/data/clean/`；`staging/` 绝不可作为策略输入。
- Gold 策略库：rclone `gdrive:down_data/data/gold/main_board/`，含 `manifest.json`、`instruments.parquet`、`daily_snapshot.parquet`；策略输出 `gdrive:stock_state_strategy/output/`。这里的 gdrive 根为 project 父文件夹，并非 My Drive 根。
- Task01 使用 `GDRIVE_OAUTH_JSON`；Gold 使用 `GDRIVE_RCLONE_CONFIG_B64`。不在仓库保存凭据。

## 每日操作
1. 北京时间08:00查看 `task01.yml` 夜间 21:17/23:30/次日03:23 的最后一次有效成功；不要把备用跳过视为失败。
2. 检查 Task01 `current.json`、对应 `production_manifest.json` 的 validated 和实际数据日期，核对文件 ID/sha256 与远程文件。
3. 查看 `astock-quant-drive-first.yml` 23:50 运行及 Gold `manifest.json` 的 `as_of_date`、`validation.primary_keys_unique`、`validation.ohlc_valid`。
4. 对比两边主板证券集合、日期、行情单位、复权、daily_basic、历史月分区、停牌和退市；保存报告与 run ID。
5. 若任一失败，维持两套独立下载，不修改冻结策略、不删除旧版本，晨检报告标注差异。

## 手动诊断
- Task01：Actions → Task 01 data infrastructure → Run workflow，`verify_drive_auth` 只测授权，`publish_drive_snapshot` 只发布最近已验收快照；正常运行全部选项不勾。
- Gold：Actions → astock-quant - Drive-first strategy → Run workflow，`tests` 不需云盘，`preflight` 只检查，`gold-refresh` 会真实下载和发布。优先使用只读测试。
- 注意：手动触发会消耗 Tushare/Drive 配额，不得为了“测试”重复全量下载。

## 切换门槛与回滚
至少5个交易日影子对比；主板集合、行情与冻结策略输出无未解释差异；断点、校验失败和回滚演练通过。只有此后才能将 Gold 独立下载替换为读取 Task01 已验收版本。异常立即保留/恢复旧 Gold 并停止新指针切换。不得删除 Gold 旧历史或修改冻结 V1。

## 验收记录模板
`date | task01_run_id | task01_version | task01_as_of | gold_run_id | gold_as_of | universe_diff | ohlcv_diff | strategy_diff | rollback_test | verdict`


## 2026-10-31 受控恢复工作流部署与操作门禁

正式主分支已依次合并：#142（Google Drive 部分 308 ACK 安全偏移，`312bac8e`）、#143（显式 `--resume-stage-id` SHA 核验复用，`7fcc975f`）、#144（主分支**只手动**启用受控暂存审计+恢复的工作流，`fe10223718f12d6bf3f148877981d7796e9338b1`）。#144 最终双 CI [41159199544](https://github.com/magangwwd/ashare-research-infra/actions/runs/41159199544)、[41159204815](https://github.com/magangwwd/ashare-research-infra/actions/runs/41159204815) 均成功，186 passed/2 skipped；**未进行真实受控发布，不代表 Task01 正式指针已更新**。

受控工作流：`.github/workflows/task01-verified-stage-resume.yml`（Actions 名称 `Task 01 verified staged publication (manual)`）。仅 `workflow_dispatch`，无 schedule、无 Tushare 下载，限制 `main`，通过 `task01-data` 并发锁避免和 Task01 生产重叠，350 分钟上限。严格先确认指定 source run 的验收完成但发布步骤被取消，同 run 的 acceptance+checkpoint 工件未过期、checkpoint ZIP digest、源 task01 job **真实日志中唯一的 staging ID**；安全提取存档后用生产 OAuth 先运行 `--audit-resume-stage-id` 完整远端 SHA 核验和正式 pointer 稳定性验证，仅审计合格才调用 `--resume-stage-id` 复用文件补缺，然后核对最终正式发布回执及旧版本可恢复。独立留下 `task01-stage-audit` 和 `task01-stage-recovery-publication` 工件。任何异常不得认定上线。

**历史记录，已失效，不得作为恢复输入。** 曾有来源配对 `source_run_id=41119601566`、`stage_id=1CHTuLs2_VS_MGbqlJnpZiJwOinR7ihGf`；源运行日志唯一记录此 ID，checkpoint artifact `13025260198` 在核验时仍未到期，文件预计 2026-11-03 09:34:28 北京时间到期，**可能被后续自动运行提前清理**，只要同 run 工件缺失就必须停止，不得借用其他 run 的 checkpoint。当前 GitHub 连接器**不提供 workflow_dispatch 动作**，故本轮未运行正式恢复、未生成两项受控工作流工件。若使用 GitHub 网页 Run workflow，需在 main 选择该工作流并手工填写两个原样 ID，先确认 checkpoint 和正在运行任务状态；工作流本身执行严格安全门禁。禁止从旧日志自动挑选 staging。

真实云盘先前读取的 Task01 `current.json` 仍为 `2026-10-24`（5406 文件，历史完整性 false），Gold 正式 manifest `2026-10-30`。即使 Task01 新指针成功，也必须继续推进独立适配器、连续5个交易日影子策略对比、冻结 V1 输出一致性和回滚演练，才能考虑切换 Gold 下载。没有完成前两条生产链路照常保留，不删数据、不碰冻结 V1。详情见 `docs/mainboard-baseline-audit-20261028.md`、Issue #99。


## 2026-11-02 18:00 CST｜发布故障处置与证据更新（覆盖上文旧配对）

本节基于 2026-11-02 18:00 CST 前的 GitHub API、Google Drive 正式文件重新读取及本轮实际 SHA256 校验；后续操作必须重新核实状态，**不能将历史结果当作新事实**。

- `main` 当前 SHA `b33673df7ca5433798951f0c528d2e5e406dda45`。最近60条 Actions 记录中未发现正在运行的 Task01 生产/审计；该结果不替代启动前的全量并发检查。
- 最近取消的 Task01 生产运行 [#41359343597](https://github.com/magangwwd/ashare-research-infra/actions/runs/41359343597) 对应**唯一同源**暂存 `1l43dDu1jY3hsJ_K8dkAV12ld_xPz-95r`，checkpoint artifact `13116312860`，digest `sha256:9fab53d721d2c20a2100b5df44e93a3d59094a7733cff5f72d06fc64114527ee`，GitHub 报告到期 `2026-11-05T04:26:15Z`；验收工件 `13106636666`。旧 `41119601566` 配对已失效，绝不跨 run 配对。
- 对新批次的只读审计 [#41397631741](https://github.com/magangwwd/ashare-research-infra/actions/runs/41397631741) `completed/failure`，Job `125122297420` 日志：`audit_failure=downloaded_sha256`、`path_fingerprint=0b3901a39e05d22a`、`verified_before_failure=8`，**无合格审计 artifact**；禁止对该批次直接执行 verified-stage-resume、禁止忽略元数据 SHA 差异。
- [PR #164](https://github.com/magangwwd/ashare-research-infra/pull/164) head `53a2955c36c1d28dc5dab705ca14aba4916869b5` 的两次精确 CI `41415111430` / `41415118356` 均 success；其改动只在报告除 `checked_at` 外的所有字段相等时保留原时间，实质差异不得复用时间。**PR 仍为草稿，尚未合并；该修复不会自动修复已有远端文件 SHA。**
- Google Drive Task01 正式 `current.json` ID `1Z51dXKxxt2J_4jtcW7NcSqsNt3V3qMdB` 两次读取一致，版本 `2026-10-24T211915.119571_0000-7c30e07d`、`as_of=2026-10-24`、`file_count=5406`、`total_bytes=2331724247`、`critical_history_complete=false`；对应 `production_manifest.json` ID `1RzoGA4-RqdqQQSzmaE6M3wkiSh9-GVG1` 本地 SHA256 `118e6afb6febd3993656afce9b46452809e5a05bf412db7c87515a891bc3e9e8`，与正式指针一致，清单路径及文件 ID 各5406个无重复；**不等于逐个重新下载所有生产 Parquet 核验**。
- Gold `manifest.json` ID `1wO2UFU4-kQsujMLo9G8rQLoUIeSRaCqn` 本地 SHA256 `3687f018cad6c96bdaf704217900925a773cc90fcb050031d6dd07cf20f33580`，`as_of_date=2026-10-30`，本轮未逐个重新下载核验底层 Parquet。

**下一可执行门禁：** 先确定不一致仅限可重建的元数据还是包含其他文件；不得仅根据 `checked_at` 差异推定所有文件安全。保留现有失败证据，评估在已修复的代码与有效同源检查点下**新建独立暂存版本**或等待下一次合法生产批次，重新生成不可变 inventory、逐文件 SHA 和来源/指针回执。不得原地覆盖已失败 stage 以伪造成功。仅当新 stage 全量审计报告 `audit_only=true`、`passed=true`、`pointer_unchanged=true`、`production_switch_allowed=false`、同源 checkpoint/SHA/文件数/字节/指针均通过，且无生产并发、回滚门禁满足，才评估受控发布。继续保留旧 Raw/Clean、Gold 独立下载、冻结 V1 和08:00晨检；Gold 去重切换仍需5个有效交易日影子与冻结策略排名一致性。
