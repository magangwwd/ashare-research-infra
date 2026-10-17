# BTC 降风险「禁加仓＋分阶段恢复」部署验收（2026-10-17）

## 当前状态：研究自动化已恢复并成功执行

- GitHub 仓库：`magangwwd/ashare-research-infra`；当前连接实测可创建分支/写入代码/创建并合并 PR。
- [PR #35](https://github.com/magangwwd/ashare-research-infra/pull/35) merged；主分支合并提交 `34d7d5670a1cfaf7bffc0b19b9f6308a66613e0c`。
- [PR完整BTC单元测试 run #39015331134](https://github.com/magangwwd/ashare-research-infra/actions/runs/39015331134)：SUCCESS。
- [main完整 BTC研究 run #39015361237](https://github.com/magangwwd/ashare-research-infra/actions/runs/39015361237)：`unit-tests` **SUCCESS**、`daily-shadow` **SUCCESS**，其中包括 Google Drive `rclone copy --immutable` 及 `rclone check --one-way --size-only`。
- 旧版原冻结V3、交易/实盘接口、其他仓库与A股数据任务未修改。
- Drive沿用既有 `BTC_Quant_System_OKX` 根目录，无新文件夹；输出文件前缀 `btc_router_2026-10-16__run_39015361237__VOL_REENTRY__*`，**66份**本研究输出文件已在Drive列目录核对。
- [实际回测机器报告 JSON](https://drive.google.com/file/d/1BfOS3nn5u9OtjJUxP0IJ5Iggs66itxfq/view)
- [此次主分支SHA256完整归档清单](https://drive.google.com/file/d/1leS5zPwGIOsOgeiIuS_8qLQ3zqcFJMaC/view)
- 使用2026-10-16截止的完整Binance BTCUSDT Spot UTC日线，研究期2022-06-29—2026-10-16，18折/1571日，初始1000 USDT，下一日历史开盘价代理成交，现货无杠杆。

## 历史基准逐日精确复现

本轮从真实Drive归档的 `noadd_reentry_summary.json` 回读，复核了与同一运行中旧模型结果对照：

| 检验 | 逐日NAV最大绝对差USDT | 成交笔数旧/新 | 结果 |
|---|---:|---:|---|
| VOL_BASE 1x | 0 | 26 / 26 | PASS |
| VOL_DD5 1x | 0 | 42 / 42 | PASS |
| VOL_BASE 2x | 0 | 26 / 26 | PASS |
| VOL_DD5 2x | 0 | 42 / 42 | PASS |

## 历史模型对照（百分比）

| 版本 | 净收益1x | 最大DD1x | 净收益2x | 最大DD2x |
|---|---:|---:|---:|---:|
| VOL_BASE | 50.98% | 13.38% | 50.56% | 13.41% |
| VOL_DD5 | 47.40% | 11.76% | 47.03% | 11.78% |
| VOL_NOADD_REDUCED | 49.87% | 12.41% | 49.48% | 12.43% |
| VOL_DD5_STAGE_RECOVERY | 49.80% | 11.76% | 49.42% | 11.78% |
| VOL_NOADD_STAGE_RECOVERY | 49.80% | 11.76% | 49.42% | 11.78% |
| VOL_NOADD_GLOBAL_REENTRY（危险反例） | 40.70% | 15.05% | 40.23% | 15.13% |

`VOL_NOADD_REDUCED`有6次禁止买入事件，包括2026-06-01；`VOL_DD5_STAGE_RECOVERY`在不解除8/4/15账户总风险保护的前提下改善DD5反弹参与，历史最大DD未增，但离相对降低25%的预登记门槛仍有差距。`VOL_NOADD_GLOBAL_REENTRY`触发15%永久停机，不能晋级。

**验收决策：研究链路部署完成，策略提升决策 NO_PROMOTION。**

## 曾经的「连接层拦截」根因判断

当时GitHub写入接口返回的是安全检查拦截提示，不能据此直接推断GitHub仓库被锁定、API Key失效或GitHub Actions故障。此次复查连接返回 `push:true`，分支创建、文件写入、PR合并都成功；因此可以认定**先前操作受阻已解除**。但当前没有足够的底层拦截日志证明唯一确切原因。不要因一次临时拦截建议用户重置所有密钥或开公有库。

文档只陈述真实已执行结果，独立影子前向评估仍未满足，不涉及直接交易授权。
