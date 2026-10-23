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
