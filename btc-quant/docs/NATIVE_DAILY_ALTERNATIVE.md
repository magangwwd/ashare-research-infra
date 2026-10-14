# BTC V3 历史数据缺口与替代回测

## 问题确认（2026-10-15）

原始 Binance Spot BTCUSDT 4h (2018-01—2026-09) 任务在严格时序验证中发现 2018-02-08 至 2018-02-09 缺口后自动中止。Binance 公共数据项目长期记录了同日期的缺失以及当时的交易中断：参见 https://github.com/binance/binance-public-data/issues/77 、https://github.com/binance/binance-public-data/issues/316 、https://github.com/binance/binance-public-data/issues/379 。不允许人为插值或用前一根收盘价虚构其间 4h OHLCV。

## 独立替代路线，不等于修好原 4h 数据

- **源**：Binance Vision BTCUSDT Spot 原始月度 **1d UTC** K 线，2020-01 至 2026-09；不是其他交易所代理报价，不混用现货和期货。
- **校验**：时间戳 UTC 00:00，日期必须逐日相连，不允许重复/异常 OHLCV；输入 ZIP 及输出 CSV 的 SHA-256 留存。如遇缺日则停止，不做插值、不造数据。
- **回测**：复用现有日线收盘信号、次日日线开盘模拟成交和全部策略代码，输出净值、交易日志及成本压力测试。日线来源不同且起点不同，不能直接与原 4h 口径或旧版业绩比较。
- **边界**：原生日线并不保证交易所全天无停市；日内故障期间不可能真实成交，“次日开盘成交”的假设仍应审查。新通道通过并不代表 4h 数据通过，也不代表策略正式验收合格。
- **文件**：`data/native_daily.py`、`tests/test_native_daily.py`、`.github/workflows/btc-v3-daily-alternative.yml`。
- **存储**：每次输出包含仓库 commit SHA、执行 run ID、源文件摘要。优先存 GitHub Actions 30 天产物；若仓库已有 Google Drive rclone 配置，追加到现有 `BTC_Quant_System_OKX/BTC_V3_第三轮回测_20261014/`，不能新建项目根目录、覆盖已有产物。
- **严格原流程**：`btc-v3-backtest.yml`、其失败告警 Issue 应保持可追溯；备用通道不能自动关闭原失败事件。

下一阶段如需长期 4h 同源研究，可继续核验官方小时/逐笔成交，若为真实停市，则只可明确分段或以原生日线口径另设研究，不得声称完成原 4h 数据修复。
