---
title: Markov 市场状态过滤策略实现方案
created: 2026-06-24
revised: 2026-06-24
source_attachment: markov_strategy/Claude_Code_Markov_Quant_Strategy_Beginner_Guide.md
reviewed_by: Markov_Market_State_Filter_Strategy_Final_Reviewed.md（第一轮审核）+ 第二轮审计意见
status: 待实现方案（两轮审核通过，可进入第一版工程实现）
---

# Markov 市场状态过滤策略实现方案

## Summary

- 新增独立模块 `markov_market_state`，实现三状态 Markov 市场状态框架，不改现有 LightGBM / Kronos / 质量报告主线。
- **策略定位**：A 股主板 long-only 市场风险过滤器，不是独立选股或涨跌预测模型。用途是判断当前市场是否适合正常交易、降低仓位或暂停交易。
- 第一版只读取本地 `daily_data/` 数据，不触发任何外部数据下载。
- HMM 第一版只预留结构，不新增 `hmmlearn` 依赖。

---

## Key Changes

### 数据输入

- 默认宇宙 = `daily_data/*.csv` 全部文件，但必须做**代码级主板过滤**：
  ```text
  沪市主板：600xxx / 601xxx / 603xxx / 605xxx
  深市主板：000xxx / 001xxx / 002xxx / 003xxx
  排除：300xxx 创业板、688xxx 科创板、8xxxxx / 4xxxxx / 9xxxxx 北交所
  ```
  不能只依赖文件夹内容，必须在代码中按股票代码前缀过滤，防止 `daily_data/` 后续混入非主板数据污染市场收益序列。
- 可选 `--stock-list filtered_stocks.csv`：指定后只使用名单内股票计算市场收益。
- 复用 `TechnicalPatternScanner.utils.load_all`，不下载外部数据。

### 市场收益构建

- 每日个股收益 = `close_i(t) / close_i(t-1) - 1`；如有复权价字段则优先使用复权价。
- **极端收益截尾（winsorize）**：主板股票单日收益超出 ±10% 的，截尾至 ±10%，防止涨跌停极端值过度影响等权均值。
- 每日市场收益（主信号）= 全部有效个股截尾后日收益**等权平均**（`market_ret_equal`）。
- 每日市场收益（辅助）= 全部有效个股截尾后日收益**中位数**（`market_ret_median`）。如 `market_ret_equal > 0` 但 `market_ret_median < 0`，说明均值被少数强势股拉高，市场赚钱效应存疑。
- 每日市场宽度 = 当天上涨股票数 / 当天 eligible 股票数（`breadth`）。
- 市场净值 = `cumprod(1 + market_ret_equal)`。
- **定名说明**：本序列称为"主板等权赚钱效应收益"，不等同于上证指数或沪深 300，衡量的是 A 股主板普通个股的平均赚钱效应，不是大盘指数趋势。
- **免责说明**：等权市场收益不是真实可交易指数，回测报告应描述为"基于主板等权赚钱效应收益序列进行风险过滤测试"，不得写"跑赢市场指数"。建议同时输出上证指数 / 沪深 300 / 中证全指作为参考对照。

### 覆盖率过滤（已修正分母定义）

```text
coverage(t) = 当天有有效收益的股票数 / 当天应当可交易的股票数
```

"当天应当可交易的股票数"定义为：

```text
eligible_stock(t) = 最近 60 日内至少有 20 个有效交易日收益的股票
```

**不得用当前全部股票总数作为历史日期的分母**（早期年份很多股票尚未上市，会导致历史覆盖率被严重低估）。

默认规则：`coverage(t) < 80%` 的交易日视为无效交易日，不参与市场收益计算。

### 20 日市场收益与状态标签

- `ret20(t) = market_nav.pct_change(20)`，即当日净值相对 20 个交易日前净值的单点比较，非滚动累加。
- 状态标签采用**百分位自适应阈值 + 正负收益过滤**：
  - `P33(t)` / `P67(t)` = 过去 252 个交易日（`t-252` 到 `t-1`）的 `ret20` 分布分位数，当天 `ret20(t)` 不纳入分位数计算，避免未来函数。
  - `ret20(t) > P67(t)` 且 `ret20(t) > 0` → **Strong**
  - `ret20(t) < P33(t)` 且 `ret20(t) < 0` → **Weak**
  - 其他情况 → **Neutral**
- 历史数据预热要求：`20 + 252 = 272` 个交易日；不足 272 日的序列起始段无有效状态标签。

### Markov 转移矩阵（已拆分参数）

将原 `--transition-sample-interval` 拆分为两个独立参数：

| 参数 | 说明 |
|------|------|
| `--transition-horizon H` | 转移对的预测周期：`(t, t+H)` |
| `--transition-sample-step S` | 采样步长：每隔 S 个交易日取一个样本点 |

第一版**同时输出两组**：

```text
horizon=1, sample_step=1  → 次日状态概率（辅助输出，标注：状态高自相关，不代表真实预测能力）
horizon=5, sample_step=5  → 未来5日状态概率（非重叠采样，默认交易动作来源）
```

**注意**：若 `horizon=1, sample_step=1` 效果明显而非重叠版本失效，说明策略主要吃的是 20 日窗口重叠带来的状态惯性，不代表真实预测能力。

其他矩阵规则：

- 训练窗口：默认用最近 `504` 个已知交易日的有效样本点估计转移矩阵。
- 只使用 `t+H <= signal_date` 的历史转移，避免未来函数。
- 对 3x3 计数矩阵做 Laplace smoothing `alpha=1`，保证每行概率可用且和为 1。

### 状态稳健性测试（新增）

必须同时测试三种状态序列构建方式：

```text
方案A：ret20 每日滚动标签（当前方案）
方案B：每5日采样一次标签
方案C：每20日非重叠标签
```

如果只有方案 A 有效而方案 B / C 失效，说明策略主要依赖重叠窗口惯性，不具备真实预测能力。

### 交易动作

- `signal = P(Strong) - P(Weak)`（基于默认 `horizon=5` 信号）。
- `signal >= 0.20` → `normal`，回测敞口 `1.0`。
- `-0.20 < signal < 0.20` → `reduce`，回测敞口 `0.5`。
- `signal <= -0.20` → `no_trade`，回测敞口 `0.0`。
- **信号执行时点**：`exposure(t)` 由 `t-1` 收盘后的信号决定，不允许使用当天收盘后才知道的状态参与当天收益计算。
- 回测成本默认 `5 bps × abs(exposure_change)`，基于组合净值计算。

### 回测基准（扩展为五组对比）

| 组别 | 策略 | 优先级 |
|------|------|--------|
| A | **Buy and Hold**：始终满仓等权组合，exposure=1.0 | P0 |
| B | **当前状态直接映射**：Strong=1.0 / Neutral=0.35 / Weak=0.1 | P0 |
| C | **ret20 > 0 动量过滤**：ret20>0 满仓，否则空仓 | P1 |
| D | **均线过滤**：价格在 120 日均线以上满仓，否则空仓 | P2 |
| E | **Markov 概率过滤**（本策略）：signal = P(Strong) - P(Weak) | P0 |

如果策略 E 不能明显优于策略 B 和 C，说明 Markov 矩阵没有增加显著价值。

### 敏感性测试（P1）

必须测试以下参数组合：

```text
--threshold-grid 0.10,0.15,0.20,0.25,0.30
--cost-bps-grid 5,10,20
--interval-grid 1,5,10,20
--hold-days-grid 5,10,20
```

如果策略只在单一参数值有效而其他值失效，不能认为策略足够稳健。

### 回测评价指标

walk-forward summary 至少输出：

```text
CAGR 年化收益
Max Drawdown 最大回撤
Sharpe Ratio 夏普比率
Calmar Ratio 卡玛比率
Annual Volatility 年化波动率
Average Exposure 平均仓位
Turnover Count 换仓次数
Total Cost 总交易成本
Cost / Return 成本收益占比
normal 占比 / reduce 占比 / no_trade 占比
相对 Buy-and-Hold 的超额收益
```

动作分布检查：

```text
normal 占比过高 → 过滤器基本没有作用
no_trade 占比过高 → 策略过度保守
reduce 占比过高 → 信号区分度不足
```

### CLI 设计

```bash
# 基础命令
python -m markov_market_state --signal-date latest

# walk-forward 回测
python -m markov_market_state --walk-forward --start-date 2023-01-01 --hold-days 20

# 快速验证
python -m markov_market_state --sample-size 30 --output-prefix smoke
python -m markov_market_state --walk-forward --sample-size 30 --start-date 2025-01-01 --hold-days 20 --output-prefix smoke_wf
```

主要参数：

```text
--lookback 20
--percentile-low 33
--percentile-high 67
--percentile-window 252
--train-window 504
--transition-horizon 5          预测周期，第一版默认5日
--transition-sample-step 5      采样步长
--forecast-days 1               额外输出的次日预测（辅助）
--coverage-threshold 0.8
--cost-bps 5
--normal-threshold 0.20
--no-trade-threshold -0.20
--stock-list                    可选，指定股票名单 CSV
--sample-size N                 快速验证用，随机抽取 N 只
--output-prefix
--quiet
```

敏感性测试参数（P1）：

```text
--threshold-grid 0.10,0.15,0.20,0.25,0.30
--cost-bps-grid 5,10,20
--interval-grid 1,5,10,20
--hold-days-grid 5,10,20
```

### 输出文件

```text
markov_market_state/output/<prefix>_universe_audit.csv           股票池审计：纳入/排除原因/数据起止/有效交易日数
markov_market_state/output/<prefix>_states.csv              含 market_ret_equal / market_ret_median / breadth / breadth_20d / state / ret20 列
markov_market_state/output/<prefix>_transition_matrix.csv
markov_market_state/output/<prefix>_latest.csv
markov_market_state/output/<prefix>_walk_forward.csv
markov_market_state/output/<prefix>_sensitivity_threshold.csv    P1
markov_market_state/output/<prefix>_sensitivity_cost.csv         P1
markov_market_state/output/<prefix>_sensitivity_interval.csv     P1
markov_market_state/output/<prefix>_action_distribution.csv      P1
markov_market_state/output/markov_market_state_<YYYYMMDD>_<prefix>.md
```

---

## Public Interfaces

新增 `markov_market_state.core`：

```python
build_market_series(...)           # 含代码级主板过滤 + 动态 eligible universe 覆盖率
label_market_states(...)
estimate_transition_matrix(...)    # 接受 transition_horizon / transition_sample_step 两个参数
forecast_state_probabilities(...)
evaluate_latest_signal(...)
run_walk_forward_backtest(...)
```

新增轻量 dataclass：

```python
MarkovConfig      # 含 transition_horizon, transition_sample_step 字段
LatestSignal
BacktestSummary
```

不接入 `generate_quality_h20_report.py`；后续若验证有效，再把 `<prefix>_latest.csv` 作为主策略或 Kronos regime gate 的辅助输入。

---

## Test Plan

### 单元测试（`tests/test_markov_market_state.py`）

必须覆盖以下 16 项：

```text
1.  ret20 计算正确（pct_change(20) 单点比较，非累加）。
2.  P33 / P67 不包含当天数据（只使用 t-252 到 t-1）。
3.  ret20 > P67 且 ret20 > 0 正确标记 Strong。
4.  ret20 < P33 且 ret20 < 0 正确标记 Weak。
5.  其他情况正确标记 Neutral。
6.  coverage 使用动态 eligible universe，不使用固定总股票数。
7.  转移矩阵每行概率和为 1。
8.  稀疏状态下 Laplace smoothing 后仍输出有效概率。
9.  多步预测等于矩阵幂结果。
10. walk-forward 训练窗口不使用测试期数据。
11. exposure(t) 使用 t-1 收盘后的信号（信号执行时点正确）。
12. 动作阈值正确映射 normal / reduce / no_trade。
13. 交易成本按 exposure_change 正确扣除，基于组合净值。
14. transition_horizon=1 和 transition_horizon=5 输出不同转移矩阵。
15. 代码级主板过滤正确排除创业板 / 科创板 / 北交所代码。
16. 早期年份覆盖率计算不因未上市股票导致低估（动态 eligible universe）。
17. 极端收益截尾：单日收益超 ±10% 的个股被正确截尾至边界值，不影响其他股票。
18. market_ret_median 与 market_ret_equal 方向背离时能被正确检出（median < 0 而 equal > 0 的场景）。
19. breadth（上涨家数占比）计算正确：分母为当天 eligible 股票数，不是全局总数。
```

### 冒烟测试

```bash
python -m markov_market_state --help
python -m markov_market_state --sample-size 30 --output-prefix smoke
python -m markov_market_state --walk-forward --sample-size 30 --start-date 2025-01-01 --hold-days 20 --output-prefix smoke_wf
python -m unittest tests.test_markov_market_state
```

---

## 实施优先级

### P0：必须实现（最小可行第一版）

```text
代码级主板过滤
动态 eligible universe 覆盖率（修正分母）
全市场等权收益 + market_nav
ret20 计算
百分位自适应状态标签（无未来函数）
Markov 转移矩阵（transition_horizon / transition_sample_step 两参数）
次日和5日双输出状态概率
signal = P(Strong) - P(Weak)
normal / reduce / no_trade 动作输出
信号执行时点：exposure(t) 由 t-1 收盘后信号决定
walk-forward 回测（三组核心基准：A / B / E）
universe_audit.csv 输出
```

### P1：强烈建议同期实现

```text
阈值敏感性测试（--threshold-grid）
成本敏感性测试（--cost-bps-grid）
采样间隔敏感性测试（--interval-grid）
持有期敏感性测试（--hold-days-grid）
状态稳健性测试（方案A/B/C 三种序列对比）
动作分布统计（normal / reduce / no_trade 占比）
四个敏感性输出 CSV
回测基准C：ret20 > 0 动量过滤
市场宽度指标（breadth = 上涨家数占比）及20日滚动均值（breadth_20d）——与 market_ret_equal 同步输出至 states.csv，用于 Strong 信号可靠性辅助验证
中位数收益（market_ret_median）——与 market_ret_equal 同步输出，若二者方向持续背离则在报告中标注
```

### P2：后续增强

```text
above_ma20_ratio（价格在20日均线以上的股票占比）
回测基准D：均线过滤
真实指数对照（上证指数 / 沪深300 / 中证全指）
行业状态扩展
HMM 隐状态模型
接入主策略报告 / LightGBM / Kronos regime gate
```

---

## Assumptions

- 策略宇宙 = A 股主板（SH 600xxx–605xxx + SZ 000xxx–003xxx），通过代码级过滤确保，不依赖 `daily_data/` 文件夹内容。
- 策略只做 A 股 long-only 风险过滤，不做卖空。
- 第一版只读取本地数据，不触发任何网络调用。
- 等权市场收益仅用于状态判断和风险过滤，不作为可交易指数，不得直接与真实指数比较超额收益。
- `transition_horizon=5, transition_sample_step=5` 为第一版默认（非重叠采样，减少 20 日窗口惯性）；次日预测（horizon=1）同时输出但仅作辅助参考。
- 策略成功标准：是否降低最大回撤、改善收益回撤比、在弱势环境减少亏损、在强势环境不过度错过上涨、在不同参数和成本假设下保持稳定。

---

## 默认参数汇总

```text
lookback                  = 20
percentile_window         = 252
percentile_low            = 33
percentile_high           = 67
train_window              = 504
transition_horizon        = 5
transition_sample_step    = 5
forecast_days             = 1       （辅助次日预测）
coverage_threshold        = 0.8
eligible_lookback         = 60      （eligible universe 判定窗口）
eligible_min_days         = 20      （eligible universe 最低有效交易日）
cost_bps                  = 5
normal_threshold          = 0.20
no_trade_threshold        = -0.20
```
