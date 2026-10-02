# daily_basic

来源：Tushare；Clean Schema 1.0.0。

主键：security_id + trade_date。分区：`year=YYYY/month=MM`，ZSTD Parquet。

更新：每日，行情按类型近期回补；基础信息刷新。重复保留最后一条，日期转 Arrow Date，未知源字段仅进入 Raw。

注意：只读验收后的 `data/clean/daily_basic`；历史正在分批推进时须检查 Manifest 的实际范围。金额/股本/成交量等单位遵循 Tushare 接口原口径，Task 01 不计算因子或复权价格。

| source_field | clean_field | dtype | description |
| --- | --- | --- | --- |
| ts_code | ts_code | string | Tushare 证券标识 |
| security_id | security_id | string | 基于证券标识的稳定 UUID5 |
| trade_date | trade_date | date | 交易日期 |
| close | close | float64 | Tushare 原字段 close; 口径及单位遵循接口文档 |
| turnover_rate | turnover_rate | float64 | Tushare 原字段 turnover_rate; 口径及单位遵循接口文档 |
| turnover_rate_f | turnover_rate_f | float64 | Tushare 原字段 turnover_rate_f; 口径及单位遵循接口文档 |
| volume_ratio | volume_ratio | float64 | Tushare 原字段 volume_ratio; 口径及单位遵循接口文档 |
| pe | pe | float64 | Tushare 原字段 pe; 口径及单位遵循接口文档 |
| pe_ttm | pe_ttm | float64 | Tushare 原字段 pe_ttm; 口径及单位遵循接口文档 |
| pb | pb | float64 | Tushare 原字段 pb; 口径及单位遵循接口文档 |
| ps | ps | float64 | Tushare 原字段 ps; 口径及单位遵循接口文档 |
| ps_ttm | ps_ttm | float64 | Tushare 原字段 ps_ttm; 口径及单位遵循接口文档 |
| dv_ratio | dv_ratio | float64 | Tushare 原字段 dv_ratio; 口径及单位遵循接口文档 |
| dv_ttm | dv_ttm | float64 | Tushare 原字段 dv_ttm; 口径及单位遵循接口文档 |
| total_share | total_share | float64 | Tushare 原字段 total_share; 口径及单位遵循接口文档 |
| float_share | float_share | float64 | Tushare 原字段 float_share; 口径及单位遵循接口文档 |
| free_share | free_share | float64 | Tushare 原字段 free_share; 口径及单位遵循接口文档 |
| total_mv | total_mv | float64 | Tushare 原字段 total_mv; 口径及单位遵循接口文档 |
| circ_mv | circ_mv | float64 | Tushare 原字段 circ_mv; 口径及单位遵循接口文档 |
| schema_version | schema_version | string | Clean Schema 版本 |
