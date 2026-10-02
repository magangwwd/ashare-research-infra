# daily

来源：Tushare；Clean Schema 1.0.0。

主键：security_id + trade_date。分区：`year=YYYY/month=MM`，ZSTD Parquet。

更新：每日，行情按类型近期回补；基础信息刷新。重复保留最后一条，日期转 Arrow Date，未知源字段仅进入 Raw。

注意：只读验收后的 `data/clean/daily`；历史正在分批推进时须检查 Manifest 的实际范围。金额/股本/成交量等单位遵循 Tushare 接口原口径，Task 01 不计算因子或复权价格。

| source_field | clean_field | dtype | description |
| --- | --- | --- | --- |
| ts_code | ts_code | string | Tushare 证券标识 |
| security_id | security_id | string | 基于证券标识的稳定 UUID5 |
| trade_date | trade_date | date | 交易日期 |
| open | open | float64 | Tushare 原字段 open; 口径及单位遵循接口文档 |
| high | high | float64 | Tushare 原字段 high; 口径及单位遵循接口文档 |
| low | low | float64 | Tushare 原字段 low; 口径及单位遵循接口文档 |
| close | close | float64 | Tushare 原字段 close; 口径及单位遵循接口文档 |
| pre_close | pre_close | float64 | Tushare 原字段 pre_close; 口径及单位遵循接口文档 |
| change | change | float64 | Tushare 原字段 change; 口径及单位遵循接口文档 |
| pct_chg | pct_chg | float64 | Tushare 原字段 pct_chg; 口径及单位遵循接口文档 |
| vol | vol | float64 | Tushare 原字段 vol; 口径及单位遵循接口文档 |
| amount | amount | float64 | Tushare 原字段 amount; 口径及单位遵循接口文档 |
| schema_version | schema_version | string | Clean Schema 版本 |
