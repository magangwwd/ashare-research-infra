---
title: Markov 市场状态过滤器审计与策略方案
created: 2026-06-24
data_snapshot: daily_data latest local date = 2026-06-12
source_attachment: C:/Users/magan/Downloads/Markov_市场状态过滤器_最终落地总结版.md
status: 审计版策略方案
---

# Markov 市场状态过滤器审计与策略方案

## 1. 结论

附件文档的核心判断成立：纯 Markov 三状态转移矩阵不应作为主模型，更适合作为市场风险延续的辅助确认器。

本项目更适合落地为：

```text
主板等权市场状态序列
  -> 市场参与评分 market_score
  -> Markov 弱势/强势延续概率辅助修正
  -> normal / reduce / no_trade
  -> 作为 LightGBM / Kronos / quality H20 个股策略的仓位闸门
```

第一版不建议直接把它接入实盘动作，也不建议直接改 `generate_quality_h20_report.py`。应先做独立模块和 walk-forward 验证，确认它相对 `ret20 > 0` 简单规则有增量后，再接入日报。

## 2. 附件文档审计

### 2.1 正确部分

- 把 Markov 降级为辅助项是正确的。三状态 Markov 在同一训练窗口内最多只能给出 3 行概率，信息颗粒度太粗，不适合单独决定交易。
- 使用主板等权收益而不是上证指数更贴近本仓库的个股选股场景，因为现有策略买的是主板股票池，不是指数。
- `ret20` 用历史分位数贴 Strong / Neutral / Weak 标签，并加 `ret20 > 0` / `< 0` 限制，可以避免弱市中的相对反弹被误判为 Strong。
- 输出 `normal / reduce / no_trade` 三档动作，与本仓库现有研究辅助报告口径兼容。
- 文档要求验证 Markov 是否有增量，且没有增量就删除，这一点非常关键。

### 2.2 需要修正的部分

- 评分表中很多区间是文字描述，例如“28 - 35”“8 - 18”，工程落地前必须改成明确函数，否则每次实现会漂移。
- 宽度分只用当天上涨占比过于嘈杂，应使用 `breadth20` 为主，当天 `breadth` 只做解释字段。
- `market_ret_median` 需要定义 20 日版本。建议先用 `median_nav = cumprod(1 + market_ret_median)` 后计算 `median_ret20`，与 `market_nav.ret20` 保持口径一致。
- Markov 分数不能直接用全样本转移矩阵。每个信号日只能使用该日以前的状态序列估计矩阵。
- 回测必须把信号向后移一日：`t` 日收盘生成的 action，只能作用于 `t+1` 日收益。
- 文档中的“市场评分越高，未来收益越好”不能默认成立。样本初测显示低分组后续 20 日收益反而可能较高，说明它更像风险闸门，不是收益排序因子。
- 不建议第一版依赖 `TurnoverRate`，因为当前 `daily_data` 的 2026-05 部分换手率大面积缺失。

## 3. daily_data 样本审计

本次只读本地 `daily_data/*.csv`，没有修改行情数据。

| 项目 | 结果 |
|---|---:|
| CSV 文件数 | 2774 |
| 主板代码文件数 | 2774 |
| 非主板代码文件数 | 0 |
| 样本最早日期 | 2020-01-02 |
| 样本最新日期 | 2026-06-12 |
| 等权收益序列起点 | 2020-01-03 |
| 可贴状态起点 | 2021-02-22 |
| 等权市场交易日数 | 1559 |
| 最新 60 日有效股票数中位数 | 2774 |
| 动态覆盖率低于 80% 的天数 | 0 |

### 3.1 字段完整性

所有文件都包含标准列：

```text
Date, Close, High, Low, Open, Volume, TurnoverRate
```

但 `TurnoverRate` 在 2026-05 的多个交易日大面积缺失，最严重日期包括：

| 日期 | 缺失文件数 |
|---|---:|
| 2026-05-21 | 2772 |
| 2026-05-22 | 2772 |
| 2026-05-25 | 2772 |
| 2026-05-26 | 2772 |
| 2026-05-27 | 2772 |
| 2026-05-13 | 2771 |
| 2026-05-18 | 2771 |
| 2026-05-19 | 2771 |
| 2026-05-20 | 2771 |
| 2026-05-28 | 2771 |

第一版市场状态过滤器应只依赖 `Close` 计算市场收益、宽度和状态，不依赖换手率。若后续要加入流动性分，优先使用 `market_meta/daily_basic.csv` 或先执行换手率回填流程。

### 3.2 OHLC 质量问题

样本里存在实质 OHLC 异常，典型形式是 `High < Open` 或 `Close > High`。异常主要集中在：

| 日期 | 实质异常股票数 |
|---|---:|
| 2024-03-29 | 901 |
| 2026-04-02 | 827 |
| 2020-05-29 | 49 |

这不阻止第一版用 `Close.pct_change()` 计算市场过滤器，但会影响以下扩展：

- ATR20 止损。
- 日内振幅和波动率。
- High/Low 参与的技术指标。
- “跌破均线且放量下跌”这类执行规则。

因此第一版策略应明确：

```text
P0 只用 Close-to-Close 收益。
P1 以后如需 OHLC 技术指标，先加数据质量过滤或修复异常日期。
```

## 4. 样本市场状态结果

截至本地样本最新日期 `2026-06-12`：

| 指标 | 数值 |
|---|---:|
| 有效股票数 | 2774 |
| 动态覆盖率 | 100.00% |
| 当日等权收益 | 1.37% |
| 当日中位数收益 | 1.47% |
| 当日上涨占比 | 75.92% |
| 20 日上涨占比均值 `breadth20` | 39.54% |
| 主板等权 `ret20` | -8.49% |
| 中位数 `median_ret20` | -12.71% |
| 60 日回撤 | -10.95% |
| 过去 252 日 `ret20` P33 | 1.18% |
| 过去 252 日 `ret20` P67 | 4.08% |
| 当前状态 | Weak |

样本解释：

```text
最新一天出现反弹，但 20 日主板等权赚钱效应仍明显为负，
20 日市场宽度只有约 39.5%，60 日回撤超过 10%，
所以市场状态不应因为单日反弹而从 Weak 直接切成可进攻。
```

## 5. Markov 辅助概率样本

使用默认候选参数：

```text
transition_horizon = 5
transition_sample_step = 5
train_window = 504
Laplace alpha = 1
```

在 `2026-06-12` 的当前状态 `Weak` 下，5 日转移概率为：

| 当前状态 | P(Strong) | P(Neutral) | P(Weak) |
|---|---:|---:|---:|
| Weak | 2.70% | 26.83% | 64.52% |

```text
Markov Edge = P(Strong) - P(Weak) = -51.61%
```

辅助结论：Weak 状态延续概率很高，Markov 应该扣分或触发 `reduce -> no_trade` 的防守修正。

## 6. 第一版市场参与评分

建议保留附件中的 100 分框架，但把模糊区间改成明确函数。

| 模块 | 权重 | 第一版口径 |
|---|---:|---|
| 趋势分 | 35 | `ret20` 与过去 252 日 P33/P67 比较，并强制正负收益过滤 |
| 宽度分 | 25 | `breadth20 * 25` |
| 中位数分 | 15 | `median_ret20` 分段计分 |
| 风险分 | 15 | `drawdown60` 分段计分 |
| Markov 分 | 10 | `P(Strong)-P(Weak)` 分段计分 |

第一版动作：

| market_score | action | exposure | 用法 |
|---:|---|---:|---|
| `>= 70` | normal | 1.0 | 允许正常运行个股策略 |
| `50 - 70` | reduce | 0.5 | 只允许高置信标的，或降低组合仓位 |
| `< 50` | no_trade | 0.0 | 暂停新开仓 |

### 6.1 最新样本打分

| 模块 | 分数 |
|---|---:|
| 趋势分 | 4.00 |
| 宽度分 | 9.88 |
| 中位数分 | 2.00 |
| 风险分 | 0.00 |
| Markov 分 | 0.00 |
| 总分 | 15.88 |
| 动作 | no_trade |

样本建议：

```text
截至本地样本 2026-06-12，这套市场过滤器会阻止新增进攻仓位。
它可以解释为：短线有反弹，但中期赚钱效应、宽度、回撤和 Weak 延续概率都不支持正常交易。
```

## 7. 样本回测初筛

以下只是用当前评分雏形在主板等权收益代理上做的快速初筛，不是最终策略回测。

| 策略 | CAGR | 最大回撤 | Sharpe | Calmar | 平均仓位 |
|---|---:|---:|---:|---:|---:|
| Buy and Hold 等权代理 | 12.87% | -28.20% | 0.68 | 0.46 | 99.94% |
| `ret20 > 0` 简单过滤 | 11.42% | -18.41% | 0.84 | 0.62 | 58.63% |
| 状态直接映射 | 6.78% | -16.26% | 0.64 | 0.42 | 41.60% |
| 市场评分 + Markov | 7.27% | -13.14% | 0.75 | 0.55 | 33.87% |

初筛结论：

- 市场评分 + Markov 的最大回撤最低，但收益也明显下降，平均仓位偏低。
- `ret20 > 0` 简单过滤当前更均衡，CAGR 和 Sharpe 都更好。
- Markov 方案第一版必须降低过度防守风险，或者只把 Markov 作为 `reduce/no_trade` 的二次确认，而不是常态扣分。
- 如果后续严谨 walk-forward 仍不能优于 `ret20 > 0`，就不要保留 Markov。

## 8. 分组有效性检查

按市场评分分桶后，未来 20 日等权收益均值初筛如下：

| 分数桶 | 样本天数 | 未来 20 日均值 | 未来 20 日胜率 |
|---|---:|---:|---:|
| 0-35 | 416 | 2.19% | 61.40% |
| 35-50 | 101 | 1.03% | 53.47% |
| 50-65 | 319 | 0.30% | 55.06% |
| 65-80 | 226 | 0.66% | 58.85% |
| 80-100 | 165 | 0.77% | 59.39% |

这个结果不满足“高分 > 低分”的单调性。原因可能是 A 股主板在极弱分数后有均值回归反弹，也可能是评分规则过于防守。

落地含义：

```text
市场评分不能当作收益预测因子。
它应定位为回撤控制和新开仓闸门。
验证标准应优先看最大回撤、弱市亏损、Calmar、空仓比例和收益损失，而不是只看未来均值排序。
```

## 9. 推荐落地架构

新增独立模块：

```text
markov_market_state/
  __init__.py
  __main__.py
  config.py
  data.py
  features.py
  markov.py
  scoring.py
  backtest.py
  report.py
  output/
```

不要直接复用 `kronos_return_top3/regime_gate.py` 的动作名，因为它服务于 Kronos 精排路径，输出是 `no_trade / fast_risk / mini_only`。但可以借鉴它的思想：趋势、宽度、风险、覆盖率、硬否决。

第一版公共接口：

```python
build_market_series(...)
label_market_states(...)
estimate_transition_matrix(...)
evaluate_market_score(...)
evaluate_latest_signal(...)
run_market_filter_backtest(...)
```

## 10. 输出设计

第一版输出：

```text
markov_market_state/output/<prefix>_universe_audit.csv
markov_market_state/output/<prefix>_market_daily.csv
markov_market_state/output/<prefix>_market_score.csv
markov_market_state/output/<prefix>_transition_matrix.csv
markov_market_state/output/<prefix>_latest_signal.csv
markov_market_state/output/<prefix>_backtest_summary.csv
markov_market_state/output/<prefix>_score_bucket_test.csv
markov_market_state/output/<prefix>_report.md
```

### 10.1 latest_signal.csv

```text
date
state
market_score
trend_score
breadth_score
median_score
risk_score
markov_score
p_strong
p_neutral
p_weak
markov_edge
action
exposure
comment
```

### 10.2 market_daily.csv

```text
date
market_ret_equal
market_ret_median
breadth
breadth20
market_nav
median_nav
ret20
median_ret20
drawdown60
p33
p67
state
n_effective
eligible_count
coverage
```

## 11. 回测设计

P0 必须对比：

| 组别 | 策略 |
|---|---|
| A | Buy and Hold 主板等权代理 |
| B | `ret20 > 0` 简单过滤 |
| C | 状态直接映射：Strong=1.0 / Neutral=0.5 / Weak=0 |
| D | 市场评分，不含 Markov |
| E | 市场评分 + Markov |

所有策略都必须：

- 使用 `t-1` 的信号作用于 `t` 的收益。
- 扣除 `5 bps * abs(exposure_change)` 成本。
- 输出平均仓位和动作分布。
- 标注等权收益只是“赚钱效应代理”，不是可交易指数。

## 12. Markov 保留标准

Markov 只有在相对“不含 Markov 的市场评分”满足至少 3 条时才保留：

- 最大回撤额外下降 5% 以上。
- Calmar 提高 10% 以上。
- Weak 状态下后续亏损减少。
- Neutral 偏弱时能减少误开仓。
- 参数从 `edge=0.10` 到 `0.30` 变化时仍稳定。
- 扣除 5/10/20 bps 成本后仍有效。

出现以下任一情况，应删除 Markov 分，只保留市场评分：

- 不如 `ret20 > 0` 简单过滤。
- 只降低回撤但收益大幅下降，导致 Calmar 不提升。
- `no_trade` 占比过高，错过大部分反弹。
- 只有 `horizon=1` 有效，`horizon=5/10/20` 失效，说明只是重叠窗口惯性。

## 13. 接入个股策略规则

市场过滤器只决定能否参与和总仓位，不决定买哪只。

| 市场动作 | 个股策略执行 |
|---|---|
| normal | 正常使用 LightGBM / Kronos / quality H20 排名 |
| reduce | 只取更高置信标的，总仓位降至 30%-50% |
| no_trade | 不新开仓；已有仓位按止损、趋势和报告规则处理 |

与现有报告链路的建议接入顺序：

```text
P0 独立生成 latest_signal.csv
P1 在 generate_quality_h20_report.py 中只读取 action/exposure/comment
P2 把 action 写入报告顶部，不改变个股分数
P3 回测确认后，再让 action 影响候选池或仓位建议
```

## 14. 第一版实施优先级

### P0

- 主板代码过滤。
- 只读 `daily_data` 构建主板等权收益。
- 输出 universe audit，记录被纳入/排除原因。
- 计算 `market_ret_equal`、`market_ret_median`、`breadth20`、`ret20`、`median_ret20`、`drawdown60`。
- 无未来函数的 Strong / Neutral / Weak 标签。
- rolling Markov 矩阵和最新 `P(Strong/Neutral/Weak)`。
- 明确市场评分函数。
- 最新信号和简单回测。

### P1

- 阈值敏感性：`normal=65/70/75`，`no_trade=45/50/55`。
- Markov edge 敏感性：`0.10/0.20/0.30`。
- 成本敏感性：`5/10/20 bps`。
- `horizon=1/5/10/20` 对比。
- score bucket test。
- 与 `kronos_return_top3.regime_gate` 输出做同日对照。

### P2

- 接入报告。
- 加真实指数对照。
- 加行业宽度。
- 加 HMM 作为研究项，不进入第一版决策。

## 15. 验收标准

第一版可以进入报告链路的最低标准：

```text
最大回撤相对 Buy and Hold 下降 >= 20%
Calmar 相对 Buy and Hold 提高 >= 20%
Sharpe 不低于 ret20 > 0 简单过滤的 90%
CAGR 不低于 Buy and Hold 的 70%
平均仓位在 35% - 75% 之间
no_trade 占比不超过 45%，除非弱市年份单独验证有效
相对 ret20 > 0 至少有一个核心指标明显更好
```

如果做不到，就把 Markov 市场过滤器保留为研究报告，不接入主流程。

## 16. 当前样本下的策略建议

基于本地 `daily_data` 截至 `2026-06-12` 的样本：

```text
market_score = 15.88
state = Weak
P(Weak, 5d) = 64.52%
action = no_trade
exposure = 0%
```

这不是投资建议，只是策略框架按本地样本给出的研究信号。由于本地 `daily_data` 最新日期停在 `2026-06-12`，如果要生成今天或最近交易日的报告，必须先完成日线更新与换手率修复，再重新计算。

