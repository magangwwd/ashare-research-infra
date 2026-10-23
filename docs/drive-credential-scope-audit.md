# Google Drive Secrets 权限矩阵与实测记录

本文件区分 **仓库代码引用**、**GitHub Secrets 实际存在性**、**认证成功** 和 **实际目录读写**。不得把 rclone 根目录可列出等同于全盘访问，也不得把 ChatGPT Drive connector 的权限等同于 GitHub Actions 权限。

## 已核实的工作流引用（2026-10-17）
| Secret 名称 | 使用者 | 预期用途 | 实际权限结论 |
|---|---|---|---|
| GDRIVE_RCLONE_CONFIG_B64 | astock-quant-drive-first.yml、ai-research-data-only.yml | rclone 的 gdrive: 远端 | 待 GitHub Runner 实测 |
| GDRIVE_OAUTH_JSON | task01.yml、data_pipeline.drive_auth / drive_publish / drive_restore | Task 01 OAuth API | 待 GitHub Runner 实测 |
| TUSHARE_TOKEN | task01.yml、股票数据任务 | Tushare 数据接口，不是 Drive 凭据 | 不适用 |

以上为代码引用的 **3 个 Secret 名称**，不代表 Secrets 设置页实际只有 3 个。GitHub 不提供读取 Secret 明文的能力；本工具也不能直接列出仓库 Secret 元数据。

## 手动执行
1. 合并含 `.github/workflows/drive-credential-scope-probe.yml` 的 PR。
2. GitHub → Actions → **Drive credential scope probe (manual only)** → Run workflow。
3. 第一次保持 `write_probe=false`；下载 `drive-credential-scope-report`。
4. 确认两个目标目录可读取后，第二次可选 `write_probe=true`，仅在目标目录下的 `_access_probe` 子目录尝试一次唯一临时文件上传、读回和删除。若目录不存在，rclone copyto 可能创建该测试目录；不触碰现有生产文件。
5. 查看 JSON：`rclone.scopes.*.read_listing` 和 `write_delete`。任何失败均不能宣称通过。若显示 `FAIL_OR_CLEANUP_REQUIRED`，检查并清理相应测试文件。
6. `GDRIVE_OAUTH_JSON` 目前只测试是否配置且 JSON 可解析；需使用 Task 01 的 `verify_drive_auth` 手动工作流验证其授权目标目录。该流程不等于全盘读写测试。

## 验收边界
- **全盘读写**：必须覆盖 My Drive 与所需共享盘/共享目录的授权边界，抽样测试不能证明整个账号所有文件可访问；当前统一记为 **未证明**。
- **Ashare_Quant 指定目录**：需要实际读取其文件，并在专用测试目录上传、读回、删除才算该目录读写通过。
- **stock_state_strategy 指定目录**：同上。
- **Tushare**：不是 Google Drive 密钥，不应纳入云盘权限判断。
- 工作流不会打印凭据、OAuth token 或完整错误响应；报告不含文件正文。
- 不更改 Task 01、现有财务库、任何冻结 V1 策略或现有 Secret。
