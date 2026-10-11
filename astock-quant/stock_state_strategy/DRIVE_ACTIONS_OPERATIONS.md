# GitHub Actions 计算 + Google Drive 持久存储（2026-10-11）

## 架构

GitHub 只保存代码、测试、配置与策略文档。临时 Ubuntu Runner 通过 rclone 从 Drive 下载数据、运行 Python，随后将输出与运行状态复制回 Drive。不会把运行结果提交 Git，不使用 Actions artifact，不运行 rclone sync 或 delete。

- 唯一正式日频行情库：Drive 下 down_data/data/gold/main_board/
- 策略产物：Drive 下 stock_state_strategy/output/
- 策略重建数据：Drive 下 stock_state_strategy/runtime/data/
- 历史只读输入：Drive 下 stock_state_strategy/artifacts/legacy/
- 运行状态报告：Drive 下 stock_state_strategy/runs/
- 旧仓库数据备份：Drive 下 astock_quant_repo_backup/

正式策略仍为 LOCK_MARKUP_SINGLE_PAPER_V1 / PAPER_ONLY_NO_ENTRY_AUTHORIZATION。不会连接券商下单。

## 需要管理员配置的 GitHub Actions Secret

**必须先由用户本人完成 OAuth 授权。** 当前 GitHub 连接不支持设置 Actions Secret，因此本次创建的运行流程尚未获得 Drive 写权限。

使用 rclone config 配置名为 gdrive 的 Google Drive remote。remote 的 root_folder_id 必须是现有 project 父文件夹的 ID：

1eI1oqhndalitKtS3zrU449cta0TTQGY_

该文件夹下同时存在原 Google Drive 文件夹 stock_state_strategy 和 down_data。工作流会读取两边的已知文件校验 remote 根目录，避免误写到 My Drive 其他位置。

在本人的可信电脑上完成 rclone OAuth，然后将本地 rclone.conf 的完整内容 base64 编码，保存到 GitHub 仓库 Settings → Secrets and variables → Actions 的 GDRIVE_RCLONE_CONFIG_B64。该配置包含敏感 token，不得贴到聊天或提交 Git。需要 Tushare 下载时再设 TUSHARE_TOKEN Secret。过期 OAuth token 应更新该 Secret。

## 手动运行 GitHub 工作流

进入仓库 Actions → Stock strategy Drive-first runner → Run workflow。

| task | 做什么 | 数据持久位置 |
|---|---|---|
| tests | 运行原有离线测试，不需要 Drive | 不输出持久数据 |
| preflight | 下载 Gold 并验证结构及截至日期 | 读取 Gold，写 runs |
| current-state | 读取 Gold 和原历史股东处理数据，生成四阶段状态 | output/state_definition_YYYYMMDD |
| forward-baseline | 读取冻结历史依赖，重放 2026-08-18 基准 | output/forward_20260818 |
| ownership-refresh | 下载和处理 Tushare 股东披露 | runtime/data |
| gold-refresh | 执行原 down_data 的下载、构建、历史与状态更新 | down_data/data |
| backup-repo-data | 备份旧 Git 数据并执行 rclone 校验，**不删除源文件** | astock_quant_repo_backup |

Gold 更新直接读取 Drive down_data 目录现有的四个 .py 下载与回填脚本，避免在 GitHub 再维护第二套实现。它只负责日频，不包括尚未落地的 15 分钟主库。

as_of 参数可选 auto（读取 Gold manifest 的日期），min_as_of 可设置要求的最低交易日。数据不够新时任务失败而非伪造信号。对股票状态研究而言，仅更新行情并不代表股东公告已足够新；自动日报和实盘提醒必须另行通过数据时点验收。

**日频调度已添加（2026-10-11）**：GitHub Actions 在每天北京时间/新加坡时间 **23:50**（UTC 15:50）自动触发。\
`astock-quant-drive-first.yml` 将依次执行 `gold-refresh` 和 `current-state --as-of auto`，所有产物继续写入 Drive；`task01.yml` 同时执行独立的 Tushare Raw/Clean 财务、估值与行情增量流水线，正式发布到 Drive 的 `Ashare_Quant` 数据目录。两套数据存储隔离，严禁将 Task 01 的未验证数据直接混入冻结V1。GitHub定时任务可能延迟启动。\

**重要前置条件**：需确认 Secrets `GDRIVE_RCLONE_CONFIG_B64`、`TUSHARE_TOKEN`、`GDRIVE_OAUTH_JSON` 有效并完成一次真实端到端验证；仅有 cron 配置不表示更新已经成功。财务清洗数据当前属于研究数据源，不会自动成为 LOCK_MARKUP_SINGLE_PAPER_V1 的筛选条件，任何因子接入须另行审计并获得新的策略版本。

## 旧 Git 数据清理

仓库 daily_data 中的约 2,775 个文件、407 MB CSV 仍供 LightGBM/Kronos 等原有项目使用；其他 market_meta、financial_data 和模型 output 也可能有依赖。务必先执行 backup-repo-data 并核实备份，再更改旧项目读取路径和通过测试，才能在第二个独立 PR 中删除这些数据。

本阶段只删除无业务价值的已提交 Python __pycache__/*.pyc 文件。不删除旧系统的数据、回测报告和结果。

请注意：Git 删除 tracked CSV 后，旧的历史提交依然保存大文件，不会自动缩小既有仓库的全部 Git 历史。需要真正压缩历史必须另行规划 git-filter-repo、备份和强制更新所有克隆，当前不做历史重写。

## 可靠性限制

- 当前暂未在 GitHub 上使用真实 Drive Secret 执行或验证上传；工作流需用户配置授权后运行。
- GitHub Runner 使用临时 SSD 下载约 1.25 GB Gold（日线），受免费空间、网络、Google API配额和最长运行时间限制。任务结束后临时文件不会以 Git 提交保留。
- Gold 上传先复制数据文件，最后发布验证 manifest/summary；多文件上传不构成严格数据库级原子事务。运行失败时报告错误，不宣称数据已最新。
- GitHub Actions 不上传 artifact，但 GitHub 平台仍保留一定期限的运行日志。
- 这是离线批处理系统，不是常驻服务。30秒级持仓监控不应该依赖 GitHub 托管的 Actions Runner。
