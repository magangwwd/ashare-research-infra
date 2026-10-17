# BTC 纯波动策略：风险保护增量实验真实结果（2026-10-17）

## 工程验收和结论

- [GitHub Actions真实运行 #38994013829](https://github.com/magangwwd/ashare-research-infra/actions/runs/38994013829)：BTC完整单元测试 SUCCESS、历史下载和校验 SUCCESS、VOL风险研究 SUCCESS、Artifact SUCCESS、既有 Google Drive immutable copy + rclone check SUCCESS。
- 源代码合并 PR [#34](https://github.com/magangwwd/ashare-research-infra/pull/34)，合并提交 `d7d5de3a1316407d5d0316275f441f0422754a18`。
- 研究数据截至 **2026-10-16 UTC**，测试为 **2022-06-29—2026-10-16**，18折，真实Binance Spot BTCUSDT官方完整日线，源输入SHA256 `008c582c887218c625ccae7d99a3a21932eef14c368dcba8eae01694c7da92e3`。这里**不是实时价格预测，也不是真正尚未出现的独立前向样本**。
- 所有实验独立于冻结 `V3` 前向账户、冻结配置、A股任务；资金1000 USDT、现货多头、无杠杆、信号完成日收盘→下一日历史开盘模拟成交。
- **基准逐日复现完全通过**：1×与2×的VOL_BASE新引擎相对上一阶段VOL_STATIC引擎，1571个测试日中的**最大单日净值绝对差=0 USDT**，且每个成本情景均26笔成交。
- 归档路径：现有 Google Drive `BTC_Quant_System_OKX` 根目录（ID `1f1QpjccD0YvciS7RwaxfRGkuW-o0k_0P`），本次使用 `btc_router_2026-10-16__run_38994013829__VOL_RISK__*` 追加，不新建目录、不覆盖历史。
- **研究结论：所有6个风险保护候选均未同时满足预登记85%收益留存＋最大回撤相对下降25%的双轴门槛（1×、2×成本均须通过）。不晋级。**

## 主结果（累计净收益/最大回撤，两种成本）

| 版本 | 1×净收益 | 1×最大DD | 2×净收益 | 2×最大DD |
|---|---:|---:|---:|---:|
| VOL_BASE基准 | +50.98% | 13.38% | +50.56% | 13.41% |
| VOL_CAP35（35%仓位上限） | +51.72% | 13.32% | +52.21% | 12.82% |
| VOL_SCALE75（目标仓位固定乘0.75） | +38.54% | 11.05% | +38.28% | 11.08% |
| VOL_SHOCK20（20/60日波动比≥1.5周一减半） | +51.48% | 12.59% | +51.00% | 12.63% |
| VOL_DD5（完成收盘DD≥5%，下次开盘持仓限制25%） | +47.40% | 11.76% | +47.03% | 11.78% |
| VOL_COMBO（SHOCK20+DD5） | +46.90% | 11.76% | +46.49% | 11.78% |
| VOL_TRAIN_RISK_MATCH（训练期风险近似配平） | +29.54% | 11.73% | +29.34% | 11.76% |
| V3_STATIC历史对照 | +29.54% | 8.03% | +28.37% | 8.26% |

同本轮测试区间的1倍成本下，DD5相对BASE **保留92.98%净收益、最大回撤相对下降12.17%**。SHOCK20相对BASE **保留100.98%净收益、最大回撤相对下降5.90%**。这些比率不代表统计显著性。

## 逐折稳健性和风险保护事件

- 与同折纯波动基准相比，1×成本下：VOL_CAP35收益更高 **5/18折**，VOL_SHOCK20 **7/18折**，VOL_DD5 **5/18折**，VOL_COMBO **4/18折**。因此不存在“多数时间击败基准”的稳健证据。
- DD5风险保护的激活日期（事件日志所记录的已完成UTC收盘）：2022-11-09、2023-08-18、2024-07-04、2024-08-05、2024-09-05、2025-03-09、2025-11-04，共7次。恢复条件为仅周一且最新完成日账户DD≤3%。
- 最差回撤日期两者均为 **2026-06-30**；BASE峰谷最大DD约13.38%，DD5约11.76%。需进一步区分保护规则对极端损失的效果与长期缩小风险暴露的效果。DD5研究期平均BTC占比约19.31%，BASE约20.26%，不能将更低回撤自动归结于更精准的市场转折预测。
- 固定减少25%仓位使损失下降，但只保留约75.6%的净利润，直接未过收益保留门槛；训练期风险比例版本也仅保留58%的利润。说明“少买币风险自然变小”并非有效的风险管理突破。
- 1×和2×摩擦均已计算；2×成本下DD5保留约93%的基准净利润，最大回撤降低约12.2%，依然未达25%。

## 解释和下一轮约束

最接近“有一定减风险价值”的是DD5，最接近“保持原收益”的是SHOCK20；没有通过晋级。不得在已看过的2022—2026结果上反复更换5%、3%、1.5等阈值只报最好版本。真要研究增量价值，应首先分解2026-06-30最大回撤前后的**真实仓位路径**、全年和逐折超额收益及贡献，再采用训练期选参数、对测试期冻结的完整嵌套Walk-Forward，并在2026-10-17之后逐日积累独立影子前向记录。

同时需补入风险匹配的静态仓位缩放基准、异常事件分层及不确定性区间；针对高频止损或跳价，日线代理不能给出盘中风险保障。现在所有结果只允许保留研究性质，`NO_PROMOTION`；原冻结V3独立运行不变。

## 真实数据文件

- [完整机器结果 JSON](https://drive.google.com/file/d/1vyhex9-XjCrJurRq7TthHomB4cu72Xmq/view)
- [18折全部候选指标 CSV](https://drive.google.com/file/d/1izMwC2q_LiwPjiAAf9_VcQidwMXDVZWO/view)
- [DD5事件日志](https://drive.google.com/file/d/1ci__pCzR42E1-v1bnv3Bfhyk69wM9AQp/view)
- [BASE净值历史](https://drive.google.com/file/d/1m2_yIQ421NmNEn1cUA7m4d2d22ETSgVR/view)
- [DD5净值历史](https://drive.google.com/file/d/1x0VegY4B1sLhiGO2q8pIgSjYGHC7QgHz/view)
- [训练期风险比例](https://drive.google.com/file/d/1oUZawAHwd50nZO4preoKBKrrW6OFAri8/view)
- [SHA256归档校验清单](https://drive.google.com/file/d/16myjnMq1Nv2dvXaVFT3toRqCQoJpiEUM/view)

验证动作和数据路径见 [预登记研究协议](VOL_RISK_STUDY_PROTOCOL_20261017.md)。
