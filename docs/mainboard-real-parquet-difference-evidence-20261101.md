# A股主板 Task01 / Gold 真实 Parquet 行级审计｜2026-11-01

**状态：已取得可复核的真实只读数据差异；仅作历史诊断，不是生产影子验收。禁止切换与删除。**

## 实验来源及防误用

- GitHub 一次性只读 Runner [#41259469029](https://github.com/magangwwd/ashare-research-infra/actions/runs/41259469029) 已完成成功，工件 `#13064467426`。首次确认同一已签名的正式文件集合，实际读取数据页并逐行比较；该次报告包含未归类差异。
- GitHub 字段级只读 Runner [#41259711886](https://github.com/magangwwd/ashare-research-infra/actions/runs/41259711886) 已成功，工件 `#13064168596`，ZIP SHA256 `91d01177702034f770242f01f56c367bb066ec0a1443a1c1dc759538ee9a6bf5`。
- GitHub 舍入规则只读 Runner [#41259953483](https://github.com/magangwwd/ashare-research-infra/actions/runs/41259953483) 已成功，工件 `#13064303876`，ZIP SHA256 `1258f05ec299f2dc83065e1b4805e565f42f42d3ed402a1b57db9d715a7a6d77`，输出 `mainboard_real_parquet_diff.json`。
- 三次成功运行均**只通过 Google Drive API GET 读取**正式文件，并以当前已知SHA256锁定；没有Drive上传/删除/正式指针写入。每个独立分支的工作流临时 `push` 均已禁用：v2 `c346a4c9e541580f7b9e36154652b4d12d47b880`、字段归因 `dc23b0b0df887d5e655aa5faccee595e29f4e198`、半元细化 `60357d6a0e3a052e8cd3eab1be1258094bca2b9e`。
- 旧失败只读 Runner [#41258910938](https://github.com/magangwwd/ashare-research-infra/actions/runs/41258910938) 状态 `failure`；其安全错误汇总不能确定根因。后续 v2 已成功，并不等于已查明旧失败原因。

## 源数据与比较边界

| 来源 | 文件 | Drive 文件 ID | 证据 |
|---|---|---|---|
| Task01 正式指针 | `Ashare_Quant/current.json` | `1Z51dXKxxt2J_4jtcW7NcSqsNt3V3qMdB` | `snapshot_as_of=2026-10-24`; 历史完整性 false |
| Task01 正式清单 | `production_manifest.json` | `1RzoGA4-RqdqQQSzmaE6M3wkiSh9-GVG1` | SHA256 `118e6afb6febd3993656afce9b46452809e5a05bf412db7c87515a891bc3e9e8`，与指针相符 |
| Task01 2026年10月 clean daily | `data/clean/daily/year=2026/month=10/part-000.parquet` | `11J4PMUpBHn-wK5bjXG7mckYbssd9cHwx` | SHA256 `22c1fa25caba35d49fa662f03ffc83f05572039672ecf1250df112dca9990858`，匹配 Task01 正式清单 |
| Gold 正式清单 | `main_board/manifest.json` | `1wO2UFU4-kQsujMLo9G8rQLoUIeSRaCqn` | `as_of_date=2026-10-30`，顶层主键/OHLC 校验标志 true |
| Gold 2026年10月日线 | `daily_prices/year=2026/month=10/data.parquet` | `1OcZ9Jvg_0MtzccR4n80vidu0S7b2Bsvu` | 实际下载SHA256 `e084260b2d6829f3e0f439a877a25b94b866fa480dc05e8e057c444b9db23cca`，**Gold顶层manifest不提供历史月分区SHA** |

**日期门禁：** Task01 正式数据截至 `2026-10-24`，Gold 当前正式数据截至 `2026-10-30`；使用两者较早的截止日，实际数据重合日期为2026-10-08至2026-10-23的 **12个交易日**。Gold较新日期的15,948条日线记录从历史对比中剔除。

## 真实行级结果

| 比较项 | 结果 |
|---|---:|
| Task01 10月原始 clean daily 行数 | 66,775 |
| Gold 10月原始 daily_prices 行数 | 54,213 |
| 双方共同 cutoff 及 symbol/date 主键 | **38,265** |
| Gold有而Task01无的主键 | **0** |
| Task01有而Gold无的主键 | 28,510 |
| 开/高/低/收盘价不一致 | **0 / 0 / 0 / 0** |
| 成交量（股）不一致 | **0** |
| 成交额（元）严格数值不一致 | **13,002** |
| 成交额差异绝对值不超过0.50元 | **13,002 / 13,002** |
| 成交额差异绝对值超过0.50元 | **0** |
| 符合 `ROUND_HALF_UP` 整数元约定的差异 | 12,836 |
| 符合 `ROUND_HALF_EVEN` 整数元约定的差异 | 12,998 |
| 在0.50元以内但不符合所述 `ROUND_HALF_EVEN` 的差异 | 4 |
| 恰好差0.50元的差异 | 329 |

**解释**：主板共同记录的价格和成交量已在本次读到的数据页逐字段完全一致。所有成交额数值差异均不超过0.50元，呈现单位精度/舍入特征；但Gold正式历史分区的数据来源与编码规则未逐项独立认证，因此这只是数值诊断，**不是已签发 Gold 的生产契约或换源许可**。4个不符合所试单一舍入规则的记录需保留观察/追溯。

**额外行的代码组**：`300.SZ=11222, 301.SZ=5668, 302.SZ=12, 688.SH=7392, 689.SH=12, 920.BJ=4204`，合计28,510。这反映 Task01 包含更宽的证券范围；不能据此认定 Gold 缺失主板交易日，更不能跳过历史时点股票池校验。

## 已提交的适配器纠偏（仍是研究）

原 `gold_shadow_daily_adapter.py` 的 `_exact_units(amount,1000)` 在真实部分日线上遇到小数元时会报错，导致影子候选转换无法完成。新增 **`_shadow_turnover_cny`** 仅在未验证候选中采用显式 `DECIMAL_HALF_EVEN_UNVERIFIED` 整元转换、记录小数精度丢失行数 `turnover_cny_precision_loss_rows`，并保留 `turnover_cny_rounding_contract_verified_against_gold=false`、`quality_validation_complete=false`、`production_switch_allowed=false`。代码提交 `7b7f0c8a71db1c3dd204ab157dba6f58a32cd6f2`，新增5项单测 `65eb2005d48d2848de9709aec3b3beef7cc46400`；**实际 CI 结果须另外确认**。没有修改正式Gold或Task01。

## 仍未完成的上线门禁

1. Task01 最新运行 #41234129775 的发布终态、同源checkpoint/暂存完整SHA、云盘正式 pointer 更新；后续 #41248659151 的并发/队列状态。
2. Task01 `critical_history_complete` 为 false，历史数据范围未完整达标。
3. Gold **历史月分区 SHA 正式签名**、逐证券历史主板 PIT 成员身份、退市与停复牌/复权、估值/财务字段。
4. **连续5个有效交易日**影子数据对比、冻结 V1 选股/排名一致性，以及失败后可回滚演练。
5. 全部通过前：**不得切生产、不得删除旧数据、不得停止两侧原下载、不得关闭整合自动任务**。

本文件为仓库内部操作留证；明细数值请以所列 GitHub artifact JSON 为权威收据。