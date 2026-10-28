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
