# stk_mins — 沪深A股主板15分钟行情

## 数据来源与范围

- 行情：Tushare 专用股票分钟接口 `stk_mins`，`freq=15min`，按证券逐一请求。
- 名单：Tushare `stock_basic` 查询 L/D/P 股票，使用返回的 `market`、`exchange`、上市及退市日期识别当时可交易股票。
- **只收沪市 SSE、深市 SZSE，且 `market=主板` 的 A 股**，创业板、科创板、北交所不发分钟请求；当日明确停牌股票不计入分钟覆盖率分母。
- 不按证券代码前缀猜板块；未知板块不擅自归入主板。分母只包含符合本范围的当日目标证券。
- 保存 `metadata/manifest/intraday_universe.json`：采集日、名单来源、沪深数量、逐个 `ts_code` 与名单 SHA256；`official_exchange_reconciled=false` 明确表示尚未和沪深交易所独立逐只比对，不可用该记录冒充官方核验。
- 如有历史退市股票，其历史分钟请求必须落在上市日至退市日前的实际存续区间。

## 历史范围

- 起点：2020-01-01；从第一个有效交易日开始按“证券 × 年”有界补齐，不声称现已完成。
- 单次响应达 8,000 行会被拒收并标为待拆分，避免把静默截断当完整历史。
- 最新交易日逐个证券下载；目标范围证券覆盖率 95% 才能标为分钟源健康。
- 日常/历史请求均按配置预算分批执行，失败保留 Gap、重试，不编造数据。

## Clean Schema 与单位

主键：`security_id + trade_time`，字段：
`ts_code / security_id / trade_date / trade_time / freq / open / high / low / close / vol / amount / schema_version`。

Raw、Clean 均按 `year/month` 存为 ZSTD Parquet；Clean 长期保存，Raw 默认保留14天。分钟 `vol` 单位为股、`amount` 单位为元。

## 与原有全市场日线的隔离

**分钟范围缩至主板后，不得直接替换全市场 `daily` 日线。** 继续通过 Tushare 正式 `daily` 获取原全市场日线，原 `adj_factor` 和 `daily_basic` 日更不变。分钟状态 `activated=false`，`daily_source=tushare_daily`。

分钟行情自身的 95% 覆盖率仅用于分钟数据健康状态和告警，不替代全市场日线的独立 Critical 质量验收；分钟缺失须保留明确告警。

本文件记录采集配置，不等于真实 Google Drive 发布成功；实际需核对 Actions、完整分钟名单与远端数据校验。
