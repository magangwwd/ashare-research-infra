# 艾艾精工（603580.SH）单股价格行为与强化学习实验

**2026-11-03 状态：研究代码第一版；尚未完成PPO训练，禁止真实交易。**

## 研究问题
强化学习能否学习“快速上涨—跳空—反转—波动聚集”等可重复的行为特征，并在样本外改善净收益或风险，而不只是记住过去行情？

## 第一阶段：可复核的单笔历史价格路径
运行：
```sh
python astock-quant/three_stock_rl/paper_trade.py --signal-date 2026-07-17 --signal-close 44.92 --entry-date 2026-07-20 --entry-open 49.41 --exit-date 2026-07-21 --exit-open 36.39 --fee-rate 0.0003 --slippage-rate 0.001
python -m unittest discover -s astock-quant/three_stock_rl/tests -v
```
**注意**：传入价格是待外部核验的历史记录，输出仅为理论开盘价参考收益。不表示真实开盘一定成交，更不是强化学习训练结果。模型不得使用未来行情决定历史买卖操作。

## 后续强制前置检查
1. 唯一正式历史行情为已验证的 Tushare Gold `down_data/data/gold/main_board` 及其 manifest，不得用旧 `daily_data` 冒充。
2. 对复权、历史ST标识、涨跌停规则、停牌、成交时点和T+1单独检查。
3. 训练集、验证集、测试集按时间隔离；至少五个随机种子。
4. A：固定趋势；B：固定趋势+波动过滤；C：PPO仓位决策。相同数据、成本、执行假设，报告收益、回撤、暴露、交易频率和置信区间。
5. 股票画像必须用多个独立市场区间复核。禁止把一个案例当成可重复盈利规律。

不更改 `stock_state_strategy` 冻结V1、Task01及任何真实交易入口。
