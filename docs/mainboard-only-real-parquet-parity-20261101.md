# 沪深A股主板范围的真实日线只读核验（2026-11-01）

**结论：MAINBOARD_ONLY_PARQUET_DIAGNOSTIC_PASSED_FOR_KEY_COVERAGE；生产切换**仍**被阻断。** 根据用户明确要求，本整合以后只处理沪深A股主板证券。旧 Task01 全市场 Raw/Clean 文件保留，不删除、不改下载。非主板证券不参与正式主板数据差异与冻结策略的评价。

## 范围依据和固定来源

- Gold 正式 `manifest.json`（Drive ID `1wO2UFU4-kQsujMLo9G8rQLoUIeSRaCqn`），`as_of_date=2026-10-30`，`validation.primary_keys_unique/ohlc_valid=true`。
- **官方签名主板名录**：Gold `instruments.parquet`（ID `1KyHYGngvkUIkqisQFO5qZCTeFxzzBO0q`）SHA256 `6e06b2f17c6d46384df35b15c558f2fb1c9fe845e0cc7f608eec95d5e0789586` 与 Gold manifest 完全一致，共 3,486只历史/在册证券。逐条要求 `board=MAIN_BOARD`、`exchange=SSE/SZSE`、`currency=CNY`、ID唯一；不靠代码前缀推断正式资格。
- Task01 正式 `current.json`（ID `1Z51dXKxxt2J_4jtcW7NcSqsNt3V3qMdB`）`snapshot_as_of=2026-10-24`，正式 manifest SHA 匹配。实际读取其 `data/clean/daily/year=2026/month=10/part-000.parquet`（Drive ID `11J4PMUpBHn-wK5bjXG7mckYbssd9cHwx`），正式清单 SHA 匹配。
- Gold `daily_prices/year=2026/month=10/data.parquet`（Drive ID `1OcZ9Jvg_0MtzccR4n80vidu0S7b2Bsvu`）实际字节 SHA `e084260b2d6829f3e0f439a877a25b94b866fa480dc05e8e057c444b9db23cca`；但 Gold 顶层 manifest **没有**该历史月文件的 SHA 签名，未作为生产级历史分区验收。

## 只读 GitHub 运行和真实报告

- 代码草稿 [PR #149](https://github.com/magangwwd/ashare-research-infra/pull/149)：`tools/mainboard_real_parquet_shadow.py` 的 `gold_instruments_path` 现为显式必需输入，缺名录或 SHA 不对则拒绝；`data_pipeline/gold_shadow_drive_probe.py` 固定6个 Drive 只读文件。
- CI [#41260847596](https://github.com/magangwwd/ashare-research-infra/actions/runs/41260847596) `completed/success`，291 passed / 2 skipped / 9 subtests passed。代码提交 `1cdbc53382e9e8665802a612cfba2975c1a98f6a`、`3705b7d26588d5afd4e14334704774796dd09155`、`8ecd25ea085b2304a41fa2981327456538c81904`、`259dc89b62b1c73db880536fd17bf4d628f4b882`、`7e728ceb4350bf5bc41942be8017c5f7e3192631`、修复测试 `dc58eccf2c340170ab2207342b1e9ea2019a4a95`。
- 一次性受控只读 Runner [#41260957019](https://github.com/magangwwd/ashare-research-infra/actions/runs/41260957019) `completed/success`，已通过六份固定文件 SHA、正式指针二次复验、真实 Parquet 数据页比较。报告 artifact `mainboard-official-universe-parity` **#13064962449**（ZIP digest `sha256:682784ce8a92a08f7119c22d7d48015083654372e200bdcd1e7907878fd7be23`）。
- 隔离工作流 `.github/workflows/mainboard-only-readonly-20261101.yml` 位于 `ops/one-shot-mainboard-only-20261101`；只在 `9aa53a931ce1a402a985cad0e5655bc32b1fe070` 发起一次 push 后立即以 `9d8a797e1b238b68c7a2c163fb2c8a9082ae40b1` **禁用 push 触发**。无生产发布、无调度器、没有写 Drive。

| 2026-10月分区只读检查（共同截止日2026-10-24） | 真实结果 |
|---|---:|
| Gold 已验证主板名录证券数 | 3,486 |
| Task01 原始月份行数（含其他板块） | 66,775 |
| 由官方主板名录排除的非主板历史行 | 28,510 |
| 余下双方共同 `(证券,日期)` 键 | 38,265 |
| 仅 Task01 有的**主板**键 | **0** |
| 仅 Gold 有的**主板**键 | **0** |
| 主板开/高/低/收价格不一致 | **0** |
| 主板成交量不一致 | **0** |
| 主板成交额严格数值差 | 13,002 |
| 成交额差异中绝对误差超过 0.5元 | **0** |
| 成交额差异中符合 ROUND_HALF_EVEN 整元结果 | 12,998 |
| 成交额差异中不符合上述规则 | 4 |

排除记录的代码组：`300.SZ=11222,301.SZ=5668,302.SZ=12,688.SH=7392,689.SH=12,920.BJ=4204`。这些记录不是 Gold 主板缺失，后续不再需要为它们做主板补齐/失败诊断。成交额的4处不符合半数取偶舍入，但每处差异均不超过0.5元；**仍需查明具体数值转换或浮点规则，不能未经证据放松严格验收。**


## 第二次精度复核：13,002项均符合 Gold 旧浮点换算（2026-11-01）

- Gold 源脚本 `backfill_main_board_history.py` 对成交额的原路径为 `(pd.to_numeric(values) * 1000).round().astype("Int64")`：先按 `float64` 浮点运算乘1000，再用 pandas/NumPy 整元舍入，**不是直接对 Task01 十进制元金额调用 Decimal ROUND_HALF_EVEN**。
- 上一轮真实 run [#41261404901](https://github.com/magangwwd/ashare-research-infra/actions/runs/41261404901)、artifact `#13065516697` 提取唯一4条 `Decimal ROUND_HALF_EVEN` 未解释的成交额记录，均为恰好0.5元。具体 `vendor_symbol/trade_date/Task01(元)→Gold(元)`：
  - `000698.SZ/2026-10-21/8321273.5000→8321273`
  - `002293.SZ/2026-10-14/66806632.5000→66806633`
  - `002422.SZ/2026-10-16/1050605834.5000→1050605835`
  - `002695.SZ/2026-10-08/133873966.5000→133873967`
- PR #149 增加受限**诊断性** `reproduce_legacy_gold_turnover_cny()`（`35454404b8444b3f801575c965be13db1963af98`）以及4个真实金额和不放宽原始差异的回归测试（`495fe91068506ce60c7a66adb91ffeaaae69c829`）。[CI #41261572708](https://github.com/magangwwd/ashare-research-infra/actions/runs/41261572708) `completed/success`，**294 passed / 2 skipped / 9 subtests**。
- 独立受控**真实主板只读** Runner [#41261678723](https://github.com/magangwwd/ashare-research-infra/actions/runs/41261678723) `completed/success`；工件 `mainboard-float-parity-report` `#13065377643`，GitHub artifact ZIP `sha256:7a9962dd51f14ed029392fe7bf21e177992ed79ffe6d1ccde26fb91f480c4f74`。
- **实际统计**：主板共同键 `38265`，OHLC和成交量差 `0`，严格成交额差 `13002`；`turnover_legacy_float_rule_compatible_differences=13002`，`turnover_legacy_float_rule_incompatible_count=0`。因此当前样本所有成交额数值差均由原Gold浮点换算和舍入复现；但**仍保留严格原始差异计数，绝不当作生产完全相等**。历史PIT、其他字段、5交易日冻结V1重放与回滚仍未验收。
- 唯一隔离触发分支 `ops/one-shot-mainboard-float-parity-20261101` 在生成run后已通过 `c969b492d6eddb1ec502b8c7a071465d4c2adcdf` 禁用 `push`；没有创建 schedule、没有 Drive 上传、没有修改冻结V1。


## 第三次实证：上市／退市日窗口一致性（2026-11-01）

- Gold 官方 SHA 校验的 `instruments.parquet` 包含 `list_date` 和 `delist_date`，此次对比扩展了逐证券上市／退市日期窗口；缺日期不作默认通过，退市之后行情不直接视为正常主板记录。
- 代码 `bf5de0b2a15d0c9638f897d30d2a4248d9243278` 与5项测试 `b18095087afeac5e016c554a25d25c415b10adac`，CI [#41261860421](https://github.com/magangwwd/ashare-research-infra/actions/runs/41261860421) `completed/success`，**299 passed、2 skipped、9 subtests**。
- 受限一次性只读真实运行 [#41261928673](https://github.com/magangwwd/ashare-research-infra/actions/runs/41261928673) `completed/success`，artifact `mainboard-listing-window-report` **#13065647309**（ZIP digest `sha256:6488b574be851e1308c802a1479f9e381dd8b0aae9c487d8549ef7e189445982`），6份云盘输入固定SHA检查通过。
- **实际结果**：Gold当前认证主板证券数 `3486`，**缺上市日期证券0**；Task01、Gold各自按日期窗口检查的行，**未知上市日期0**、**上市前或退市后行情0**。此前共同主板键 `38265`、无主板键差/开高低收或成交量差、成交额旧Gold浮点转换全部复现的结论保持不变。
- 分支 `ops/one-shot-mainboard-listing-window-20261101` 在唯一run创建后 commit `b385dd0b81884e9fb836ea30b626eaf4e66b4f70` 立即关闭 `push`。无第二次调用、无生产Drive写入。
- **限制**：当前Gold名录的上市/退市日期窗口通过不能独立证明历史 `market=主板` 随时间的实际板块归属、转板处理、停复牌、复权、财务与冻结V1重放。仍 `gold_historical_pit_universe_verified=false`、`production_switch_allowed=false`，尚未完成5个交易日真实前瞻影子和回滚。

## 保留闸门

- 使用的是 Gold 在 `2026-10-30` 的正式名录，对 10月8日至23日历史证券范围的逐日**point-in-time归属仍未独立验收**。对板块变动、退市、停牌等事件需时点复核；不能声称所有历史日均无差异。
- 尚未做 15min/财务/复权因子/daily_basic/停复牌/涨跌停等正式全量一致性，也未完成连续5个交易日的冻结V1影子策略、回滚演练。
- Task01 正式数据仍停在 `2026-10-24`，历史完整性 false，旧上传 [#41234129775](https://github.com/magangwwd/ashare-research-infra/actions/runs/41234129775) 至此检查还处于 `in_progress`；后续任务 [#41248659151](https://github.com/magangwwd/ashare-research-infra/actions/runs/41248659151) `pending`。不启动重复上传，不切正式 Gold、不删除旧数据或修改冻结 V1。

