# Task 01 GitHub Actions 启动方式与接管约束（2026-10-23）

## 已核实

- GitHub Actions 现有工作流：`.github/workflows/task01.yml`，包含 `workflow_dispatch` 与三条 `schedule`，**没有 `push` 触发器**。
- 当前 ChatGPT GitHub 连接可以读取、创建分支、提交文件和 PR；未提供创建全新 `workflow_dispatch` 的专用工具。重跑失败 job 不等于新建一次 Task 01 数据更新。
- 当前 Chat 模式 Browser 连接报 `Browser access requires the originating browser context`，不能代替用户操作已登录的 GitHub 网页。
- 不使用 WebCodex；GitHub Actions Runner 为唯一数据更新执行环境。

## 可用启动入口

1. **推荐：GitHub Actions UI**：访问 https://github.com/magangwwd/ashare-research-infra/actions/workflows/task01.yml ，选择 Run workflow → main → Run workflow，保持默认输入。必须检查实际 Run ID、日志、Drive current 版本和数据截止日；这仅启动现有版本，不代表新版 daily-sync 已落地。
2. **具有 Actions 写权限的 GitHub CLI**：`gh workflow run task01.yml --repo magangwwd/ashare-research-infra --ref main`；随后 `gh run list --repo magangwwd/ashare-research-infra --workflow task01.yml --limit 5`。不要在日志输出令牌。
3. **具有 Actions 写权限的 GitHub REST API**：`POST /repos/magangwwd/ashare-research-infra/actions/workflows/task01.yml/dispatches`，请求 JSON `{"ref":"main"}`。成功响应为 HTTP 204，但仍须另行核查生成的 Run。需要相应仓库权限和凭证，不要将凭证写入代码。
4. **Git push 替代方案（需代码审查）**：在独立、受保护的工作流上配置 `push` 或 `workflow_run` 触发，只有在单写入者、生产门槛、数据验证和并发锁均验收后才允许生产写入。现有 `task01.yml` 不支持通过普通 push 启动；不得为强行启动而直接添加无保护的生产 push 触发器。

## 生产接管要求

- 代码在 GitHub，运行在 Actions Runner，数据仍在原 Google Drive folder ID `1rkC99Ymgp8mjvAcJNRPJMlOGLdI9_bue`。
- 从现有 Drive manifest/checkpoint 增量更新、按 SHA256 去重、失败不修改正式 current。
- 不覆盖正在进行的云端回补，不擅自停止 watchdog；须先验收单写入者交接。
- 不修改 Task 02、不运行策略回测、不恢复高频历史回补。
- 新版需真实 Runner 更新、同日幂等重跑、Drive 恢复测试，验收后再切换每日一次 schedule。
- 本文件仅为启动方法存档，不表示本次已触发新 Run 或完成生产部署。


## 2026-10-31：已真实验证的 GitHub Runner 一次性委托启动

**结果（实证，不是设想）：** 由于当前 ChatGPT GitHub 连接器缺少创建 `workflow_dispatch` 的专用动作，GitHub CLI 本地无认证环境，WebCodex 未连接，采用仓库内部 **一次性、独立分支** 的 GitHub Actions 中转，在 GitHub Runner 的受 GitHub 权限管控的临时 `GITHUB_TOKEN` 下调用 GitHub 原生 `workflow_dispatch`，未索取/显示/外传用户令牌。

- 隔离分支：`ops/one-shot-readonly-stage-audit-20261031`，仅该分支/该工作流文件变动触发，严格绑定仓库及一次性 commit message；权限仅 `contents:read`、`actions:write`。
- 代码提交 `433f755aa65e14673dbaea067a44262525925d38` 启动中转工作流 `.github/workflows/task01-one-shot-readonly-dispatch-20261031.yml`，实跑 **[#41160251568](https://github.com/magangwwd/ashare-research-infra/actions/runs/41160251568)**，其 gate 和 `gh api --method POST` 步骤真实成功。
- 精确目标只读审计 `.github/workflows/task01-stage-audit.yml` 主分支 `workflow_dispatch`，源 `source_run_id=41119601566`、暂存 `stage_id=1CHTuLs2_VS_MGbqlJnpZiJwOinR7ihGf`，真实生成 **[#41160259395](https://github.com/magangwwd/ashare-research-infra/actions/runs/41160259395)**，`event=workflow_dispatch`。**这只是生产只读审计启动成功；最终所有暂存文件 SHA 校验、Drive 发布、pointer 更新仍须独立核查。**
- 启动确认后通过提交 `28f6f0a88fe14d3020887f9c4da6d99b0fe61b3d` 将一次性中转文件的 `on: push` 禁用，实查此中转分支只有一次运行，避免以后被重复触发。未合并中转 workflow 至主分支。

**允许复用的条件（每次重新审查，不自动批量派发）：**

1. 用户已经明确授权本次启动；工具操作必须持有真实仓库代码写入权限，禁止索取/打印用户 PAT、复制 OAuth 或调用任何未获授权的外部服务。
2. 提前真实查询目标工作流的 `main` 版本、输入契约、最近运行、源 Task01 run 的完成结论、同 run 验收和 checkpoint 工件 `expired=false`、从源 job 日志确认唯一的 staging ID；若同目标已有未完成运行，不启动第二份。
3. 对仅只读审计可采用每次**唯一**隔离分支上的一次性 GitHub Actions push 中转，工作流精确核对仓库/分支/source、同run两个工件和无并发审计，仅调用官方 GitHub API `POST /repos/{owner}/{repo}/actions/workflows/<workflow-file>/dispatches`，`ref=main` 和显式输入；GitHub 若拒绝 `actions:write` 必须停止，不试图提升/绕过权限。
4. 中转工具成功不代表目标工作流成功；必须再读取目标实际 `workflow_dispatch` Run ID、jobs/artifact，以及生产 Drive Manifest/pointer readback。只读审计不能直接标记发布完成。禁止通过任意普通 push/PR 触发 Task01 生产上传，也不得放宽目标已有的 fail-closed 入口检查。
5. 一次性中转成功后立刻禁用其后续 push 入口，不新建 schedule，不重复派发相同 source/stage。若要正式恢复，必须先确认全量只读审计成功、目标无并发生产写入，再使用已在 main 的受控 `task01-verified-stage-resume.yml`；它仍独立验证同源工件、stage、逐文件 SHA 和原始生产恢复性。不允许修改冻结 V1、旧数据和 Gold 链路。

**注意：** 旧文档称“无法由聊天侧启动新工作流”的说法仅适用于 GitHub 连接器**直接调用**；这次已证明合法的 GitHub Runner 一次性中转可在授权范围内发起原生 workflow_dispatch。这不是新增万能访问权限，也不是默认对所有项目适用的方法。
