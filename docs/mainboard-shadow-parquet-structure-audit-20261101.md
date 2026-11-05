# Task01 → Gold：2026-10 日线真实文件结构审计（2026-11-01）

状态：**READ_ONLY / STRUCTURE_ONLY / NOT_ROW_VERIFIED / BLOCKED_FOR_SWITCH**。本文件随草稿 PR #149 保存，不修改主分支、不写 Drive、不改变冻结 V1，也不代表 5 个交易日影子验收。

## 原始来源和范围

- GitHub main：`00493bc7220536a86e7eb610801873580c7b535f`；草稿 PR #149 上一次单测 [#41257401498](https://github.com/magangwwd/ashare-research-infra/actions/runs/41257401498) `success`，236 passed / 2 skipped。
- Task01 正式 pointer：Google Drive `Ashare_Quant/current.json`，文件 ID `1Z51dXKxxt2J_4jtcW7NcSqsNt3V3qMdB`，`snapshot_as_of=2026-10-24`，`validated=true`，`critical_history_complete=false`。
- Task01 正式 manifest：文件 ID `1RzoGA4-RqdqQQSzmaE6M3wkiSh9-GVG1`；下载后 SHA256 `118e6afb6febd3993656afce9b46452809e5a05bf412db7c87515a891bc3e9e8`，与正式 pointer 中声明相符。
- Task01 **clean** 日线 2026-10：正式 manifest 内路径 `data/clean/daily/year=2026/month=10/part-000.parquet`，Drive 文件 ID `11J4PMUpBHn-wK5bjXG7mckYbssd9cHwx`。下载 `2,668,215` 字节，实际 SHA256 `22c1fa25caba35d49fa662f03ffc83f05572039672ecf1250df112dca9990858`，**与 Task01 正式 manifest 该分区记录一致**。
- Gold 正式根 `project/down_data/data/gold/main_board` 的 `manifest.json`，文件 ID `1wO2UFU4-kQsujMLo9G8rQLoUIeSRaCqn`，`as_of_date=2026-10-30`，两项核心质量标志为 true。
- Gold 月日线 `daily_prices/year=2026/month=10/data.parquet`，Drive 文件 ID `1OcZ9Jvg_0MtzccR4n80vidu0S7b2Bsvu`；下载 `1,574,012` 字节，实际 SHA256 `e084260b2d6829f3e0f439a877a25b94b866fa480dc05e8e057c444b9db23cca`。**Gold 顶层 manifest 未列该历史月分区 SHA256**；该哈希只证明本次已下载字节的指纹，不视作 Gold 正式分区验收。
- Gold `instruments.parquet` ID `1KyHYGngvkUIkqisQFO5qZCTeFxzzBO0q`、`daily_snapshot.parquet` ID `1rw_0sRymrWSr2kIeOZ5AeS0L9m9qG0eQ`：本轮下载 SHA 分别为 `6e06b2f17c6d46384df35b15c558f2fb1c9fe845e0cc7f608eec95d5e0789586`、`6eb60191b7c9f589b3de9028fe9e55ac7200daa0772846d3da61d1cf7f906f08`，**均与 Gold 顶层 manifest 一致**。

## Parquet 原始 Footer 元数据

读取已下载二进制文件的 `PAR1` 魔数、末尾 metadata 长度和 Thrift Compact FileMetaData，读取 `num_rows` 与 `SchemaElement.name`。未解码数据页、未运行真实逐行价格比较。

| 项目 | Task01 clean daily 2026-10 | Gold daily_prices 2026-10 |
| --- | ---: | ---: |
| num_rows | 66775 | 54213 |
| 数据字段数（不计 schema 根） | 13 | 16 |
| 原始字节 | 2668215 | 1574012 |

Task01 13字段：`ts_code, security_id, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount, schema_version`。

Gold 16字段：`instrument_id, vendor_symbol, trade_date, open, high, low, close, previous_close, price_change, return_pct, volume_shares, turnover_cny, currency, source_vendor, is_quality_valid, quality_flags`。

Gold instrument 名录为 3486 行、daily_snapshot 为 3190 行（两项下载 SHA 匹配 Gold manifest）。

**解释**：`66775-54213=12562` 是原始分区总行数差，不是「缺失 12562 行」。两端正式 `as_of` 先后相差6个自然日，分区来源和证券覆盖集合不同；必须先统一日期、证券身份、PIT 范围，再逐 `(股票,交易日)` 核对字段与单位。

## 已确认的未完成事项 / 生产阻断

- Task01 `critical_history_complete=false`、`full_requested_core_range_complete=false`，且与 Gold 正式日期不一致，不能切换。
- 当前 Task01 [run #41234129775](https://github.com/magangwwd/ashare-research-infra/actions/runs/41234129775) 正在 Drive 发布步骤，备份 [#41248659151](https://github.com/magangwwd/ashare-research-infra/actions/runs/41248659151) 排队，严禁重复生产上传。
- 先前 [暂存 SHA 审计 #41160259395](https://github.com/magangwwd/ashare-research-infra/actions/runs/41160259395) 为 success：预期 7104 文件，验证暂存 4350，缺 2754，缺约 822.66MB；但 source run #41119601566 **当前无同 run checkpoint**，不得直接恢复旧 stage。
- 真实 OHLCV 行级对比、daily_basic/adj_factor/PIT、停牌退市、冻结 V1 选股/排名一致性、连续 5 个交易日影子对比、回滚演练均**尚未完成**。
- 本地审计环境没有可用的 PyArrow/Fastparquet；本轮使用纯 Thrift footer 读取结构，**不能声称完成行级数据对比**。正式逐行比较须在已安装 Parquet 解码库的隔离只读环境执行，并核验文件哈希及同一截止日。

## 下一轮

1. 先复查生产 Task01 是否从 `in_progress` 转入终态；看 `task01-drive-publication-report` 是否产生，读取 Drive 正式 pointer 的新 SHA/as_of；检查源同 run checkpoint。
2. 不重复发布。若尚阻塞，独立完善 Gold `point-in-time` 映射、影子对比脚本和真实 Parquet 解码测试。
3. 先取得准确日期匹配和证券集合对齐的可机器复核差异，才进入5交易日冻结策略双跑与回滚。

审计本身**未使用 GitHub 生产凭据**、未调用生产上传、未改旧正式指针。
