# stk_mins — A股15分钟行情

## 角色

Task 01 基础行情数据。源接口由 Tushare Python SDK 的股票历史分钟 `pro_bar` 提供，固定请求 `freq=15min`、`asset=E`、`adj=None`。本数据集内部命名为 `stk_mins`。

## 历史范围

- 目标起点：2020-01-01。
- 历史按“证券 × 年”有界请求，从最近年份向前回补。
- 单次响应触及 8000 行安全上限时拒绝写入，要求进一步拆分，避免静默截断。
- 最新交易日按证券逐只请求；首次证券覆盖率达到 95% 后才允许激活 daily_from_minutes。

## Clean Schema

主键：`security_id + trade_time`。

字段：`ts_code / security_id / trade_date / trade_time / freq / open / high / low / close / vol / amount / schema_version`。

`trade_time` 保留 Tushare 返回的中国市场本地交易时间字符串；`trade_date` 从 `trade_time` 显式派生。Raw 和 Clean 均按 `year/month` 分区，ZSTD Parquet。

## 单位与日线聚合

分钟接口：
- `vol`：股；
- `amount`：元。

聚合为现有 daily 合约时：
- open = 第一根 15min open
- high = 日内 max(high)
- low = 日内 min(low)
- close = 最后一根 15min close
- vol = sum(minute vol) / 100，转为“手”
- amount = sum(minute amount) / 1000，转为“千元”

`pre_close/change/pct_chg` 不从分钟线猜测，聚合结果留空；复权继续依赖 `adj_factor`。

## 数据源切换

激活前：Tushare daily 仍是日线正式价格来源；分钟接口权限不足或覆盖不足只记录状态，不破坏现有正式版本。

激活后：
- 激活日及以后 daily 由 15min Clean 聚合；
- 不再使用全市场 Tushare daily 作为未来日线主源；
- `adj_factor`、`daily_basic` 继续独立日更；
- Tushare daily 只做少量 OHLC QA；
- 当日分钟证券覆盖率低于 95% 时阻止新版本发布，不自动退回 daily。

## 保留

- Raw：默认 14 天；
- Clean：长期；
- 历史回补预算独立于日线 Core 快车道。

真实分钟权限和首次生产覆盖必须通过线上 Task 01 后，才能把本数据集标记为运行验收完成。
