# adj_factor

来源：Tushare；Clean Schema 1.0.0。

主键：security_id + trade_date。分区：`year=YYYY/month=MM`，ZSTD Parquet。

更新：每日，行情按类型近期回补；基础信息刷新。重复保留最后一条，日期转 Arrow Date，未知源字段仅进入 Raw。

注意：只读验收后的 `data/clean/adj_factor`；历史正在分批推进时须检查 Manifest 的实际范围。金额/股本/成交量等单位遵循 Tushare 接口原口径，Task 01 不计算因子或复权价格。

| source_field | clean_field | dtype | description |
| --- | --- | --- | --- |
| ts_code | ts_code | string | Tushare 证券标识 |
| security_id | security_id | string | 基于证券标识的稳定 UUID5 |
| trade_date | trade_date | date | 交易日期 |
| adj_factor | adj_factor | float64 | Tushare 原字段 adj_factor; 口径及单位遵循接口文档 |
| schema_version | schema_version | string | Clean Schema 版本 |
