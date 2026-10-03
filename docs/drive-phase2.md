# Google Drive 快照发布

用户已完成 [Runner OAuth 授权](drive-auth.md)。`drive_publish.py` 提供手动快照发布：恢复最新 checkpoint 中最后一个已验收正式版本，按该版本的 `as_of` 重新执行 Critical 总验收，再上传。不会把未完成的暂存数据上传为正式数据，不发起新的 Tushare 下载，也不宣称全量历史完成。实际发布结果以 Actions 成功记录和 Drive `current.json` 为准。

仓库 Actions → Task 01 data infrastructure → Run workflow：仅勾选 `publish_drive_snapshot`，保留 `verify_drive_auth` 为 false。`GDRIVE_OAUTH_JSON` 已在 Secrets 配置，不再需要交出凭据。两个选项同时勾选时只执行发布任务；未勾选仍执行原数据任务。每日定时任务暂不自动上传 Drive。

目标目录：

```text
Ashare_Quant/
  current.json
  staging/                         # 失败的上传留在这里，消费者不得读取
  versions/<version>/              # 不可变的已核对快照
    data/raw/
    data/clean/
    data/acceptance.json
    metadata/manifest/
    metadata/schemas/
    metadata/capabilities/
    metadata/acceptance.json
    metadata/history_progress.json
    metadata/history_progress.md
    docs/datasets/
    production_manifest.json
```

正式发布顺序：恢复本地正式版本 → 重新 Critical 验收 → 上传独立 `staging/version` → 列举全部远程路径、核对文件 ID 和大小、下载每个文件重算 SHA256 → 上传并回读核对 `production_manifest.json` → 将完整版本移入 `versions` → 最后一次写入/回读 `current.json`。成功后 staging 中该版本因移动而消失。

不会覆盖旧版本数据。上传和验证失败不会修改旧指针；移动后、指针切换前失败可能留下未被引用的版本，消费者仍只读 `current.json` 指向的版本。指针更新响应丢失时回读确认实际结果，不把已成功切换误报为失败或回滚。HTTP 和 OAuth 异常不会输出凭据。

Task 02 先读取 `current.json`，验证 `validated: true`，再按 `manifest_file_id` 获取 Manifest，核对其 SHA256；按 Manifest 中相对路径与 `file_id` 读取 Hive Parquet。不能只取最新文件夹或把 `staging` 当正式数据。

`production_manifest.json` 是本次实际正式文件的清单，包含路径、大小、SHA256 和 Drive ID；checkpoint 内原 `metadata/manifest` 仍是下载工作状态，可能包含失败暂存的 Gap/请求，不能代替正式文件清单。`critical_history_complete`、`full_requested_core_range_complete` 和 `extensions_history_complete` 保留真实状态，`false/null` 不影响已存数据通过质量验收后发布。

当前每次发布上传完整快照，无去重、远程增量恢复或版本自动删除；可恢复下载继续依赖原 GitHub checkpoint。Drive 老版本、Runner 本地恢复状态和 GitHub Artifact 都不会被此任务主动删除。GitHub 托管 Runner 在任务结束后自动销毁；上传 Drive 不会自动释放 Artifact 占用。先完成实际首次发布验证，再决定自动上传和保留策略。

凭据只从环境变量读取，不写入代码或 YAML。核心数据集随版本上传 README 和 Schema；来源、主键、字段、分区、频率、清洗规则和注意事项见 `docs/datasets/` 和 `metadata/schemas/`。

必需故障测试：上传中断、漏文件、checksum 错误、remote validation 失败和指针切换失败。每种失败都检查旧 Production 数据、Manifest 和 Current Pointer 完全不变；成功后只能通过新 Pointer 读取已验收版本。
