# BTC 第三轮回测执行手册（研究版）

**身份：** 仅研究 BTCUSDT Spot 现货、多头、无杠杆。不会连接 OKX 或真实交易密钥。本项目与 astock-quant 平级，资产数据不提交公共 GitHub。

**存储：** Google Drive 仍使用现有 `My Drive/project/BTC_Quant_System_OKX/`，不新建项目根目录、不覆盖历史文件。GitHub 存代码、参数和测试；原始 ZIP、CSV、交易日志和最终结果应经受控同步存到该 Drive 项目。

## 在能访问互联网的可信 Python 环境执行

从仓库根目录开始；使用 Python 3.10+，脚本只需 Python 标准库：

```bash
python btc-quant/data/download.py --start 2018-01 --end 2026-09 --out ./btc_raw
python btc-quant/data/prepare.py --source ./btc_raw --out ./btc_validated
python btc-quant/backtest/run.py --input ./btc_validated/btc_daily_utc.csv --out ./btc_results
python -m unittest discover -s btc-quant/tests -v
```

2026-10 月尚未结束，因此第一轮先下载到 2026-09 完整月。下载脚本从 Binance 官方 Binance Vision 的 Spot 4h 月度归档获取数据；缺失、下载失败、重复、4h 缺口和非法 OHLCV 均明确报错，禁止填补或捏造。Binance Vision 2025 年前后可能采用毫秒/微秒时间戳，校验脚本可按量级识别。4h 聚合 UTC 日线需要一天恰好 6 根；边缘半天数据舍弃并留痕。月度 ZIP 本身和 SHA256 均需留存。

## 策略定义和结果

- V1：卡尔曼价格过滤器的**重新实现**，并非旧版本源码的复现。
- V2-A：55 日高点突破 + EMA200，跌破前 20 日低点退出。
- V2-B：EMA200 ± ATR20，连续两日确认。
- V3：30/90/180 日正收益个数 /3 为趋势分数，60 日日对数收益波动率年化，以 `score * min(0.5, 0.10 / sigma)` 调仓；北京时间周一 08:00 用上一根收完的 UTC 日 K 线信号，开盘模拟成交。变动 5 个百分点以上才调仓。
- V3-NoTrend：去掉趋势因子，验证趋势贡献。
- 对照：现金、50% BTC + 50% USDT 每月再平衡。
- 研究初始资金 1000 USDT；单边手续费 0.10% + 模拟滑点 0.05%，另输出成本双倍压力测试。
- 保护版：上一根完成日线回撤到 8% 则仓位减半；到 15% 进入永久停机、**下一日开盘**卖出。行情跳空可能使最大回撤超过 15%，不是严格止损保证。
- 诊断版：无上述回撤保护，便于比较策略本身表现。

每一版本产出 `*_equity.csv` 和 `*_trades.csv`，以及 `summary.json`。回测摘要包含数据源 SHA256、Python 版本、交易成本、收益、波动、最大回撤和手续费估算。暂未完成独立样本外验证、旧策略原代码核查、实盘对账；因此即便回测出现正收益也不能自动视为准入成功。

## 当前执行证据（2026-10-14）

- 已编写数据认证与回测程序。
- 临时 Python 测试环境：6 个合成行情单元测试通过。这是**程序逻辑测试**，不是历史市场回测。
- 当前临时环境无法解析 Binance 官方数据站域名；历史 K 线尚未实抓和认证。
- Codex 云端 Runner 通道不可用，不能声称第三轮回测已在云端执行。
- Drive 中 `BTC_V3_第三轮回测_20261014` 已有文件夹但尚无正式结果；请勿将合成测试输出当作历史收益。

**继续推进的实际前置条件：** 用有公网访问权限的执行环境完成 ZIP 下载、认证及回测；核查实际下载日期与仓库提交 SHA；再把新生成文件按独立日期或执行 ID 上传已有 Drive 根项目，避免覆盖旧成果。没有真实数据时不得填写正式收益表。
