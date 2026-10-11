# 数据存储边界与旧模型运行

2026-10-11 清理了Git追踪中的旧市场指标、财务CSV、模型预测、回测报告与本地编辑器运行状态。本项目代码均仍在Git。**用户确认原始数据均已在Google Drive备份**；本次仅删除Git中的版本，未修改云端备份。

## 强制规则

1. 正式 `stock_state_strategy` 使用现有 GitHub Actions `Stock strategy Drive-first runner` 和 `stock_state_strategy/github_drive_runner.py`。Tushare Gold、股东披露、状态池和报告的唯一持久层是Google Drive。
2. 旧 LightGBM/Kronos/ROE-FCF/财务研究使用 GitHub Actions `Legacy models - Google Drive only`，它会在临时runner上恢复旧CSV、运行代码，再把结果同步到Drive。旧模块使用历史Yahoo CSV，与正式Tushare Gold策略严格隔离。
3. 两个工作流都只有 `contents: read` 权限，且 `actions/checkout` 禁止持久化Git凭据。运行过程可能在临时runner磁盘生成CSV/模型文件，但绝不提交、推送到GitHub仓库或使用Actions Artifacts长期存储。
4. 根目录 `.gitignore` 禁止新CSV、Parquet和输出目录进入Git；`stock_state_strategy/check_git_storage_policy.py` 为每个PR提供追踪文件扫描。如果需要向仓库添加用于测试的模拟数据，请用Python即时生成，不提交CSV。
5. 旧代码使用固定相对路径，但runner在执行前把Drive数据暂存到对应位置，执行后上传结果。这些临时工作区文件不会产生Git提交。直接在普通GitHub Actions中调用旧脚本而不使用上述封装**不保证结果保存**。
6. 旧模型数据默认在 `gdrive:astock_quant_repo_backup/` 下。若用户备份在其他文件夹，使用工作流的 `drive_prefix` 输入指定相对路径。必须包含 `daily_data/`、`filtered_stocks.csv`、`industry_map.csv`，否则任务失败而非返回伪结果。
7. 冻结V1仍是 `PAPER_ONLY_NO_ENTRY_AUTHORIZATION`；旧预测输出不得自动代替正式V1模拟信号。

## 首次运行说明

用户需在仓库 Secrets 中配置 `GDRIVE_RCLONE_CONFIG_B64`，并在需要Tushare的任务中配置 `TUSHARE_TOKEN`。2026-10-11 已为 Task 01 股票/财务数据流水线和 stock_state_strategy Gold/状态池流水线设置每天 UTC 15:50（UTC+8 23:50）的 GitHub Actions 调度；其他旧模型工作流仍仅手动触发。调度能否实际完成取决于有效的 GitHub Secrets、Tushare 权限、Google Drive 授权和远程验收，不代表已成功夜间实跑。由于云端凭据由用户管理，这次Git清理并不表示已验证旧模型从Drive实际重跑成功。

旧研究可运行入口：
- `market-features`、`event-features`：特征生成。
- `financial-score`：财务评分。
- `lightgbm-daily`、`lightgbm-backtest`：LightGBM预测、测试。
- `kronos-predict`、`combined-top3`：旧Kronos/组合研究（计算量较大，依赖模型权重和可用环境）。
- `roe-fcf`：ROE/FCF研究。
- `daily-report`：旧系统日更、评分和预测报告。

若旧模型缺少本地权重、授权或其他隐式依赖，作业应报错，并不应生成成功推荐。禁止为通过验收而伪造数据。
