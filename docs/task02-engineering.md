# Task 02：回测引擎工程阶段

当前阶段只验证研究与回测基础设施，不用现有短历史判断策略有效性。

## 数据入口

Task 02 只读 Task 01 已验收的 `data/current/clean`。不得从 staging、未完成 checkpoint 或 Raw 直接产生策略结论。

## 当前模块

- `research_engine/data.py`：读取正式 Clean，并按主键合并 daily / adj_factor / daily_basic。
- `prices.py`：用当日 adj_factor 构造不依赖样本终点的调整价格。
- `universe.py`：按历史上市/退市日期构造基础主板股票池，不使用“今天仍上市”做历史过滤。
- `factors.py`：工程验证用的后视趋势、波动率、成交量因子。
- `signals.py`：横截面排序与目标权重。
- `backtest.py`：信号日收盘后生成目标，严格到下一全局交易日开盘执行；支持显式佣金、滑点和卖出税参数。
  - 退市：传入 `delistings`（`security_id`、`delist_date`）后，持仓在退市日及之后第一个无开盘价的交易日按最后估值乘以 `delisting_recovery_ratio`（默认 0，即全额核销）清算，交易记录为 `DELIST`；不传则保持原行为（按最后收盘价持有）。
  - 涨跌停：`market` 含原始 `open`、`up_limit`、`down_limit` 列时（可用 `CleanResearchData.market(include_limits=True)` 并入 `stk_limit`），开盘即涨停不可买入、开盘即跌停不可卖出；缺少限价的行视为可交易，`enforce_price_limits=False` 可关闭。
  - 输入校验：目标权重含 NaN/inf 或同一 `(signal_date, security_id)` 重复时直接报错。
- `metrics.py`：收益、波动、Sharpe、最大回撤、换手和成本统计。
- `walkforward.py`：按时间顺序生成 train / validation / test 窗口。
- `readiness.py`：只按三类核心数据“最近连续共同交易日”判断研究成熟度。

## Readiness

| 阶段 | 最近连续核心交易日 | 用途 |
| --- | ---: | --- |
| Engineering | 60 | 只验证工程正确性 |
| Exploratory | 250 | 第一版因子探索 |
| Research | 750 | 正式策略研究与 Walk-forward |
| Robustness | 1250 | 跨阶段稳健性 |
| Stress test | 2000 | 牛熊震荡与风格切换压力测试 |

Readiness 不是盈利承诺，也不是策略有效性证明。

## 工程回测入口

```bash
python -m research_engine.readiness --write
python -m research_engine.run_engineering_demo --factor momentum_20d --top-n 10
```

第二条命令会输出回测指标，但始终带 `engineering_only=true` 和 `strategy_efficacy_claim=false`。在 Research 门槛前，不依据这些指标选择策略、优化参数或宣称因子有效。

## 已冻结的时序约束

1. 因子在交易日 t 只使用 t 及以前信息。
2. t 日收盘后形成信号，最早 t+1 全局交易日开盘成交。
3. 不使用样本最后一天的复权因子回填过去价格。
4. 历史股票池使用每只股票的上市/退市日期，不用当前存续状态造成幸存者偏差。
5. 缺少历史 ST 等状态时明确保留限制，不猜测。
6. 参数比较和 Walk-forward 要等至少 750 个最近连续核心交易日后再进入正式研究。
