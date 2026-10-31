# Task01 未发布 checkpoint 被清理：实测事件与保护补丁（2026-10-31）

状态：**BLOCKED / NO PRODUCTION RESUME**。本文为实际运行与 Drive 只读收据，不是发布成功证明。

## 实测证据（北京时间 2026-10-31 12:00 轮）

- main SHA（本轮基线）：`3958a844042b99daf74dbe45f6d04117ee640d7c`。
- [只读 stage 审计 Run #41160259395](https://github.com/magangwwd/ashare-research-infra/actions/runs/41160259395)：**completed/success**，工件 `task01-stage-audit` ID **13028765093**；原始 `stage_audit.json`：
  - `source_run_id=41119601566`，`stage_id=1CHTuLs2_VS_MGbqlJnpZiJwOinR7ihGf`；
  - `accepted_as_of=2026-10-30`，`expected_files=7104`；
  - `sha256_verified_staged_files=4350`，`missing_files=2754`，`missing_bytes=822662604`；
  - `passed=true`，`pointer_unchanged=true`，`production_switch_allowed=false`，旧指针 `current_as_of=2026-10-24`。
- [Task01 Run #41141750990](https://github.com/magangwwd/ashare-research-infra/actions/runs/41141750990) 已 **completed/failure**，其 `Publish accepted snapshot to Drive` 步骤失败，`DRIVE_PUBLISHED=false`。该 Run 生成新 checkpoint artifact **13028541555**（3,863,135,211 字节，尚未过期），然后在 `Prune superseded recovery checkpoints` 步骤于 **2026-10-31 03:38:12 UTC** 执行 `deleted task01-checkpoint 13025260198`。
- 被删除的 **13025260198** 正是审计来源 Run #41119601566 的同源 checkpoint。当前该来源运行的 artifact 列表只见 `task01-validation-report`，**不再有同 run checkpoint**。不得拿 #41141750990 的 checkpoint 拼接 #41119601566 的 stage，故 **原来源的受控恢复工作流应 fail-closed，不得启动**。
- Drive 正式 `Ashare_Quant/current.json` 实读：`snapshot_as_of=2026-10-24`，5406 文件，2,331,724,247 字节，`critical_history_complete=false`。Gold 正式 `main_board/manifest.json` 实读：`as_of_date=2026-10-30`，两项质量标志为 true。两者不对齐，禁止切换。
- 以上只读审计成功是**暂存数据未损坏的有限证据**，不是生产数据完整或发布成功。旧正式指针保持不变。

## 根因

当前 `.github/workflows/task01.yml` 的 checkpoint 清理在上传失败后仍会执行，并仅保留最新一个 checkpoint。此策略可在独立只读审计仍在进行或刚完成时删除对应来源 run 的 checkpoint，导致同源验收/备份/暂存恢复门禁无法满足。

## 本 PR 的最小防护

- **仅当本次 `steps.drive_publish.outcome == 'success'` 时清理 GitHub checkpoints**。失败、取消、未进入发布步骤时不删除其他 run 的 checkpoint。
- 未发布 checkpoints 仍受 `retention-days: 3` 自动过期限制；不会永久保存，须及时审核来源和恢复可用性。该策略在连续失败期间会暂时增加 GitHub artifact 存储占用；不能把它当作无限期备份。
- 保留原 `task01-data` 并发锁、三档 schedule、现有验收及指针切换逻辑。**不修改冻结 V1、Gold 下载、Tushare 更新、Drive 生产数据或原历史文件**。
- 新增离线测试 `tests/test_task01_checkpoint_retention_gate.py` 验证清理仅能在成功发布后执行，保留3天到期约束。
- 当前仅为草稿 PR，**尚未部署 main**；必须等 GitHub CI 通过并完成安全复核才可合并。

## 后续执行门槛

1. 核验本 PR 的 push + PR CI；未通过就修复测试，不允许未经验证合并。
2. 等下一份真实 Task01 来源运行具备同 run 验收 + checkpoint、明确唯一 stage ID，且没有生产并发；**重新做只读全量 SHA 审计**，不能沿用旧审计和已删除的来源 checkpoint。
3. 审计报告通过、同 run artifact 未过期、旧正式 pointer 未变化后，才考虑 main 已部署的受控恢复工作流；仍必须验证文件数/字节、缺失补传、最终 manifest、pointer 和回滚。
4. Task01 正式数据日期追上 Gold 后，再推进 Gold 适配器、至少5交易日影子比对、冻结 V1 输出一致性和回滚；两条生产链路在此之前保持不变。

关联：[PR #116](https://github.com/magangwwd/ashare-research-infra/pull/116)、[Issue #99](https://github.com/magangwwd/ashare-research-infra/issues/99)、[Task01 启动手册](https://github.com/magangwwd/ashare-research-infra/blob/main/docs/task01-actions-start-method.md)。
