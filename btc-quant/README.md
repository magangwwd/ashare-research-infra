# btc-quant · BTC 现货多头研究

本目录与仓库的 `astock-quant/` 并列，独立研究 BTC/USDT 现货策略。仅模拟/研究，不发送真实订单、不使用杠杆或做空。

## 研究边界
- 初始模拟资金：1,000 USDT；允许 USDT 空仓。
- 目标：扣除成本后的长期正收益，同时管理回撤；20% 是风险控制目标，不是保证。
- 统一数据：优先 Binance 官方 BTCUSDT Spot 4h K 线；统一 UTC 日线，保留数据来源与校验记录。
- 执行：收盘确认信号，最早下一根 K 线以可成交价格模拟；基础单边手续费 0.10% + 滑点 0.05%，双倍成本压力测试。
- V3：每周一北京时间 08:00 根据 30/90/180 日收益计算趋势强度 S，结合 60 日日收益波动率设仓位；8% 回撤减风险、15% 永久停机；同时保存关闭停机的诊断曲线。
- 对照：V1、V2-A、V2-B、V3、V3-NoTrend、50/50 月调仓及全现金。
- 历史报告不可直接视为复现；若旧代码/数据/交易日志缺失，明确标记为“重新实现”。不得捏造缺失 K 线或独立样本外表现。

## 当前已提交代码（第一版）
- `data/download.py`：下载 Binance Vision 现货 BTCUSDT 4h 月度 ZIP。
- `data/validate.py` 与 `data/prepare.py`：严格校验 4h K 线并合成 UTC 日线。
- `backtest/run.py`：重建 V1、V2-A、V2-B、V3、V3-NoTrend、现金及 50/50 基准，带双成本和回撤保护。
- `tests/test_research.py`：合成行情单元测试（本地通过 7 项）。
- `docs/RUNBOOK.md`：复现命令、数据路径、研究限制及验收要求。
- `.github/workflows/btc-v3-backtest.yml`：一次推送触发及手动触发，尝试下载真实历史数据、回测并写入已有 Drive 子文件夹；实际 Actions 成功与 Drive 上传尚待日志/文件核验。

## 持久化与安全
GitHub 保存源代码、测试和参数；Google Drive **不新建目录**，沿用现有的 `project/BTC_Quant_System_OKX/` 保存行情原件、清洗数据、日志、净值、研究报告和证据清单；现有文件保持不变，新增研究结果需清晰命名、不可覆盖历史成果。对应文件夹：https://drive.google.com/drive/folders/1f1QpjccD0YvciS7RwaxfRGkuW-o0k_0P 。已编写和提交回测程序、通过本地合成数据测试；**尚未确认 GitHub Actions 真实历史回测成功**。

**重要：**本仓库当前为公开仓库，不得提交 API Key、Token、账户信息、交易密钥或受限/敏感数据。大文件及生成数据不直接提交 Git。

## 下一步验收
1. 核实 GitHub Actions 是否真正启动、7 项测试是否通过、Binance 下载是否成功。
2. 核实 Drive 原有 `BTC_V3_第三轮回测_20261014` 子文件夹新增源数据哈希、交易日志、净值和 summary.json；若缺失，查看 Actions 失败步骤。
3. 审查旧 V1/V2 原始脚本和报告，明确标注重新实现与历史数字的差异。
4. 补独立样本外、多周期稳定性、执行假设与中文结论；未通过不进入模拟盘。
