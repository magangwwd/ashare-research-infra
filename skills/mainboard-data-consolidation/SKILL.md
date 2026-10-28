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
