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
